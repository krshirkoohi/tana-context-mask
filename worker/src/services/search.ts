import { D1Database, VectorizeIndex, Ai } from '@cloudflare/workers-types';
import { TanaNode } from '../types';
import { D1GraphStore } from './graph';

export interface SearchHit {
  id: string;
  score: number;
  source: 'vector' | 'keyword' | 'hybrid';
}

export class HybridSearchService {
  constructor(
    private db: D1Database,
    private vectorize: VectorizeIndex,
    private ai: Ai,
    private graphStore: D1GraphStore
  ) {}

  /**
   * Generates 384-dim BGE embedding on Cloudflare Workers AI edge GPUs.
   */
  async embedQuery(query: string): Promise<number[]> {
    const response: any = await this.ai.run('@cf/baai/bge-small-en-v1.5', {
      text: [query]
    });
    // Cloudflare Workers AI returns { shape: [...], data: [[...]] }
    if (response && response.data && response.data[0]) {
      return response.data[0];
    }
    throw new Error('Failed to generate embedding from Workers AI');
  }

  /**
   * Performs hybrid search across Cloudflare Vectorize and D1 FTS5.
   */
  async search(query: string, limit: number = 20, alpha: number = 0.65): Promise<TanaNode[]> {
    // 1. Vector Search via Vectorize
    const vectorPromise = (async () => {
      try {
        const queryVector = await this.embedQuery(query);
        const vectorResults = await this.vectorize.query(queryVector, {
          topK: limit,
          returnValues: false,
          returnMetadata: 'none'
        });
        return vectorResults.matches.map(m => ({ id: m.id, score: m.score }));
      } catch (err) {
        console.error('Vector search error:', err);
        return [];
      }
    })();

    // 2. Keyword Search via D1 FTS5
    const keywordPromise = (async () => {
      try {
        const cleanTerms = query.match(/\w+/g) || [];
        if (cleanTerms.length === 0) return [];
        const ftsQuery = cleanTerms.map(t => `"${t}"*`).join(' OR ');

        const { results } = await this.db
          .prepare(`
            SELECT f.id, bm25(node_fts) as rank_score
            FROM node_fts f
            JOIN nodes n ON f.id = n.id
            WHERE node_fts MATCH ? AND n.in_trash = 0
            ORDER BY rank_score ASC
            LIMIT ?
          `)
          .bind(ftsQuery, limit)
          .all<any>();

        return (results || []).map((r: any) => {
          const rawBm25 = Math.max(0.0, -Number(r.rank_score));
          const normScore = rawBm25 / (1.0 + rawBm25);
          return { id: r.id, score: normScore };
        });
      } catch (err) {
        console.error('FTS search error:', err);
        return [];
      }
    })();

    const [vectorHits, keywordHits] = await Promise.all([vectorPromise, keywordPromise]);

    // 3. Score Fusion (Alpha Weighted)
    const fusedScores = new Map<string, number>();

    for (const v of vectorHits) {
      fusedScores.set(v.id, (fusedScores.get(v.id) || 0) + v.score * alpha);
    }

    for (const k of keywordHits) {
      fusedScores.set(k.id, (fusedScores.get(k.id) || 0) + k.score * (1.0 - alpha));
    }

    // Sort by final score
    const rankedIds = Array.from(fusedScores.entries())
      .sort((a, b) => b[1] - a[1])
      .slice(0, limit)
      .map(entry => entry[0]);

    return await this.graphStore.getNodesByIds(rankedIds);
  }
}
