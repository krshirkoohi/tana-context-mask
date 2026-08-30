import { D1Database, VectorizeIndex, Ai } from '@cloudflare/workers-types';
import { TanaNode } from '../types';
import { D1GraphStore } from './graph';

export interface SearchHit {
  id: string;
  score: number;
  source: 'vector' | 'keyword' | 'hybrid';
}

const STOPWORDS = new Set([
  'a', 'about', 'above', 'after', 'again', 'against', 'all', 'am', 'an', 'and',
  'any', 'are', 'as', 'at', 'be', 'because', 'been', 'before', 'being',
  'below', 'between', 'both', 'but', 'by', 'can', 'did', 'do', 'does', 'doing',
  'down', 'during', 'each', 'few', 'for', 'from', 'further', 'had', 'has', 'have',
  'having', 'he', 'her', 'here', 'hers', 'him', 'his', 'how', 'i', 'if', 'in',
  'into', 'is', 'it', 'its', 'me', 'more', 'most', 'my', 'no', 'nor', 'not',
  'of', 'off', 'on', 'once', 'only', 'or', 'other', 'our', 'ours', 'out', 'over',
  'own', 'same', 'she', 'should', 'so', 'some', 'such', 'than', 'that', 'the',
  'their', 'theirs', 'them', 'then', 'there', 'these', 'they', 'this', 'those',
  'through', 'to', 'too', 'under', 'until', 'up', 'very', 'was', 'we', 'were',
  'what', 'when', 'where', 'which', 'while', 'who', 'whom', 'why', 'with',
  'would', 'you', 'your', 'yours'
]);

export interface ScoredNode {
  node: TanaNode;
  score: number;
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
   * Performs hybrid search returning nodes with their calibrated fusion score.
   */
  async searchWithScores(query: string, limit: number = 20, alpha: number = 0.50): Promise<ScoredNode[]> {
    // 1. Vector Search via Vectorize
    const vectorPromise = (async () => {
      try {
        const queryVector = await this.embedQuery(query);
        const vectorResults = await this.vectorize.query(queryVector, {
          topK: limit * 2,
          returnValues: false,
          returnMetadata: 'none'
        });
        return (vectorResults.matches || []).map(m => ({ id: m.id, score: m.score }));
      } catch (err) {
        console.error('Vector search error:', err);
        return [];
      }
    })();

    // 2. Keyword Search via D1 FTS5 (with stopword filtering and phrase matching)
    const keywordPromise = (async () => {
      try {
        const rawTokens = (query.toLowerCase().match(/\w+/g) || []);
        const meaningfulTerms = rawTokens.filter(t => !STOPWORDS.has(t) && t.length > 1);
        const searchTerms = meaningfulTerms.length > 0 ? meaningfulTerms : rawTokens.filter(t => t.length > 1);

        if (searchTerms.length === 0) return [];
        
        // Exact phrase prefix + individual token prefix OR
        const phraseTerm = `"${searchTerms.join(' ')}"`;
        const ftsQuery = [phraseTerm, ...searchTerms.map(t => `"${t}"*`)].join(' OR ');

        const { results } = await this.db
          .prepare(`
            SELECT f.id, bm25(node_fts) as rank_score
            FROM node_fts f
            JOIN nodes n ON f.id = n.id
            WHERE node_fts MATCH ? AND n.in_trash = 0
            ORDER BY rank_score ASC
            LIMIT ?
          `)
          .bind(ftsQuery, limit * 2)
          .all<any>();

        return (results || []).map((r: any, idx: number) => {
          // Top keyword match receives 1.0, 2nd receives 0.96, etc.
          const keywordScore = Math.max(0.4, 1.0 - (idx * 0.04));
          return { id: r.id, score: keywordScore };
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
    const sortedEntries = Array.from(fusedScores.entries())
      .sort((a, b) => b[1] - a[1])
      .slice(0, limit);

    const rankedIds = sortedEntries.map(e => e[0]);
    const nodes = await this.graphStore.getNodesByIds(rankedIds);
    const nodeMap = new Map<string, TanaNode>(nodes.map(n => [n.id, n]));

    const scoredNodes: ScoredNode[] = [];
    for (const [id, score] of sortedEntries) {
      const node = nodeMap.get(id);
      if (node) {
        scoredNodes.push({ node, score });
      }
    }

    return scoredNodes;
  }

  /**
   * Performs hybrid search across Cloudflare Vectorize and D1 FTS5.
   */
  async search(query: string, limit: number = 20, alpha: number = 0.50): Promise<TanaNode[]> {
    const scored = await this.searchWithScores(query, limit, alpha);
    return scored.map(s => s.node);
  }
}
