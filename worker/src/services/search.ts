import { D1Database, VectorizeIndex, Ai } from '@cloudflare/workers-types';
import { TanaNode, SemanticSearchRequest, SearchResponse, SearchResultItem } from '../types';
import { D1GraphStore } from './graph';
import {
  computeTemporalScore,
  classifyTemporalRelationship,
  parseTemporalIntent,
  isDayNode
} from './temporal';

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
    if (response && response.data && response.data[0]) {
      return response.data[0];
    }
    throw new Error('Failed to generate embedding from Workers AI');
  }

  async getNodesByDateRange(dateFrom: string, dateTo: string, limit: number = 100): Promise<TanaNode[]> {
    const { results } = await this.db
      .prepare(`
        SELECT id FROM nodes
        WHERE in_trash = 0 AND effective_date IS NOT NULL
          AND effective_date >= ? AND effective_date <= ?
        ORDER BY effective_date ASC
        LIMIT ?
      `)
      .bind(dateFrom, dateTo, limit)
      .all<any>();
    const ids = (results || []).map(r => r.id);
    return this.graphStore.getNodesByIds(ids);
  }

  /**
   * Performs hybrid search with full calendar provenance & temporal gating.
   */
  async searchWithTemporal(req: SemanticSearchRequest): Promise<SearchResponse> {
    const startT = Date.now();
    const query = (req.query || '').trim();
    if (!query) {
      return { query, total_hits: 0, results: [], latency_ms: 0 };
    }

    const limit = req.limit || 20;
    const alpha = 0.50;
    let targetDate = req.target_date;
    let dateFrom = req.date_from;
    let dateTo = req.date_to;
    let temporalMode = req.temporal_mode || 'none';
    const temporalWeight = req.temporal_weight || 0.40;

    if (temporalMode === 'none' && !targetDate && !dateFrom && !dateTo) {
      const intent = parseTemporalIntent(query);
      if (intent.has_temporal_intent && intent.target_date) {
        targetDate = intent.target_date;
        dateFrom = intent.date_from;
        dateTo = intent.date_to;
        temporalMode = intent.temporal_mode;
      }
    }

    const candidateMap = new Map<string, TanaNode>();
    const vectorScores = new Map<string, number>();
    const keywordScores = new Map<string, number>();

    // Strategy A: Calendar-first candidate generation for strict/filter
    if (['strict', 'filter'].includes(temporalMode) && (targetDate || dateFrom || dateTo)) {
      const wFrom = dateFrom || (targetDate ? `${targetDate.slice(0, 7)}-01` : '1900-01-01');
      const wTo = dateTo || (targetDate ? `${targetDate.slice(0, 7)}-31` : '2099-12-31');
      const calNodes = await this.getNodesByDateRange(wFrom, wTo, 100);
      for (const cn of calNodes) {
        candidateMap.set(cn.id, cn);
      }
    }

    // Strategy B: Vector + Lexical Search
    const vectorPromise = (async () => {
      try {
        const queryVector = await this.embedQuery(query);
        const vectorResults = await this.vectorize.query(queryVector, {
          topK: limit * 3,
          returnValues: false,
          returnMetadata: 'none'
        });
        return (vectorResults.matches || []).map(m => ({ id: m.id, score: m.score }));
      } catch (err) {
        console.error('Vector search error:', err);
        return [];
      }
    })();

    const keywordPromise = (async () => {
      try {
        const rawTokens = (query.toLowerCase().match(/\w+/g) || []);
        const meaningfulTerms = rawTokens.filter(t => !STOPWORDS.has(t) && t.length > 1);
        const searchTerms = meaningfulTerms.length > 0 ? meaningfulTerms : rawTokens.filter(t => t.length > 1);

        if (searchTerms.length === 0) return [];
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
          .bind(ftsQuery, limit * 3)
          .all<any>();

        return (results || []).map((r: any, idx: number) => {
          const keywordScore = Math.max(0.4, 1.0 - (idx * 0.04));
          return { id: r.id, score: keywordScore };
        });
      } catch (err) {
        console.error('FTS search error:', err);
        return [];
      }
    })();

    const [vectorHits, keywordHits] = await Promise.all([vectorPromise, keywordPromise]);

    for (const v of vectorHits) {
      vectorScores.set(v.id, v.score);
    }
    for (const k of keywordHits) {
      keywordScores.set(k.id, k.score);
    }

    const allCandidateIds = new Set<string>([
      ...Array.from(candidateMap.keys()),
      ...Array.from(vectorScores.keys()),
      ...Array.from(keywordScores.keys())
    ]);

    const missingIds = Array.from(allCandidateIds).filter(id => !candidateMap.has(id));
    if (missingIds.length > 0) {
      const hydrated = await this.graphStore.getNodesByIds(missingIds);
      for (const h of hydrated) {
        candidateMap.set(h.id, h);
      }
    }

    const targetYear = targetDate ? targetDate.substring(0, 4) : (dateFrom ? dateFrom.substring(0, 4) : undefined);
    const scoredList: Array<{
      node: TanaNode;
      finalScore: number;
      semScore: number;
      lexScore: number;
      tempScore: number;
      relClass: string;
    }> = [];

    for (const [id, node] of candidateMap.entries()) {
      if (node.in_trash) continue;

      if (req.tag_filter) {
        const tagNames = (node.supertags || []).map(t => t.tag_name.toLowerCase());
        if (!tagNames.some(tn => tn.includes(req.tag_filter!.toLowerCase()))) {
          continue;
        }
      }

      const sScore = vectorScores.get(id) || 0.0;
      const lScore = keywordScores.get(id) || 0.0;
      const baseScore = (alpha * sScore) + ((1.0 - alpha) * lScore);

      const tempScore = computeTemporalScore(node.effective_date, targetDate, dateFrom, dateTo);
      const relClass = classifyTemporalRelationship(node, targetDate, targetYear);

      let finalScore = baseScore;

      if (temporalMode === 'none') {
        finalScore = baseScore;
      } else if (temporalMode === 'boost') {
        finalScore = ((1.0 - temporalWeight) * baseScore) + (temporalWeight * tempScore);
      } else if (temporalMode === 'filter') {
        if (!node.effective_date || tempScore <= 0.0) continue;
        if (dateFrom && node.effective_date < dateFrom) continue;
        if (dateTo && node.effective_date > dateTo) continue;
        finalScore = baseScore;
      } else if (temporalMode === 'strict') {
        if (!node.effective_date || !['day_node', 'field', 'explicit_rel'].includes(node.date_source || '')) {
          continue;
        }
        if (relClass === 'conflicting' || relClass === 'retrospective') {
          continue;
        }
        if (targetYear && node.effective_date.substring(0, 4) !== targetYear) {
          continue;
        }
        if (dateFrom && node.effective_date < dateFrom) continue;
        if (dateTo && node.effective_date > dateTo) continue;
        finalScore = (baseScore * 0.70) + (tempScore * 0.30);
      }

      const [isBareCal] = isDayNode(node);
      if (isBareCal && !node.description && (!node.fields || node.fields.length === 0)) {
        finalScore *= 0.50;
      } else if (node.description || (node.fields && node.fields.length > 0)) {
        finalScore += 0.05;
      }

      scoredList.push({
        node,
        finalScore,
        semScore: sScore,
        lexScore: lScore,
        tempScore,
        relClass
      });
    }

    scoredList.sort((a, b) => b.finalScore - a.finalScore);
    const top = scoredList.slice(0, limit);

    const results: SearchResultItem[] = top.map(item => ({
      id: item.node.id,
      name: item.node.name,
      description: item.node.description || '',
      score: Math.round(item.finalScore * 10000) / 10000,
      semantic_score: Math.round(item.semScore * 10000) / 10000,
      lexical_score: Math.round(item.lexScore * 10000) / 10000,
      breadcrumbs: item.node.breadcrumbs || [],
      tags: (item.node.supertags || []).map(t => t.tag_name),
      parent_id: item.node.parent_id,
      last_updated: item.node.updated_at,
      effective_date: item.node.effective_date,
      date_source: item.node.date_source,
      date_source_node_id: item.node.date_source_node_id,
      calendar_path: item.node.calendar_path,
      ancestor_day_node_id: item.node.ancestor_day_node_id,
      ancestor_month_node_id: item.node.ancestor_month_node_id,
      ancestor_year_node_id: item.node.ancestor_year_node_id,
      temporal_relationship: item.relClass,
      temporal_score: Math.round(item.tempScore * 10000) / 10000
    }));

    const latencyMs = Date.now() - startT;
    const insufficient = results.length === 0 && temporalMode === 'strict' && targetDate !== undefined;

    return {
      query,
      total_hits: results.length,
      results,
      latency_ms: latencyMs,
      target_date: targetDate,
      temporal_mode: temporalMode,
      insufficient_evidence: insufficient
    };
  }

  /**
   * Performs hybrid search returning nodes.
   */
  async search(query: string, limit: number = 20, alpha: number = 0.50): Promise<TanaNode[]> {
    const res = await this.searchWithTemporal({ query, limit });
    return res.results.map(r => ({
      id: r.id,
      name: r.name,
      description: r.description,
      is_done: false,
      in_trash: false,
      supertags: r.tags.map(t => ({ tag_id: t, tag_name: t })),
      fields: [],
      children: [],
      references: [],
      backlinks: [],
      breadcrumbs: r.breadcrumbs,
      effective_date: r.effective_date,
      date_source: r.date_source,
      date_source_node_id: r.date_source_node_id,
      calendar_path: r.calendar_path,
      ancestor_day_node_id: r.ancestor_day_node_id,
      ancestor_month_node_id: r.ancestor_month_node_id,
      ancestor_year_node_id: r.ancestor_year_node_id
    }));
  }
}
