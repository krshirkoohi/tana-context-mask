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
      const cleanName = c.node.name.trim();

      // Penalize placeholder/empty template titles (e.g. "Title:", "Untitled") unless they have substantive descriptions
      if ((cleanName.toLowerCase() === 'title:' || cleanName.toLowerCase() === 'untitled' || cleanName.length < 3) && (!c.node.description || c.node.description.length < 10)) {
        finalScore -= 0.35;
      }

      // 1. Exact Lexical Overlap Boost (Node Name & Description)
      const nameAndDesc = `${cleanName} ${c.node.description || ''}`.toLowerCase();
      const nodeTokens = new Set((nameAndDesc.match(/\w+/g) || []));
      let nameOverlapCount = 0;
      for (const t of taskTokens) {
        if (nodeTokens.has(t)) nameOverlapCount++;
      }
      if (taskTokens.size > 0 && nameOverlapCount > 0) {
        const overlapRatio = nameOverlapCount / taskTokens.size;
        finalScore += Math.min(0.40, overlapRatio * 0.45);
      }

      // 2. High-Priority Field Name & Value Scoring
      // Fields define the core semantics and properties of typed nodes in Tana
      let fieldMatchCount = 0;
      const matchedFieldDetails: string[] = [];
      const hasFields = c.node.fields && c.node.fields.length > 0;

      if (hasFields) {
        for (const f of c.node.fields) {
          const fName = (f.field_name || '').toLowerCase();
          const fVal = (f.value_text || f.value_node_id || '').toLowerCase();
          const fieldTokens = new Set(`${fName} ${fVal}`.match(/\w+/g) || []);

          let fieldHits = 0;
          for (const t of taskTokens) {
            if (fieldTokens.has(t)) fieldHits++;
          }

          if (fieldHits > 0) {
            fieldMatchCount += fieldHits;
            matchedFieldDetails.push(`${f.field_name}: ${f.value_text || f.value_node_id}`);
          }
        }

        // Field semantic relevance boost (up to +0.30)
        if (taskTokens.size > 0 && fieldMatchCount > 0) {
          finalScore += Math.min(0.30, (fieldMatchCount / taskTokens.size) * 0.35);
        }

        // Schema Density Bonus: Typed nodes with populated fields get higher prominence over loose bullets
        finalScore += Math.min(0.08, c.node.fields.length * 0.02);
      }

      // 3. Freshness Boost (recent updates within last 30 days get small bump)
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

      // 4. Supertag relevance
      if (c.node.supertags && c.node.supertags.length > 0) {
        finalScore += 0.05;
      }

      let annotatedReason = c.reason;
      if (matchedFieldDetails.length > 0) {
        annotatedReason = `${c.reason} (Matched field: ${matchedFieldDetails.slice(0, 2).join(', ')})`;
      } else if (nameOverlapCount === taskTokens.size && taskTokens.size > 0) {
        annotatedReason = `High-confidence title & content match for '${task.slice(0, 40)}'`;
      }

      scored.push({
        node: c.node,
        score: finalScore,
        reason: annotatedReason
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
