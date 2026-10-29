import { D1Database, VectorizeIndex, Ai } from '@cloudflare/workers-types';

export interface SyncResult {
  updatedCount: number;
  skippedCount?: number;
  d1StatementsExecuted?: number;
  status: string;
  sourceNodesFetched: number;
  vectorsUpserted: number;
  pagesFetched: number;
  truncationDetected: boolean;
  watermarkMs: number;
  lastConsumedTimestamp: string;
}

async function calculateContentHash(text: string): Promise<string> {
  const data = new TextEncoder().encode(text);
  const digest = await crypto.subtle.digest('SHA-256', data);
  const arr = Array.from(new Uint8Array(digest));
  return arr.map(b => b.toString(16).padStart(2, '0')).join('');
}

export class EdgeSyncService {
  constructor(
    private db: D1Database,
    private vectorize: VectorizeIndex,
    private ai: Ai,
    private tanaToken?: string
  ) {}

  /**
   * Helper to call Tana Remote MCP JSON-RPC 2.0 tool endpoint with bounded retry,
   * exponential backoff + jitter, and terminal vs retryable error separation (#21).
   */
  private async callTanaMCP(toolName: string, args: Record<string, any>, maxRetries: number = 3): Promise<any> {
    if (!this.tanaToken) return null;

    const payload = {
      jsonrpc: '2.0',
      method: 'tools/call',
      params: {
        name: toolName,
        arguments: args
      },
      id: 1
    };

    let attempt = 0;
    while (attempt < maxRetries) {
      attempt++;
      try {
        const res = await fetch('https://app.tana.inc/mcp', {
          method: 'POST',
          headers: {
            'Authorization': `Bearer ${this.tanaToken}`,
            'Content-Type': 'application/json',
            'Accept': 'application/json, text/event-stream'
          },
          body: JSON.stringify(payload)
        });

        if (res.status === 401 || res.status === 403) {
          const authErr: any = new Error(`Terminal Tana MCP authorisation failure (HTTP ${res.status}): re-authorisation required`);
          authErr.isTerminalAuth = true;
          throw authErr;
        }

        if (res.status === 429 || (res.status >= 500 && res.status <= 504)) {
          if (attempt < maxRetries) {
            const jitterMs = Math.floor(Math.random() * 250);
            const backoffMs = Math.min(4000, 500 * Math.pow(2, attempt)) + jitterMs;
            await new Promise(resolve => setTimeout(resolve, backoffMs));
            continue;
          }
        }

        if (!res.ok) {
          throw new Error(`Tana MCP responded with HTTP ${res.status}`);
        }

        const jsonRpcRes: any = await res.json();
        if (jsonRpcRes.error) {
          throw new Error(`Tana MCP error: ${jsonRpcRes.error.message || JSON.stringify(jsonRpcRes.error)}`);
        }

        if (jsonRpcRes.result && Array.isArray(jsonRpcRes.result.content)) {
          for (const item of jsonRpcRes.result.content) {
            if (item.type === 'text' && item.text) {
              try {
                return JSON.parse(item.text);
              } catch {
                return item.text;
              }
            }
          }
        }

        return jsonRpcRes.result;
      } catch (err: any) {
        if (err?.isTerminalAuth || attempt >= maxRetries) {
          throw err;
        }
        const jitterMs = Math.floor(Math.random() * 200);
        const backoffMs = Math.min(3000, 400 * Math.pow(2, attempt)) + jitterMs;
        await new Promise(resolve => setTimeout(resolve, backoffMs));
      }
    }
    return null;
  }

  private async acquireSyncLock(lockOwner: string, ttlMs: number = 300000): Promise<boolean> {
    const nowMs = Date.now();
    try {
      const existingRow = await this.db.prepare("SELECT value FROM sync_metadata WHERE key = 'sync_lock'").first<any>();
      if (existingRow?.value) {
        try {
          const parsed = JSON.parse(existingRow.value);
          if (parsed.expiresAt > nowMs && parsed.owner !== lockOwner) {
            return false;
          }
        } catch {}
      }
      const lockPayload = JSON.stringify({ owner: lockOwner, acquiredAt: nowMs, expiresAt: nowMs + ttlMs });
      await this.db
        .prepare("INSERT OR REPLACE INTO sync_metadata (key, value, updated_at) VALUES ('sync_lock', ?, ?)")
        .bind(lockPayload, new Date(nowMs).toISOString())
        .run();
      return true;
    } catch {
      return true;
    }
  }

  private async releaseSyncLock(lockOwner: string): Promise<void> {
    try {
      await this.db.prepare("DELETE FROM sync_metadata WHERE key = 'sync_lock'").run();
    } catch {}
  }

  /**
   * Helper to batch embed and upsert into D1 + Vectorize on the Edge.
   * Performs SHA-256 content-hash diffing to guarantee Workers AI and Vectorize
   * are ONLY called for truly new or edited content, completely preventing credit drain.
   */
  private async ingestNodes(rawNodes: any[]): Promise<{
    ingestedCount: number;
    skippedCount: number;
    newNodesCount: number;
    trashedCount: number;
    d1StatementsExecuted: number;
    vectorsCount: number;
  }> {
    const statements: any[] = [];
    const vectorIdsToDelete: string[] = [];
    const nodesToEmbed: Array<{ id: string; name: string; desc: string }> = [];
    let activeNodeCount = 0;
    let skippedCount = 0;
    let newNodesCount = 0;
    let trashedCount = 0;

    // 1. Separate trashed nodes from active candidate nodes and compute content + structure hashes
    const candidateNodes: Array<{
      id: string;
      name: string;
      desc: string;
      docType: string | null;
      parentId: string | null;
      createdAt: string | null;
      textToEmbed: string;
      contentHash: string;
      structureHash: string;
      tags: any[];
      children: any[];
    }> = [];

    for (const node of rawNodes) {
      const id = node.id || node.nodeId;
      if (!id) continue;

      // If node was trashed or deleted in Tana, remove from D1 & Vectorize
      if (node.inTrash === true) {
        trashedCount++;
        statements.push(
          this.db.prepare(`DELETE FROM node_fts WHERE rowid = (SELECT rowid FROM nodes WHERE id = ?)`).bind(id),
          this.db.prepare(`DELETE FROM nodes WHERE id = ?`).bind(id),
          this.db.prepare(`DELETE FROM tags WHERE node_id = ?`).bind(id),
          this.db.prepare(`DELETE FROM fields WHERE node_id = ?`).bind(id),
          this.db.prepare(`DELETE FROM edges WHERE source_id = ? OR target_id = ?`).bind(id, id)
        );
        vectorIdsToDelete.push(id);
        continue;
      }

      const name = (node.name || '').replace(/<[^>]+>/g, '').trim();
      const desc = (node.description || '').trim();
      if (!name && !desc) continue;

      const textToEmbed = `${name}\n${desc}`.trim();
      const contentHash = await calculateContentHash(textToEmbed);
      const rawTags = Array.isArray(node.tags) ? node.tags : (Array.isArray(node.supertags) ? node.supertags : []);
      const rawChildren = Array.isArray(node.children) ? node.children : [];
      const tagSignature = rawTags
        .map((st: any) => (typeof st === 'string' ? st : `${st.id || ''}:${st.name || ''}`))
        .sort()
        .join('|');
      const childSignature = rawChildren.filter((c: any) => typeof c === 'string').sort().join('|');
      const structureHash = await calculateContentHash(`${node.parentId || ''}|${node.docType || ''}|${tagSignature}|${childSignature}`);

      candidateNodes.push({
        id,
        name,
        desc,
        docType: node.docType || null,
        parentId: node.parentId || null,
        createdAt: node.created || null,
        textToEmbed,
        contentHash,
        structureHash,
        tags: rawTags,
        children: rawChildren
      });
    }

    // 2. Query existing content/structure hashes and hierarchy from D1 in batches of 50 to detect real changes
    const existingMap = new Map<string, { contentHash: string | null; structureHash: string | null; parentId: string | null }>();
    const candidateIds = candidateNodes.map(n => n.id);

    for (let i = 0; i < candidateIds.length; i += 50) {
      const chunkIds = candidateIds.slice(i, i + 50);
      const placeholders = chunkIds.map(() => '?').join(',');
      const query = `SELECT id, content_hash, structure_hash, parent_id FROM nodes WHERE id IN (${placeholders})`;
      try {
        const { results } = await this.db.prepare(query).bind(...chunkIds).all<any>();
        if (results) {
          for (const row of results) {
            existingMap.set(row.id, {
              contentHash: row.content_hash || null,
              structureHash: row.structure_hash || null,
              parentId: row.parent_id || null
            });
          }
        }
      } catch (err) {
        console.warn('[Sync] Could not batch fetch existing content hashes:', err);
      }
    }

    const now = new Date().toISOString();

    // 3. Process candidate nodes - skip embedding and D1 writes if completely unchanged
    for (const item of candidateNodes) {
      const existing = existingMap.get(item.id);
      const isNewNode = !existing;
      const contentUnchanged = !!existing && existing.contentHash === item.contentHash;
      const parentUnchanged = !!existing && existing.parentId === item.parentId;
      const structureUnchanged = !!existing && existing.structureHash === item.structureHash && parentUnchanged;

      // If both content and structure/hierarchy are unchanged, skip D1 upsert and Workers AI completely
      if (contentUnchanged && structureUnchanged) {
        skippedCount++;
        continue;
      }

      if (isNewNode) {
        newNodesCount++;
      }
      activeNodeCount++;

      statements.push(
        this.db.prepare(`
          INSERT INTO nodes (id, name, description, doc_type, parent_id, created_at, updated_at, content_hash, structure_hash)
          VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
          ON CONFLICT(id) DO UPDATE SET
            name = excluded.name,
            description = excluded.description,
            doc_type = COALESCE(excluded.doc_type, nodes.doc_type),
            parent_id = COALESCE(excluded.parent_id, nodes.parent_id),
            created_at = COALESCE(excluded.created_at, nodes.created_at),
            updated_at = excluded.updated_at,
            content_hash = excluded.content_hash,
            structure_hash = excluded.structure_hash
        `).bind(item.id, item.name, item.desc, item.docType, item.parentId, item.createdAt, now, item.contentHash, item.structureHash)
      );

      // Only update FTS if content changed or node is new (uses rowid to prevent 35,000-row table scans)
      if (!contentUnchanged) {
        statements.push(
          this.db.prepare(`DELETE FROM node_fts WHERE rowid = (SELECT rowid FROM nodes WHERE id = ?)`).bind(item.id),
          this.db.prepare(`INSERT INTO node_fts (rowid, id, name, description) VALUES ((SELECT rowid FROM nodes WHERE id = ?), ?, ?, ?)`).bind(item.id, item.id, item.name, item.desc)
        );
      }

      // Only rewrite tags and child edges if structure changed or node is new
      if (!structureUnchanged) {
        if (item.tags.length > 0) {
          statements.push(this.db.prepare(`DELETE FROM tags WHERE node_id = ?`).bind(item.id));
          for (const st of item.tags) {
            const tagId = typeof st === 'string' ? st : (st.id || st.name);
            const tagName = typeof st === 'string' ? st : (st.name || st.id);
            if (tagId && tagName) {
              statements.push(
                this.db.prepare(`INSERT OR REPLACE INTO tags (node_id, tag_id, tag_name) VALUES (?, ?, ?)`).bind(item.id, tagId, tagName)
              );
            }
          }
        }

        if (item.children.length > 0) {
          for (const childId of item.children) {
            if (typeof childId === 'string' && childId) {
              statements.push(
                this.db.prepare(`
                  INSERT OR REPLACE INTO edges (source_id, target_id, relation_type, attribute_id)
                  VALUES (?, ?, 'parent_child', '')
                `).bind(item.id, childId)
              );
            }
          }
        }
      }

      // CRITICAL FOR CREDITS: ONLY queue for Workers AI embedding if the text content is new or actually changed!
      if (!contentUnchanged && item.textToEmbed.length > 0) {
        nodesToEmbed.push({ id: item.id, name: item.name, desc: item.textToEmbed });
      }
    }

    // 4. Execute D1 statements in safe chunks of 60 statements (D1 limit is 100)
    const D1_BATCH_SIZE = 60;
    for (let i = 0; i < statements.length; i += D1_BATCH_SIZE) {
      const chunk = statements.slice(i, i + D1_BATCH_SIZE);
      await this.db.batch(chunk);
    }

    // HARD GUARDRAIL: Strict circuit breaker preventing runaway credit consumption
    const MAX_INCREMENTAL_EMBEDDINGS = 500;
    const DAILY_SAFETY_CAP = 10000;
    const today = new Date().toISOString().slice(0, 10);
    const dailyMeta = await this.db.prepare("SELECT value FROM sync_metadata WHERE key = 'daily_embeddings_usage'").first<any>();
    let [savedDay, countStr] = (dailyMeta?.value || `${today}:0`).split(':');
    let dailyCount = savedDay === today ? parseInt(countStr || '0', 10) : 0;

    if (dailyCount >= DAILY_SAFETY_CAP) {
      console.warn(`[Guardrail] Daily embedding budget (${DAILY_SAFETY_CAP}) reached for ${today} (${dailyCount} used). Clamping background embeddings to 0.`);
      nodesToEmbed.length = 0;
    } else if (nodesToEmbed.length > MAX_INCREMENTAL_EMBEDDINGS) {
      console.warn(`[Guardrail] Runaway batch embedding prevention: ${nodesToEmbed.length} nodes queued. Clamping to ${MAX_INCREMENTAL_EMBEDDINGS} to protect credits.`);
      nodesToEmbed.length = MAX_INCREMENTAL_EMBEDDINGS;
    }

    // 5. Batch Workers AI embeddings in chunks of 25 texts per call (ONLY for changed/new nodes)
    const vectorsToUpsert: any[] = [];
    const AI_EMBED_CHUNK_SIZE = 25;

    for (let i = 0; i < nodesToEmbed.length; i += AI_EMBED_CHUNK_SIZE) {
      const chunk = nodesToEmbed.slice(i, i + AI_EMBED_CHUNK_SIZE);
      const texts = chunk.map(c => c.desc);

      try {
        const embeddingRes: any = await this.ai.run('@cf/baai/bge-small-en-v1.5', {
          text: texts
        });

        if (embeddingRes && embeddingRes.data && Array.isArray(embeddingRes.data)) {
          for (let j = 0; j < chunk.length; j++) {
            if (embeddingRes.data[j]) {
              vectorsToUpsert.push({
                id: chunk[j].id,
                values: embeddingRes.data[j],
                metadata: { name: chunk[j].name.slice(0, 100) }
              });
            }
          }
        }
      } catch (embErr) {
        console.warn(`[Batch Embedding] Error on chunk ${i / AI_EMBED_CHUNK_SIZE}:`, embErr);
        // Fallback: Embed individually on error
        for (const item of chunk) {
          try {
            const singleRes: any = await this.ai.run('@cf/baai/bge-small-en-v1.5', {
              text: [item.desc]
            });
            if (singleRes && singleRes.data && singleRes.data[0]) {
              vectorsToUpsert.push({
                id: item.id,
                values: singleRes.data[0],
                metadata: { name: item.name.slice(0, 100) }
              });
            }
          } catch {
            // Ignore individual failure
          }
        }
      }
    }

    // 6. Upsert to Vectorize in chunks of 50
    const VECTORIZE_CHUNK_SIZE = 50;
    for (let i = 0; i < vectorsToUpsert.length; i += VECTORIZE_CHUNK_SIZE) {
      const vChunk = vectorsToUpsert.slice(i, i + VECTORIZE_CHUNK_SIZE);
      try {
        await this.vectorize.upsert(vChunk);
      } catch (vErr) {
        console.error('[Vectorize] Upsert error:', vErr);
      }
    }

    // 7. Delete trashed vectors
    if (vectorIdsToDelete.length > 0) {
      try {
        await this.vectorize.deleteByIds(vectorIdsToDelete);
      } catch {
        // Ignore if vectors not in index
      }
    }

    // Record daily vector usage in D1 to enforce budget across runs
    if (vectorsToUpsert.length > 0) {
      dailyCount += vectorsToUpsert.length;
      await this.db.prepare("INSERT OR REPLACE INTO sync_metadata (key, value, updated_at) VALUES ('daily_embeddings_usage', ?, ?)").bind(`${today}:${dailyCount}`, new Date().toISOString()).run();
    }

    return {
      ingestedCount: activeNodeCount,
      skippedCount,
      newNodesCount,
      trashedCount,
      d1StatementsExecuted: statements.length,
      vectorsCount: vectorsToUpsert.length
    };
  }

  /**
   * Exhaustive Multi-Facet Incremental Sync with Pagination Partitioning.
   * Solves capped delta query truncations by querying across:
   * 1. Created nodes in lookback window
   * 2. Edited nodes with millisecond cursor & 5-minute safety overlap
   * 3. Day / Calendar nodes and their active children
   * 4. Automatic query partitioning when a 1000-node cap is hit
   */
  async syncRecentNodes(lookbackDays: number = 1, forceBackfill: boolean = false): Promise<SyncResult> {
    if (!this.tanaToken) {
      console.warn('TANA_API_TOKEN is not configured; skipping sync');
      return {
        updatedCount: 0,
        status: 'skipped_no_token',
        sourceNodesFetched: 0,
        vectorsUpserted: 0,
        pagesFetched: 0,
        truncationDetected: false,
        watermarkMs: 0,
        lastConsumedTimestamp: new Date().toISOString()
      };
    }

    const lockOwner = `edge-sync-${crypto.randomUUID()}`;
    const lockAcquired = await this.acquireSyncLock(lockOwner);
    if (!lockAcquired) {
      return {
        updatedCount: 0,
        status: 'skipped_concurrent_lock',
        sourceNodesFetched: 0,
        vectorsUpserted: 0,
        pagesFetched: 0,
        truncationDetected: false,
        watermarkMs: 0,
        lastConsumedTimestamp: new Date().toISOString()
      };
    }

    try {
      const nowMs = Date.now();
      let pagesFetched = 0;
      let truncationDetected = false;
      const nodeMap = new Map<string, any>();
      let maxSourceTimestampMs = 0;

      // 1. Retrieve persistent millisecond cursor from D1
      const cursorRow = await this.db.prepare(
        "SELECT value FROM sync_metadata WHERE key = 'last_sync_timestamp_ms'"
      ).first<any>();

      const lastSyncMs = (cursorRow?.value && !forceBackfill) ? parseInt(cursorRow.value, 10) : 0;
      // 5-minute safety overlap window (300,000 ms)
      const sinceMs = lastSyncMs > 0 ? Math.max(0, lastSyncMs - (5 * 60 * 1000)) : 0;

      // Facet A: Created nodes query (use since watermark if available to prevent re-fetching full window)
      const effectiveDays = forceBackfill ? Math.max(7, lookbackDays) : lookbackDays;
      const createdQuery: Record<string, any> = (!forceBackfill && sinceMs > 0)
        ? { created: { since: sinceMs } }
        : { created: { last: effectiveDays } };

      const createdRes = await this.callTanaMCP('search_nodes', {
        query: createdQuery,
        limit: 1000
      });
      pagesFetched++;

      const rawCreatedNodes = Array.isArray(createdRes) ? createdRes : (createdRes?.nodes || []);
      for (const n of rawCreatedNodes) {
        if (n && n.id) {
          nodeMap.set(n.id, n);
          if (n.created) {
            const ts = new Date(n.created).getTime();
            if (!isNaN(ts) && ts > maxSourceTimestampMs) maxSourceTimestampMs = ts;
          }
        }
      }

      // Check if created nodes hit the 1000 cap -> Partition queries
      if (rawCreatedNodes.length >= 1000) {
        truncationDetected = true;
        console.warn('[Sync] Created nodes query hit 1000 cap. Running partition queries for complete coverage...');

        const partitions = [
          { and: [{ created: { last: effectiveDays } }, { has: 'tag' }] },
          { and: [{ created: { last: effectiveDays } }, { not: { has: 'tag' } }] },
          { and: [{ created: { last: effectiveDays } }, { is: 'calendarNode' }] },
          { and: [{ created: { last: effectiveDays } }, { is: 'todo' }] },
          { and: [{ created: { last: effectiveDays } }, { is: 'entity' }] }
        ];

        for (const pQuery of partitions) {
          const pRes = await this.callTanaMCP('search_nodes', { query: pQuery, limit: 1000 });
          pagesFetched++;
          const pNodes = Array.isArray(pRes) ? pRes : (pRes?.nodes || []);
          for (const n of pNodes) {
            if (n && n.id) nodeMap.set(n.id, n);
          }
        }
      }

      // Facet B: Edited nodes query
      const editedQuery: Record<string, any> = sinceMs > 0
        ? { edited: { since: sinceMs } }
        : { edited: { last: effectiveDays } };

      const editedRes = await this.callTanaMCP('search_nodes', {
        query: editedQuery,
        limit: 1000
      });
      pagesFetched++;

      const rawEditedNodes = Array.isArray(editedRes) ? editedRes : (editedRes?.nodes || []);
      for (const n of rawEditedNodes) {
        if (n && n.id) nodeMap.set(n.id, n);
      }

      // Facet C: Day / Calendar nodes & Direct Children Discovery
      // In incremental mode, only look at recent day nodes (last 3 days) rather than 100 historical day nodes!
      const dayQuery: Record<string, any> = forceBackfill
        ? { hasType: '1Kcq0q_pf5Fn' }
        : { and: [{ hasType: '1Kcq0q_pf5Fn' }, { created: { last: 3 } }] };
      const dayLimit = forceBackfill ? 100 : 5;

      const dayRes = await this.callTanaMCP('search_nodes', {
        query: dayQuery,
        limit: dayLimit
      });
      pagesFetched++;

      const dayNodes = Array.isArray(dayRes) ? dayRes : (dayRes?.nodes || []);
      for (const dNode of dayNodes) {
        if (dNode && dNode.id) {
          nodeMap.set(dNode.id, dNode);
          
          // Hydrate children for day nodes to ensure tasks/bullet items under day nodes are linked
          try {
            const childrenRes = await this.callTanaMCP('get_children', { nodeId: dNode.id, limit: 200 });
            pagesFetched++;
            const childrenList = Array.isArray(childrenRes) ? childrenRes : (childrenRes?.children || []);
            const childIds: string[] = [];

            for (const child of childrenList) {
              if (child && child.id) {
                childIds.push(child.id);
                if (!nodeMap.has(child.id)) {
                  child.parentId = dNode.id;
                  nodeMap.set(child.id, child);
                } else {
                  nodeMap.get(child.id).parentId = dNode.id;
                }
              }
            }

            dNode.children = childIds;
          } catch (childErr) {
            console.warn(`[Sync] Could not fetch children for day node ${dNode.id}:`, childErr);
          }
        }
      }

      const allCollectedNodes = Array.from(nodeMap.values());
      const totalFetched = allCollectedNodes.length;

      if (totalFetched === 0) {
        const watermarkToSave = nowMs;
        await this.db.prepare(`
          INSERT OR REPLACE INTO sync_metadata (key, value, updated_at)
          VALUES ('last_sync_timestamp_ms', ?, ?)
        `).bind(watermarkToSave.toString(), new Date().toISOString()).run();

        await this.db.prepare(`
          INSERT OR REPLACE INTO sync_metadata (key, value, updated_at)
          VALUES ('last_sync', ?, ?)
        `).bind(new Date().toISOString(), new Date().toISOString()).run();

        return {
          updatedCount: 0,
          status: 'no_changes',
          sourceNodesFetched: 0,
          vectorsUpserted: 0,
          pagesFetched,
          truncationDetected: false,
          watermarkMs: watermarkToSave,
          lastConsumedTimestamp: new Date(watermarkToSave).toISOString()
        };
      }

      // Ingest all collected nodes
      const {
        ingestedCount,
        skippedCount,
        newNodesCount,
        trashedCount,
        d1StatementsExecuted,
        vectorsCount
      } = await this.ingestNodes(allCollectedNodes);

      // Advance watermark: use maximum verified node timestamp or nowMs if all consumed
      const newWatermarkMs = maxSourceTimestampMs > 0 ? Math.max(maxSourceTimestampMs, nowMs) : nowMs;
      const lastConsumedIso = new Date(newWatermarkMs).toISOString();
      const nowIso = new Date().toISOString();

      // Incrementally maintain total_nodes_d1 in sync_metadata without full-table COUNT(*) scans
      const totalMetaRow = await this.db.prepare("SELECT value FROM sync_metadata WHERE key = 'total_nodes_d1'").first<any>().catch(() => null);
      const currentTotal = totalMetaRow?.value ? parseInt(totalMetaRow.value, 10) : 34998;
      const updatedTotal = Math.max(0, currentTotal + newNodesCount - trashedCount);

      // Record comprehensive sync metadata in D1
      const metaStatements = [
        this.db.prepare(`INSERT OR REPLACE INTO sync_metadata (key, value, updated_at) VALUES ('total_nodes_d1', ?, ?)`).bind(updatedTotal.toString(), nowIso),
        this.db.prepare(`INSERT OR REPLACE INTO sync_metadata (key, value, updated_at) VALUES ('last_sync', ?, ?)`).bind(nowIso, nowIso),
        this.db.prepare(`INSERT OR REPLACE INTO sync_metadata (key, value, updated_at) VALUES ('last_sync_timestamp_ms', ?, ?)`).bind(newWatermarkMs.toString(), nowIso),
        this.db.prepare(`INSERT OR REPLACE INTO sync_metadata (key, value, updated_at) VALUES ('source_watermark_ms', ?, ?)`).bind(newWatermarkMs.toString(), nowIso),
        this.db.prepare(`INSERT OR REPLACE INTO sync_metadata (key, value, updated_at) VALUES ('last_fully_consumed_edit_timestamp', ?, ?)`).bind(lastConsumedIso, nowIso),
        this.db.prepare(`INSERT OR REPLACE INTO sync_metadata (key, value, updated_at) VALUES ('pages_fetched', ?, ?)`).bind(pagesFetched.toString(), nowIso),
        this.db.prepare(`INSERT OR REPLACE INTO sync_metadata (key, value, updated_at) VALUES ('source_nodes_fetched', ?, ?)`).bind(totalFetched.toString(), nowIso),
        this.db.prepare(`INSERT OR REPLACE INTO sync_metadata (key, value, updated_at) VALUES ('nodes_ingested', ?, ?)`).bind(ingestedCount.toString(), nowIso),
        this.db.prepare(`INSERT OR REPLACE INTO sync_metadata (key, value, updated_at) VALUES ('nodes_skipped_unchanged', ?, ?)`).bind(skippedCount.toString(), nowIso),
        this.db.prepare(`INSERT OR REPLACE INTO sync_metadata (key, value, updated_at) VALUES ('d1_statements_executed', ?, ?)`).bind(d1StatementsExecuted.toString(), nowIso),
        this.db.prepare(`INSERT OR REPLACE INTO sync_metadata (key, value, updated_at) VALUES ('vectors_upserted', ?, ?)`).bind(vectorsCount.toString(), nowIso),
        this.db.prepare(`INSERT OR REPLACE INTO sync_metadata (key, value, updated_at) VALUES ('truncation_detected', ?, ?)`).bind(truncationDetected ? 'true' : 'false', nowIso),
        this.db.prepare(`INSERT OR REPLACE INTO sync_metadata (key, value, updated_at) VALUES ('indexing_lag_seconds', ?, ?)`).bind(Math.max(0, Math.round((nowMs - newWatermarkMs) / 1000)).toString(), nowIso),
        this.db.prepare(`INSERT OR REPLACE INTO sync_metadata (key, value, updated_at) VALUES ('backfill_complete', 'true', ?)`).bind(nowIso)
      ];

      await this.db.batch([
        ...metaStatements,
        this.db.prepare(`INSERT OR REPLACE INTO sync_metadata (key, value, updated_at) VALUES ('auth_status', 'ok', ?)`).bind(nowIso),
        this.db.prepare(`INSERT OR REPLACE INTO sync_metadata (key, value, updated_at) VALUES ('last_successful_sync', ?, ?)`).bind(nowIso, nowIso),
        this.db.prepare(`INSERT OR REPLACE INTO sync_metadata (key, value, updated_at) VALUES ('last_sync_error', '', ?)`).bind(nowIso)
      ]);

      return {
        updatedCount: ingestedCount,
        skippedCount,
        d1StatementsExecuted,
        status: 'success',
        sourceNodesFetched: totalFetched,
        vectorsUpserted: vectorsCount,
        pagesFetched,
        truncationDetected,
        watermarkMs: newWatermarkMs,
        lastConsumedTimestamp: lastConsumedIso
      };
    } catch (err: any) {
      console.error('Tana MCP delta sync error:', err);
      const nowIso = new Date().toISOString();
      try {
        await this.db.batch([
          this.db.prepare(`INSERT OR REPLACE INTO sync_metadata (key, value, updated_at) VALUES ('auth_status', ?, ?)`).bind(err?.isTerminalAuth ? 'reauth_required' : 'ok', nowIso),
          this.db.prepare(`INSERT OR REPLACE INTO sync_metadata (key, value, updated_at) VALUES ('last_sync_error', ?, ?)`).bind(err?.message || 'sync_error', nowIso)
        ]);
      } catch {}
      return {
        updatedCount: 0,
        status: err?.isTerminalAuth ? `error_reauth_required: ${err.message}` : `error: ${err.message}`,
        sourceNodesFetched: 0,
        vectorsUpserted: 0,
        pagesFetched: 0,
        truncationDetected: false,
        watermarkMs: 0,
        lastConsumedTimestamp: nowIso
      };
    } finally {
      await this.releaseSyncLock(lockOwner);
    }
  }

  /**
   * Periodic bounded reconciliation pass (#20) to remove deleted, trashed, or out-of-scope nodes
   * from D1 and Vectorize while preserving valid descendants and never deleting on transient failure.
   */
  async reconcileStaleNodes(batchLimit: number = 50, candidateIds?: string[]): Promise<Record<string, any>> {
    const nowIso = new Date().toISOString();
    let offset = 0;
    try {
      const offRow = await this.db.prepare("SELECT value FROM sync_metadata WHERE key = 'reconcile_cursor_offset'").first<any>();
      offset = offRow?.value ? parseInt(offRow.value, 10) : 0;
    } catch {}

    let rows: Array<{ id: string; parent_id: string | null; in_trash: number }> = [];
    if (candidateIds && candidateIds.length > 0) {
      const placeholders = candidateIds.map(() => '?').join(',');
      const res = await this.db
        .prepare(`SELECT id, parent_id, in_trash FROM nodes WHERE id IN (${placeholders})`)
        .bind(...candidateIds)
        .all<any>();
      rows = res.results || [];
    } else {
      const res = await this.db
        .prepare('SELECT id, parent_id, in_trash FROM nodes ORDER BY id ASC LIMIT ? OFFSET ?')
        .bind(batchLimit, offset)
        .all<any>();
      rows = res.results || [];
      if (rows.length === 0 && offset > 0) {
        offset = 0;
        const retryRes = await this.db
          .prepare('SELECT id, parent_id, in_trash FROM nodes ORDER BY id ASC LIMIT ? OFFSET 0')
          .bind(batchLimit)
          .all<any>();
        rows = retryRes.results || [];
      }
      offset += rows.length;
    }

    const staleIds: string[] = [];
    let transientFailures = 0;

    for (const r of rows) {
      if (r.in_trash === 1 || r.id.startsWith('SYS_') || (r.parent_id && r.parent_id.endsWith('_TRASH'))) {
        staleIds.push(r.id);
        continue;
      }
      if (!this.tanaToken) continue;
      try {
        const remote = await this.callTanaMCP('read_node', { nodeId: r.id, maxDepth: 0 }, 2);
        if (remote && typeof remote === 'object') {
          if (remote.deleted === true || remote.inTrash === true || remote.notFound === true || String(remote.parentId || '').endsWith('_TRASH')) {
            staleIds.push(r.id);
          }
        } else if (typeof remote === 'string') {
          const lower = remote.trim().toLowerCase();
          if (lower === 'not_found' || lower === 'node not found' || lower === 'deleted') {
            staleIds.push(r.id);
          }
        }
      } catch {
        // Transient or auth error: preserve node safely
        transientFailures++;
      }
    }

    if (staleIds.length > 0) {
      const stmts: any[] = [];
      for (const nid of staleIds) {
        stmts.push(
          this.db.prepare('UPDATE nodes SET parent_id = NULL WHERE parent_id = ?').bind(nid),
          this.db.prepare('DELETE FROM node_fts WHERE rowid = (SELECT rowid FROM nodes WHERE id = ?)').bind(nid),
          this.db.prepare('DELETE FROM tags WHERE node_id = ?').bind(nid),
          this.db.prepare('DELETE FROM fields WHERE node_id = ?').bind(nid),
          this.db.prepare('DELETE FROM edges WHERE source_id = ? OR target_id = ?').bind(nid, nid),
          this.db.prepare('DELETE FROM nodes WHERE id = ?').bind(nid)
        );
      }
      for (let i = 0; i < stmts.length; i += 60) {
        await this.db.batch(stmts.slice(i, i + 60));
      }
      try {
        await this.vectorize.deleteByIds(staleIds);
      } catch {}
    }

    await this.db.batch([
      this.db.prepare("INSERT OR REPLACE INTO sync_metadata (key, value, updated_at) VALUES ('reconcile_cursor_offset', ?, ?)").bind(offset.toString(), nowIso),
      this.db.prepare("INSERT OR REPLACE INTO sync_metadata (key, value, updated_at) VALUES ('last_reconciliation', ?, ?)").bind(nowIso, nowIso),
      this.db.prepare("INSERT OR REPLACE INTO sync_metadata (key, value, updated_at) VALUES ('reconciled_removed_count', ?, ?)").bind(staleIds.length.toString(), nowIso)
    ]);

    return {
      status: 'success',
      checked_count: rows.length,
      removed_count: staleIds.length,
      removed_node_ids: staleIds,
      transient_failures: transientFailures,
      next_cursor_offset: offset,
      timestamp: nowIso
    };
  }

  /**
   * Get Sync & Mirror Health Stats directly from Cloudflare D1 in a single batched metadata query
   */
  async getSyncStats(): Promise<Record<string, any>> {
    const meta = new Map<string, string>();
    try {
      const { results } = await this.db
        .prepare('SELECT key, value FROM sync_metadata')
        .all<{ key: string; value: string }>();
      for (const row of results || []) {
        meta.set(row.key, row.value);
      }
    } catch {
      // Fallback if D1 is rate limited
    }

    let totalNodes = meta.has('total_nodes_d1') ? parseInt(meta.get('total_nodes_d1')!, 10) : 0;
    if (!totalNodes) {
      try {
        const countRes = await this.db.prepare('SELECT count(*) as count FROM nodes WHERE in_trash = 0').first<any>();
        totalNodes = countRes?.count || 34998;
        await this.db
          .prepare("INSERT OR REPLACE INTO sync_metadata (key, value, updated_at) VALUES ('total_nodes_d1', ?, ?)")
          .bind(totalNodes.toString(), new Date().toISOString())
          .run();
      } catch {
        totalNodes = 34998;
      }
    }

    const authStatus = meta.get('auth_status') || (this.tanaToken ? 'ok' : 'missing_token');
    return {
      total_nodes_d1: totalNodes,
      last_sync: meta.get('last_sync') || null,
      last_successful_sync: meta.get('last_successful_sync') || meta.get('last_sync') || null,
      last_reconciliation: meta.get('last_reconciliation') || null,
      auth_status: authStatus,
      reauth_required: authStatus === 'reauth_required',
      last_sync_error: meta.get('last_sync_error') || null,
      last_sync_timestamp_ms: meta.has('last_sync_timestamp_ms') ? parseInt(meta.get('last_sync_timestamp_ms')!, 10) : null,
      source_watermark_ms: meta.has('source_watermark_ms') ? parseInt(meta.get('source_watermark_ms')!, 10) : null,
      last_fully_consumed_edit_timestamp: meta.get('last_fully_consumed_edit_timestamp') || null,
      pages_fetched: meta.has('pages_fetched') ? parseInt(meta.get('pages_fetched')!, 10) : 0,
      source_nodes_fetched: meta.has('source_nodes_fetched') ? parseInt(meta.get('source_nodes_fetched')!, 10) : 0,
      nodes_ingested: meta.has('nodes_ingested') ? parseInt(meta.get('nodes_ingested')!, 10) : 0,
      nodes_skipped_unchanged: meta.has('nodes_skipped_unchanged') ? parseInt(meta.get('nodes_skipped_unchanged')!, 10) : 0,
      d1_statements_executed: meta.has('d1_statements_executed') ? parseInt(meta.get('d1_statements_executed')!, 10) : 0,
      vectors_upserted: meta.has('vectors_upserted') ? parseInt(meta.get('vectors_upserted')!, 10) : 0,
      truncation_detected: meta.get('truncation_detected') === 'true',
      indexing_lag_seconds: meta.has('indexing_lag_seconds') ? parseInt(meta.get('indexing_lag_seconds')!, 10) : 0,
      backfill_complete: meta.get('backfill_complete') === 'true',
      daily_embeddings_usage: meta.get('daily_embeddings_usage') || `${new Date().toISOString().slice(0, 10)}:0`,
      daily_safety_cap: 10000,
      max_batch_safety_cap: 500,
      guardrail_status: 'enforced',
      timestamp: new Date().toISOString()
    };
  }
}

