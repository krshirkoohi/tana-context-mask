import { TanaNode, ScoredCandidate } from '../types';

export class EdgeReranker {
  /**
   * Multi-factor reranking based on semantic score, freshness, structural depth, and deduplication.
   */
  rerank(task: string, candidates: Array<{ node: TanaNode; score: number; reason: string }>, maxNodes: number = 8): ScoredCandidate[] {
    const taskTokens = new Set((task.toLowerCase().match(/\w+/g) || []).filter(t => t.length > 2));
    const now = Date.now();

    const scored: ScoredCandidate[] = [];

    for (const c of candidates) {
      let finalScore = c.score;

      // 1. Exact Lexical Overlap Boost
      const nodeTokens = new Set((c.node.name.toLowerCase().match(/\w+/g) || []));
      let overlapCount = 0;
      for (const t of taskTokens) {
        if (nodeTokens.has(t)) overlapCount++;
      }
      if (taskTokens.size > 0 && overlapCount > 0) {
        finalScore += Math.min(0.20, (overlapCount / taskTokens.size) * 0.25);
      }

      // 2. Freshness Boost (recent updates within last 30 days get small bump)
      if (c.node.updated_at) {
        try {
          const updatedTime = new Date(c.node.updated_at).getTime();
          const ageDays = (now - updatedTime) / (1000 * 60 * 60 * 24);
          if (ageDays < 30) {
            finalScore += 0.05 * (1.0 - ageDays / 30);
          }
        } catch {
          // Ignore parse errors
        }
      }

      // 3. Supertag relevance
      if (c.node.supertags && c.node.supertags.length > 0) {
        finalScore += 0.03;
      }

      scored.push({
        node: c.node,
        score: finalScore,
        reason: c.reason
      });
    }

    // Sort descending by score
    scored.sort((a, b) => b.score - a.score);

    // Context deduplication: prune exact identical name duplicates IF they share the same parent
    const seenSignatures = new Set<string>();
    const deduplicated: ScoredCandidate[] = [];

    for (const item of scored) {
      const sig = `${(item.node.parent_id || '')}::${item.node.name.trim().toLowerCase()}`;
      if (!seenSignatures.has(sig)) {
        seenSignatures.add(sig);
        deduplicated.push(item);
      }
      if (deduplicated.length >= maxNodes) break;
    }

    return deduplicated;
  }
}
