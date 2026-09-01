import math
from datetime import datetime
from typing import List, Dict, Tuple, Any, Optional
from ..models.node import TanaNode
from .temporal import compute_temporal_score, classify_temporal_relationship, is_day_node

class TaskReranker:
    def __init__(self):
        pass

    def rerank(
        self,
        task_query: str,
        candidates: List[Tuple[TanaNode, float, str]],  # (node, base_score, inclusion_reason)
        max_nodes: int = 8,
        target_date: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        temporal_mode: str = "none"
    ) -> List[Tuple[TanaNode, float, str]]:
        """
        Reranks expanded node candidates according to multi-factor relevance:
        - Base semantic/lexical similarity
        - Graph centrality and structural connectivity
        - Temporal proximity (when target_date / history mode active) or recency decay (general queries)
        - Tag importance and content completeness
        """
        if not candidates:
            return []

        all_candidate_ids = {node.id for node, _, _ in candidates}
        scored_candidates: List[Tuple[TanaNode, float, str]] = []

        now = datetime.now().timestamp()
        target_year = target_date[:4] if target_date else (date_from[:4] if date_from else None)

        for node, base_score, reason in candidates:
            score = base_score

            # 1. Graph Connectivity Bonus
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

            # 2. Temporal Handling
            if target_date or (temporal_mode in ["strict", "boost", "filter"]):
                temp_score = compute_temporal_score(
                    node_date_str=node.effective_date,
                    target_date_str=target_date,
                    date_from_str=date_from,
                    date_to_str=date_to
                )
                rel_class = classify_temporal_relationship(
                    node=node,
                    target_date_str=target_date,
                    target_year=target_year
                )

                if temporal_mode == "strict":
                    if rel_class == "contemporaneous":
                        score += 0.30 + (temp_score * 0.20)
                    elif rel_class == "retrospective":
                        score -= 0.50  # Strong demotion so contemporary evidence dominates
                    elif rel_class == "conflicting":
                        score -= 0.60
                    else:
                        score -= 0.40
                elif temporal_mode == "boost":
                    score = (score * 0.60) + (temp_score * 0.40)
            else:
                # General query: Recent updates get small bonus
                if node.updated_at:
                    try:
                        updated_ts = datetime.fromisoformat(node.updated_at.replace("Z", "+00:00")).timestamp()
                        days_old = max(0, (now - updated_ts) / 86400.0)
                        recency_boost = 0.10 * math.exp(-days_old / 180.0)
                        score += recency_boost
                    except Exception:
                        pass

            # 3. Structural Significance Bonus (Supertags)
            tag_names = [t.tag_name.lower() for t in node.supertags]
            if any(t in tag_names for t in ["project", "goal", "task", "milestone", "meeting", "sprint"]):
                score += 0.08

            # 4. Bare calendar container demotion & penalty for trivial nodes
            is_bare_cal, _ = is_day_node(node)
            if is_bare_cal and not node.description and not node.fields:
                score *= 0.40
            elif len(node.name.strip()) < 4 and not node.description and not node.fields:
                score *= 0.60

            scored_candidates.append((node, round(score, 4), reason))

        # Sort descending by composite score
        scored_candidates.sort(key=lambda x: x[1], reverse=True)

        # Deduplication
        seen_keys = set()
        deduped: List[Tuple[TanaNode, float, str]] = []
        for node, s, r in scored_candidates:
            norm_key = (node.parent_id or "", node.name.strip().lower())
            if norm_key in seen_keys and len(node.name.strip()) > 10:
                continue
            seen_keys.add(norm_key)
            deduped.append((node, s, r))
            if len(deduped) >= max_nodes:
                break

        return deduped
