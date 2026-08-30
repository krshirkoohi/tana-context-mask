import { D1Database, VectorizeIndex, Ai } from '@cloudflare/workers-types';

export class EdgeSyncService {
  constructor(
    private db: D1Database,
    private vectorize: VectorizeIndex,
    private ai: Ai,
    private tanaToken?: string
  ) {}

  /**
   * Helper to batch embed and upsert into D1 + Vectorize on the Edge.
   * Handles deletion if inTrash is true, and updates tags, breadcrumbs, and vectors.
   */
  private async ingestNodes(rawNodes: any[]): Promise<number> {
    const statements = [];
    const vectorsToUpsert = [];
    const vectorIdsToDelete: string[] = [];
    let activeNodeCount = 0;

    for (const node of rawNodes) {
      const id = node.id || node.nodeId;
      const name = (node.name || '').replace(/<[^>]+>/g, '').trim();
      const desc = (node.description || '').trim();
      const parentId = node.parentId || null;
      const now = new Date().toISOString();

      if (!id) continue;

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

      if (!name) continue;
      activeNodeCount++;

      statements.push(
        this.db.prepare(`
          INSERT INTO nodes (id, name, description, parent_id, updated_at)
          VALUES (?, ?, ?, ?, ?)
          ON CONFLICT(id) DO UPDATE SET
            name = excluded.name,
            description = excluded.description,
            parent_id = excluded.parent_id,
            updated_at = excluded.updated_at
        `).bind(id, name, desc, parentId, now)
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

      // Generate embedding via Workers AI on Cloudflare Edge GPUs
      const textToEmbed = `${name}\n${desc}`.trim();
      try {
        const embeddingRes: any = await this.ai.run('@cf/baai/bge-small-en-v1.5', {
          text: [textToEmbed]
        });

        if (embeddingRes && embeddingRes.data && embeddingRes.data[0]) {
          vectorsToUpsert.push({
            id: id,
            values: embeddingRes.data[0],
            metadata: { name: name.slice(0, 100) }
          });
        }
      } catch (embErr) {
        console.warn(`Failed embedding for node ${id}:`, embErr);
      }
    }

    if (statements.length > 0) {
      await this.db.batch(statements);
    }

    if (vectorsToUpsert.length > 0) {
      await this.vectorize.upsert(vectorsToUpsert);
    }

    if (vectorIdsToDelete.length > 0) {
      try {
        await this.vectorize.deleteByIds(vectorIdsToDelete);
      } catch {
        // Ignore if vectors not in index
      }
    }

    return activeNodeCount;
  }

  /**
   * Incremental Delta Sync using Tana Remote MCP JSON-RPC 2.0.
   * Leverages millisecond timestamp cursor (`edited.since`) with a 5-minute safety overlap window.
   */
  async syncRecentNodes(lookbackDays: number = 1): Promise<{ updatedCount: number; status: string; queryUsed: any }> {
    if (!this.tanaToken) {
      console.warn('TANA_API_TOKEN is not configured; skipping sync');
      return { updatedCount: 0, status: 'skipped_no_token', queryUsed: null };
    }

    try {
      // 1. Retrieve persistent millisecond cursor from D1
      const cursorRow = await this.db.prepare(
        "SELECT value FROM sync_metadata WHERE key = 'last_sync_timestamp_ms'"
      ).first<any>();

      let queryFilter: any;
      const nowMs = Date.now();

      if (cursorRow?.value) {
        const lastSyncMs = parseInt(cursorRow.value, 10);
        // 5-minute safety overlap window (300,000 ms) to avoid boundary misses
        const sinceMs = Math.max(0, lastSyncMs - (5 * 60 * 1000));
        queryFilter = { edited: { since: sinceMs } };
      } else {
        queryFilter = { edited: { last: lookbackDays } };
      }

      const payload = {
        jsonrpc: '2.0',
        method: 'tools/call',
        params: {
          name: 'search_nodes',
          arguments: {
            query: queryFilter,
            limit: 200
          }
        },
        id: 1
      };

      const response = await fetch('https://app.tana.inc/mcp', {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${this.tanaToken}`,
          'Content-Type': 'application/json',
          'Accept': 'application/json, text/event-stream'
        },
        body: JSON.stringify(payload)
      });

      if (!response.ok) {
        throw new Error(`Tana MCP responded with status ${response.status}`);
      }

      const jsonRpcRes: any = await response.json();
      let rawNodes: any[] = [];

      // Extract result from MCP tool response
      if (jsonRpcRes.result && Array.isArray(jsonRpcRes.result.content)) {
        for (const item of jsonRpcRes.result.content) {
          if (item.type === 'text' && item.text) {
            try {
              const parsed = JSON.parse(item.text);
              if (Array.isArray(parsed)) rawNodes = parsed;
              else if (parsed && Array.isArray(parsed.nodes)) rawNodes = parsed.nodes;
            } catch {
              // Not JSON
            }
          }
        }
      } else if (Array.isArray(jsonRpcRes.result)) {
        rawNodes = jsonRpcRes.result;
      }

      if (rawNodes.length === 0) {
        // Advance cursor timestamp even if no changes
        await this.db.prepare(`
          INSERT OR REPLACE INTO sync_metadata (key, value, updated_at)
          VALUES ('last_sync_timestamp_ms', ?, ?)
        `).bind(nowMs.toString(), new Date().toISOString()).run();

        return { updatedCount: 0, status: 'no_changes', queryUsed: queryFilter };
      }

      const ingestedCount = await this.ingestNodes(rawNodes);

      // Record successful sync metadata & timestamp in D1
      await this.db.prepare(`
        INSERT OR REPLACE INTO sync_metadata (key, value, updated_at)
        VALUES ('last_sync', ?, ?)
      `).bind(new Date().toISOString(), new Date().toISOString()).run();

      await this.db.prepare(`
        INSERT OR REPLACE INTO sync_metadata (key, value, updated_at)
        VALUES ('last_sync_timestamp_ms', ?, ?)
      `).bind(nowMs.toString(), new Date().toISOString()).run();

      return { updatedCount: rawNodes.length, status: 'success', queryUsed: queryFilter };
    } catch (err: any) {
      console.error('Tana MCP delta sync error:', err);
      return { updatedCount: 0, status: `error: ${err.message}`, queryUsed: null };
    }
  }

  /**
   * Get Sync & Mirror Health Stats directly from Cloudflare D1
   */
  async getSyncStats(): Promise<Record<string, any>> {
    const countRes = await this.db.prepare('SELECT count(*) as count FROM nodes').first<any>();
    const lastSyncRes = await this.db.prepare("SELECT value FROM sync_metadata WHERE key = 'last_sync'").first<any>();
    const lastSyncMsRes = await this.db.prepare("SELECT value FROM sync_metadata WHERE key = 'last_sync_timestamp_ms'").first<any>();
    const backfillCompRes = await this.db.prepare("SELECT value FROM sync_metadata WHERE key = 'backfill_complete'").first<any>();

    return {
      total_nodes_d1: countRes?.count || 0,
      last_sync: lastSyncRes?.value || null,
      last_sync_timestamp_ms: lastSyncMsRes?.value ? parseInt(lastSyncMsRes.value, 10) : null,
      backfill_complete: backfillCompRes?.value === 'true',
      timestamp: new Date().toISOString()
    };
  }
}
