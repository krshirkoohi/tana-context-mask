import { D1Database, VectorizeIndex, Ai } from '@cloudflare/workers-types';

export class EdgeSyncService {
  constructor(
    private db: D1Database,
    private vectorize: VectorizeIndex,
    private ai: Ai,
    private tanaToken?: string
  ) {}

  /**
   * Incremental sync triggered via Cloudflare Cron trigger or manual API call.
   */
  async syncRecentNodes(lookbackDays: number = 1): Promise<{ updatedCount: number; status: string }> {
    if (!this.tanaToken) {
      console.warn('TANA_API_TOKEN is not configured; skipping sync');
      return { updatedCount: 0, status: 'skipped_no_token' };
    }

    try {
      // Call Tana API for modified nodes
      const response = await fetch('https://europe-west1-tagr-prod.cloudfunctions.net/search', {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${this.tanaToken}`,
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          edited: { last: lookbackDays }
        })
      });

      if (!response.ok) {
        throw new Error(`Tana API responded with status ${response.status}`);
      }

      const data: any = await response.json();
      const rawNodes = data.results || data.nodes || [];

      if (rawNodes.length === 0) {
        return { updatedCount: 0, status: 'no_changes' };
      }

      // Upsert nodes to D1 in batch
      const statements = [];
      const vectorsToUpsert = [];

      for (const node of rawNodes) {
        const id = node.id;
        const name = (node.name || '').replace(/<[^>]+>/g, '').trim();
        const desc = (node.description || '').trim();
        const parentId = node.parentId || null;
        const now = new Date().toISOString();

        if (!id || !name) continue;

        // D1 statement
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

        // FTS statement
        statements.push(
          this.db.prepare(`DELETE FROM node_fts WHERE id = ?`).bind(id),
          this.db.prepare(`INSERT INTO node_fts (id, name, description) VALUES (?, ?, ?)`).bind(id, name, desc)
        );

        // Generate embedding
        const textToEmbed = `${name}\n${desc}`.trim();
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
      }

      // Execute D1 batch
      if (statements.length > 0) {
        await this.db.batch(statements);
      }

      // Execute Vectorize upsert
      if (vectorsToUpsert.length > 0) {
        await this.vectorize.upsert(vectorsToUpsert);
      }

      // Record metadata
      await this.db.prepare(`
        INSERT OR REPLACE INTO sync_metadata (key, value, updated_at)
        VALUES ('last_sync', ?, ?)
      `).bind(new Date().toISOString(), new Date().toISOString()).run();

      return { updatedCount: rawNodes.length, status: 'success' };
    } catch (err: any) {
      console.error('Incremental sync error:', err);
      return { updatedCount: 0, status: `error: ${err.message}` };
    }
  }
}
