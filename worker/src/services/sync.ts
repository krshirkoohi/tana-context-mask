import { D1Database, VectorizeIndex, Ai } from '@cloudflare/workers-types';

export class EdgeSyncService {
  constructor(
    private db: D1Database,
    private vectorize: VectorizeIndex,
    private ai: Ai,
    private tanaToken?: string
  ) {}

  /**
   * Autonomous Full Workspace Backfill in the Cloud.
   * Self-paginates and tracks progress in D1 sync_metadata across Cloudflare Cron ticks.
   */
  async backfillWorkspaceCloud(batchSize: number = 200): Promise<{ processed: number; done: boolean; status: string }> {
    if (!this.tanaToken) {
      return { processed: 0, done: true, status: 'skipped_no_token' };
    }

    try {
      // 1. Get current backfill cursor from D1
      const metaRow = await this.db.prepare(
        "SELECT value FROM sync_metadata WHERE key = 'backfill_cursor'"
      ).first<any>();
      const cursor = metaRow?.value ? parseInt(metaRow.value, 10) : 0;

      // 2. Fetch chunk from Tana Cloud API
      const response = await fetch('https://europe-west1-tagr-prod.cloudfunctions.net/search', {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${this.tanaToken}`,
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          limit: batchSize,
          offset: cursor
        })
      });

      if (!response.ok) {
        throw new Error(`Tana API returned HTTP ${response.status}`);
      }

      const data: any = await response.json();
      const rawNodes = data.results || data.nodes || [];

      if (rawNodes.length === 0) {
        // Full workspace backfill completed
        await this.db.prepare(
          "INSERT OR REPLACE INTO sync_metadata (key, value, updated_at) VALUES ('backfill_complete', 'true', ?)"
        ).bind(new Date().toISOString()).run();
        return { processed: 0, done: true, status: 'backfill_completed' };
      }

      // 3. Ingest chunk into D1 and Vectorize
      await this.ingestNodes(rawNodes);

      // 4. Update cursor in D1
      const nextCursor = cursor + rawNodes.length;
      await this.db.prepare(
        "INSERT OR REPLACE INTO sync_metadata (key, value, updated_at) VALUES ('backfill_cursor', ?, ?)"
      ).bind(nextCursor.toString(), new Date().toISOString()).run();

      return {
        processed: rawNodes.length,
        done: rawNodes.length < batchSize,
        status: `processed_offset_${cursor}`
      };
    } catch (err: any) {
      console.error('Cloud backfill error:', err);
      return { processed: 0, done: false, status: `error: ${err.message}` };
    }
  }

  /**
   * Helper to batch embed and upsert into D1 + Vectorize on the Edge.
   */
  private async ingestNodes(rawNodes: any[]): Promise<void> {
    const statements = [];
    const vectorsToUpsert = [];

    for (const node of rawNodes) {
      const id = node.id;
      const name = (node.name || '').replace(/<[^>]+>/g, '').trim();
      const desc = (node.description || '').trim();
      const parentId = node.parentId || null;
      const now = new Date().toISOString();

      if (!id || !name) continue;

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
      if (Array.isArray(node.supertags)) {
        statements.push(this.db.prepare(`DELETE FROM tags WHERE node_id = ?`).bind(id));
        for (const st of node.supertags) {
          const tagId = typeof st === 'string' ? st : st.id;
          const tagName = typeof st === 'string' ? st : (st.name || st.id);
          statements.push(
            this.db.prepare(`INSERT OR REPLACE INTO tags (node_id, tag_id, tag_name) VALUES (?, ?, ?)`).bind(id, tagId, tagName)
          );
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
  }

  /**
   * Incremental sync using the official Tana Remote MCP JSON-RPC 2.0 endpoint.
   * Only fetches nodes edited in the last lookback window (default 1 day) to stay well within rate limits.
   */
  async syncRecentNodes(lookbackDays: number = 1): Promise<{ updatedCount: number; status: string }> {
    if (!this.tanaToken) {
      console.warn('TANA_API_TOKEN is not configured; skipping sync');
      return { updatedCount: 0, status: 'skipped_no_token' };
    }

    try {
      const payload = {
        jsonrpc: '2.0',
        method: 'tools/call',
        params: {
          name: 'search_nodes',
          arguments: {
            query: { edited: { last: lookbackDays } },
            limit: 100
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
        return { updatedCount: 0, status: 'no_changes' };
      }

      await this.ingestNodes(rawNodes);

      await this.db.prepare(`
        INSERT OR REPLACE INTO sync_metadata (key, value, updated_at)
        VALUES ('last_sync', ?, ?)
      `).bind(new Date().toISOString(), new Date().toISOString()).run();

      return { updatedCount: rawNodes.length, status: 'success' };
    } catch (err: any) {
      console.error('Tana MCP sync error:', err);
      return { updatedCount: 0, status: `error: ${err.message}` };
    }
  }

  /**
   * Get Sync & Mirror Health Stats directly from Cloudflare D1
   */
  async getSyncStats(): Promise<Record<string, any>> {
    const countRes = await this.db.prepare('SELECT count(*) as count FROM nodes').first<any>();
    const lastSyncRes = await this.db.prepare("SELECT value FROM sync_metadata WHERE key = 'last_sync'").first<any>();
    const backfillCompRes = await this.db.prepare("SELECT value FROM sync_metadata WHERE key = 'backfill_complete'").first<any>();
    const cursorRes = await this.db.prepare("SELECT value FROM sync_metadata WHERE key = 'backfill_cursor'").first<any>();

    return {
      total_nodes_d1: countRes?.count || 0,
      last_sync: lastSyncRes?.value || null,
      backfill_complete: backfillCompRes?.value === 'true',
      backfill_cursor: cursorRes?.value ? parseInt(cursorRes.value, 10) : 0,
      timestamp: new Date().toISOString()
    };
  }
}
