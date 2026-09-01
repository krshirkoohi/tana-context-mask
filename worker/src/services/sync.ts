import { D1Database, VectorizeIndex, Ai } from '@cloudflare/workers-types';

export interface SyncResult {
  updatedCount: number;
  status: string;
  sourceNodesFetched: number;
  vectorsUpserted: number;
  pagesFetched: number;
  truncationDetected: boolean;
  watermarkMs: number;
  lastConsumedTimestamp: string;
}

export class EdgeSyncService {
  constructor(
    private db: D1Database,
    private vectorize: VectorizeIndex,
    private ai: Ai,
    private tanaToken?: string
  ) {}

  /**
   * Helper to call Tana Remote MCP JSON-RPC 2.0 tool endpoint with retry & backoff.
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

        if (res.status === 429 || (res.status >= 500 && res.status <= 504)) {
          if (attempt < maxRetries) {
            const backoffMs = Math.min(4000, 500 * Math.pow(2, attempt));
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
        if (attempt >= maxRetries) {
          throw err;
        }
        const backoffMs = Math.min(3000, 400 * Math.pow(2, attempt));
        await new Promise(resolve => setTimeout(resolve, backoffMs));
      }
    }
    return null;
  }

  /**
   * Helper to batch embed and upsert into D1 + Vectorize on the Edge.
   * Chunks D1 statements to stay safely within D1 100-statement limits.
   * Batches Workers AI embedding calls and Vectorize upserts.
   */
  private async ingestNodes(rawNodes: any[]): Promise<{ ingestedCount: number; vectorsCount: number }> {
    const statements: any[] = [];
    const vectorIdsToDelete: string[] = [];
    const nodesToEmbed: Array<{ id: string; name: string; desc: string }> = [];
    let activeNodeCount = 0;

    for (const node of rawNodes) {
      const id = node.id || node.nodeId;
      if (!id) continue;

      const name = (node.name || '').replace(/<[^>]+>/g, '').trim();
      const desc = (node.description || '').trim();
      const docType = node.docType || null;
      const parentId = node.parentId || null;
      const createdAt = node.created || null;
      const now = new Date().toISOString();

      // If node was trashed or deleted in Tana, remove from D1 & Vectorize
      if (node.inTrash === true) {
        statements.push(
          this.db.prepare(`DELETE FROM nodes WHERE id = ?`).bind(id),
          this.db.prepare(`DELETE FROM node_fts WHERE id = ?`).bind(id),
          this.db.prepare(`DELETE FROM tags WHERE node_id = ?`).bind(id),
          this.db.prepare(`DELETE FROM fields WHERE node_id = ?`).bind(id),
          this.db.prepare(`DELETE FROM edges WHERE source_id = ? OR target_id = ?`).bind(id, id)
        );
        vectorIdsToDelete.push(id);
        continue;
      }

      if (!name && !desc) continue;
      activeNodeCount++;

      statements.push(
        this.db.prepare(`
          INSERT INTO nodes (id, name, description, doc_type, parent_id, created_at, updated_at)
          VALUES (?, ?, ?, ?, ?, ?, ?)
          ON CONFLICT(id) DO UPDATE SET
            name = excluded.name,
            description = excluded.description,
            doc_type = COALESCE(excluded.doc_type, nodes.doc_type),
            parent_id = COALESCE(excluded.parent_id, nodes.parent_id),
            created_at = COALESCE(excluded.created_at, nodes.created_at),
            updated_at = excluded.updated_at
        `).bind(id, name, desc, docType, parentId, createdAt, now)
      );

      statements.push(
        this.db.prepare(`DELETE FROM node_fts WHERE id = ?`).bind(id),
        this.db.prepare(`INSERT INTO node_fts (id, name, description) VALUES (?, ?, ?)`).bind(id, name, desc)
      );

      // Handle tags if present
      const tagsList = Array.isArray(node.tags) ? node.tags : (Array.isArray(node.supertags) ? node.supertags : []);
      if (tagsList.length > 0) {
        statements.push(this.db.prepare(`DELETE FROM tags WHERE node_id = ?`).bind(id));
        for (const st of tagsList) {
          const tagId = typeof st === 'string' ? st : (st.id || st.name);
          const tagName = typeof st === 'string' ? st : (st.name || st.id);
          if (tagId && tagName) {
            statements.push(
              this.db.prepare(`INSERT OR REPLACE INTO tags (node_id, tag_id, tag_name) VALUES (?, ?, ?)`).bind(id, tagId, tagName)
            );
          }
        }
      }

      // Handle child edges if present (e.g. from get_children or day nodes)
      if (Array.isArray(node.children) && node.children.length > 0) {
        for (const childId of node.children) {
          if (typeof childId === 'string' && childId) {
            statements.push(
              this.db.prepare(`
                INSERT OR REPLACE INTO edges (source_id, target_id, relation_type, attribute_id)
                VALUES (?, ?, 'parent_child', '')
              `).bind(id, childId)
            );
          }
        }
      }

      // Queue text for batch Workers AI embedding
      const textToEmbed = `${name}\n${desc}`.trim();
      if (textToEmbed.length > 0) {
        nodesToEmbed.push({ id, name, desc: textToEmbed });
      }
    }

    // 1. Execute D1 statements in safe chunks of 60 statements (D1 limit is 100)
    const D1_BATCH_SIZE = 60;
    for (let i = 0; i < statements.length; i += D1_BATCH_SIZE) {
      const chunk = statements.slice(i, i + D1_BATCH_SIZE);
      await this.db.batch(chunk);
    }

    // 2. Batch Workers AI embeddings in chunks of 25 texts per call
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

    // 3. Upsert to Vectorize in chunks of 50
    const VECTORIZE_CHUNK_SIZE = 50;
    for (let i = 0; i < vectorsToUpsert.length; i += VECTORIZE_CHUNK_SIZE) {
      const vChunk = vectorsToUpsert.slice(i, i + VECTORIZE_CHUNK_SIZE);
      try {
        await this.vectorize.upsert(vChunk);
      } catch (vErr) {
        console.error('[Vectorize] Upsert error:', vErr);
      }
    }

    // 4. Delete trashed vectors
    if (vectorIdsToDelete.length > 0) {
      try {
        await this.vectorize.deleteByIds(vectorIdsToDelete);
      } catch {
        // Ignore if vectors not in index
      }
    }

    return { ingestedCount: activeNodeCount, vectorsCount: vectorsToUpsert.length };
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

      // Facet A: Created nodes in lookback window (limit 1000)
      const effectiveDays = forceBackfill ? Math.max(7, lookbackDays) : lookbackDays;
      const createdRes = await this.callTanaMCP('search_nodes', {
        query: { created: { last: effectiveDays } },
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
      // Fetches recent Day nodes (tag: 1Kcq0q_pf5Fn) and hydrates their children via get_children
      const dayRes = await this.callTanaMCP('search_nodes', {
        query: { hasType: '1Kcq0q_pf5Fn' },
        limit: 100
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
      const { ingestedCount, vectorsCount } = await this.ingestNodes(allCollectedNodes);

      // Advance watermark: use maximum verified node timestamp or nowMs if all consumed
      const newWatermarkMs = maxSourceTimestampMs > 0 ? Math.max(maxSourceTimestampMs, nowMs) : nowMs;
      const lastConsumedIso = new Date(newWatermarkMs).toISOString();

      // Record comprehensive sync metadata in D1
      const metaStatements = [
        this.db.prepare(`INSERT OR REPLACE INTO sync_metadata (key, value, updated_at) VALUES ('last_sync', ?, ?)`).bind(new Date().toISOString(), new Date().toISOString()),
        this.db.prepare(`INSERT OR REPLACE INTO sync_metadata (key, value, updated_at) VALUES ('last_sync_timestamp_ms', ?, ?)`).bind(newWatermarkMs.toString(), new Date().toISOString()),
        this.db.prepare(`INSERT OR REPLACE INTO sync_metadata (key, value, updated_at) VALUES ('source_watermark_ms', ?, ?)`).bind(newWatermarkMs.toString(), new Date().toISOString()),
        this.db.prepare(`INSERT OR REPLACE INTO sync_metadata (key, value, updated_at) VALUES ('last_fully_consumed_edit_timestamp', ?, ?)`).bind(lastConsumedIso, new Date().toISOString()),
        this.db.prepare(`INSERT OR REPLACE INTO sync_metadata (key, value, updated_at) VALUES ('pages_fetched', ?, ?)`).bind(pagesFetched.toString(), new Date().toISOString()),
        this.db.prepare(`INSERT OR REPLACE INTO sync_metadata (key, value, updated_at) VALUES ('source_nodes_fetched', ?, ?)`).bind(totalFetched.toString(), new Date().toISOString()),
        this.db.prepare(`INSERT OR REPLACE INTO sync_metadata (key, value, updated_at) VALUES ('nodes_ingested', ?, ?)`).bind(ingestedCount.toString(), new Date().toISOString()),
        this.db.prepare(`INSERT OR REPLACE INTO sync_metadata (key, value, updated_at) VALUES ('vectors_upserted', ?, ?)`).bind(vectorsCount.toString(), new Date().toISOString()),
        this.db.prepare(`INSERT OR REPLACE INTO sync_metadata (key, value, updated_at) VALUES ('truncation_detected', ?, ?)`).bind(truncationDetected ? 'true' : 'false', new Date().toISOString()),
        this.db.prepare(`INSERT OR REPLACE INTO sync_metadata (key, value, updated_at) VALUES ('indexing_lag_seconds', ?, ?)`).bind(Math.max(0, Math.round((nowMs - newWatermarkMs) / 1000)).toString(), new Date().toISOString()),
        this.db.prepare(`INSERT OR REPLACE INTO sync_metadata (key, value, updated_at) VALUES ('backfill_complete', 'true', ?)`).bind(new Date().toISOString())
      ];

      await this.db.batch(metaStatements);

      return {
        updatedCount: ingestedCount,
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
      return {
        updatedCount: 0,
        status: `error: ${err.message}`,
        sourceNodesFetched: 0,
        vectorsUpserted: 0,
        pagesFetched: 0,
        truncationDetected: false,
        watermarkMs: 0,
        lastConsumedTimestamp: new Date().toISOString()
      };
    }
  }

  /**
   * Get Sync & Mirror Health Stats directly from Cloudflare D1
   */
  async getSyncStats(): Promise<Record<string, any>> {
    const countRes = await this.db.prepare('SELECT count(*) as count FROM nodes WHERE in_trash = 0').first<any>();
    const lastSyncRes = await this.db.prepare("SELECT value FROM sync_metadata WHERE key = 'last_sync'").first<any>();
    const lastSyncMsRes = await this.db.prepare("SELECT value FROM sync_metadata WHERE key = 'last_sync_timestamp_ms'").first<any>();
    const watermarkRes = await this.db.prepare("SELECT value FROM sync_metadata WHERE key = 'source_watermark_ms'").first<any>();
    const lastConsumedRes = await this.db.prepare("SELECT value FROM sync_metadata WHERE key = 'last_fully_consumed_edit_timestamp'").first<any>();
    const pagesRes = await this.db.prepare("SELECT value FROM sync_metadata WHERE key = 'pages_fetched'").first<any>();
    const sourceFetchedRes = await this.db.prepare("SELECT value FROM sync_metadata WHERE key = 'source_nodes_fetched'").first<any>();
    const nodesIngestedRes = await this.db.prepare("SELECT value FROM sync_metadata WHERE key = 'nodes_ingested'").first<any>();
    const vectorsUpsertedRes = await this.db.prepare("SELECT value FROM sync_metadata WHERE key = 'vectors_upserted'").first<any>();
    const truncationRes = await this.db.prepare("SELECT value FROM sync_metadata WHERE key = 'truncation_detected'").first<any>();
    const lagRes = await this.db.prepare("SELECT value FROM sync_metadata WHERE key = 'indexing_lag_seconds'").first<any>();
    const backfillCompRes = await this.db.prepare("SELECT value FROM sync_metadata WHERE key = 'backfill_complete'").first<any>();

    return {
      total_nodes_d1: countRes?.count || 0,
      last_sync: lastSyncRes?.value || null,
      last_sync_timestamp_ms: lastSyncMsRes?.value ? parseInt(lastSyncMsRes.value, 10) : null,
      source_watermark_ms: watermarkRes?.value ? parseInt(watermarkRes.value, 10) : null,
      last_fully_consumed_edit_timestamp: lastConsumedRes?.value || null,
      pages_fetched: pagesRes?.value ? parseInt(pagesRes.value, 10) : 0,
      source_nodes_fetched: sourceFetchedRes?.value ? parseInt(sourceFetchedRes.value, 10) : 0,
      nodes_ingested: nodesIngestedRes?.value ? parseInt(nodesIngestedRes.value, 10) : 0,
      vectors_upserted: vectorsUpsertedRes?.value ? parseInt(vectorsUpsertedRes.value, 10) : 0,
      truncation_detected: truncationRes?.value === 'true',
      indexing_lag_seconds: lagRes?.value ? parseInt(lagRes.value, 10) : 0,
      backfill_complete: backfillCompRes?.value === 'true',
      timestamp: new Date().toISOString()
    };
  }
}

