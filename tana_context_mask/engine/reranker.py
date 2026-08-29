import math
from datetime import datetime
from typing import List, Dict, Tuple, Any, Optional
from ..models.node import TanaNode

class TaskReranker:
    def __init__(self):
        pass

    def rerank(
        self,
        task_query: str,
        candidates: List[Tuple[TanaNode, float, str]],  # (node, base_score, inclusion_reason)
        max_nodes: int = 8
    ) -> List[Tuple[TanaNode, float, str]]:
        """
        Reranks expanded node candidates according to multi-factor relevance:
        - Base semantic/lexical similarity
        - Graph centrality and structural connectivity
        - Temporal recency decay
        - Tag importance and content completeness
        """
        if not candidates:
            return []

        all_candidate_ids = {node.id for node, _, _ in candidates}
        scored_candidates: List[Tuple[TanaNode, float, str]] = []

        now = datetime.now().timestamp()

        for node, base_score, reason in candidates:
            score = base_score

            # 1. Graph Connectivity Bonus
            # If the node's parent or children or references are also in the candidate set, it is part of a relevant cluster
            cluster_connections = 0
            if node.parent_id in all_candidate_ids:
                cluster_connections += 1
            for cid in node.children:
                if cid in all_candidate_ids:
                    cluster_connections += 1
            for rid in node.references:
                if rid in all_candidate_ids:
                    cluster_connections += 1

            if cluster_connections > 0:
                score += min(0.25, cluster_connections * 0.08)

            # 2. Recency Bonus
            if node.updated_at:
                try:
                    # ISO string parsing
                    updated_ts = datetime.fromisoformat(node.updated_at.replace("Z", "+00:00")).timestamp()
                    days_old = max(0, (now - updated_ts) / 86400.0)
                    # Exponential decay: e^(-days/180) -> up to 0.1 bonus for recent nodes
                    recency_boost = 0.10 * math.exp(-days_old / 180.0)
                    score += recency_boost
                except Exception:
                    pass

            # 3. Structural Significance Bonus (Supertags)
            tag_names = [t.tag_name.lower() for t in node.supertags]
            if any(t in tag_names for t in ["project", "goal", "task", "milestone", "meeting", "sprint"]):
                score += 0.08

            # 4. Penalise very short or trivial nodes without context
            if len(node.name.strip()) < 4 and not node.description and not node.fields:
                score *= 0.6

            scored_candidates.append((node, round(score, 4), reason))

        # Sort descending by composite score
        scored_candidates.sort(key=lambda x: x[1], reverse=True)

        # Deduplication: prune redundant leaf nodes if parent is already top-ranked with same content
        seen_names = set()
        deduped: List[Tuple[TanaNode, float, str]] = []
        for node, s, r in scored_candidates:
            norm_name = node.name.strip().lower()
            if norm_name in seen_names and len(norm_name) > 10:
                continue
            seen_names.add(norm_name)
            deduped.append((node, s, r))
            if len(deduped) >= max_nodes:
                break

        return deduped
