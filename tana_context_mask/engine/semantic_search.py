import time
from datetime import datetime, date, timedelta
from typing import List, Dict, Optional, Tuple, Any
from ..config import settings
from ..storage.db import SQLiteStore
from ..storage.vector_store import VectorStore
from ..models.search import SemanticSearchRequest, SearchResultItem, SearchResponse
from ..models.node import TanaNode
from .temporal import (
    compute_temporal_score,
    classify_temporal_relationship,
    parse_temporal_intent,
    is_day_node
)

class SemanticSearchEngine:
    def __init__(self, db: Optional[SQLiteStore] = None, vector_store: Optional[VectorStore] = None):
        self.db = db or SQLiteStore()
        self.vector_store = vector_store or VectorStore()

    def search(self, request: SemanticSearchRequest) -> SearchResponse:
        start_t = time.time()
        query = request.query.strip()
        if not query:
            return SearchResponse(query=query, total_hits=0, results=[], latency_ms=0.0)

        limit = request.limit or settings.default_top_k
        alpha = request.alpha if request.alpha is not None else settings.hybrid_alpha

        # 0. Intent and Temporal Parameters Resolution
        target_date = request.target_date
        date_from = request.date_from
        date_to = request.date_to
        temporal_mode = request.temporal_mode or "none"
        temporal_weight = request.temporal_weight if request.temporal_weight is not None else 0.40

        # Automatic historical intent detection if not explicitly configured
        if temporal_mode == "none" and not target_date and not date_from and not date_to:
            intent = parse_temporal_intent(query)
            if intent["has_temporal_intent"] and intent["target_date"]:
                target_date = intent["target_date"]
                date_from = intent["date_from"]
                date_to = intent["date_to"]
                temporal_mode = intent["temporal_mode"]

        # 1. Candidate Generation
        candidate_nodes: Dict[str, TanaNode] = {}
        vector_scores: Dict[str, float] = {}
        lexical_scores: Dict[str, float] = {}

        # Strategy A: Calendar-First Candidate Generation for Strict/Filter Historical Queries
        if temporal_mode in ["strict", "filter"] and (target_date or date_from or date_to):
            # Define search window (default initial ±7 days around target_date if bounds not set)
            if target_date and (not date_from or not date_to):
                try:
                    t_d = datetime.strptime(target_date[:10], "%Y-%m-%d").date()
                    w_from = (t_d - timedelta(days=7)).isoformat()
                    w_to = (t_d + timedelta(days=7)).isoformat()
                except ValueError:
                    w_from = date_from or target_date
                    w_to = date_to or target_date
            else:
                w_from = date_from or target_date
                w_to = date_to or target_date

            # Initial window collection
            cal_nodes = self.db.get_nodes_by_date_range(w_from, w_to, limit=200)

            # Progressive window widening if coverage is sparse (±14d, ±30d, ±60d)
            if len(cal_nodes) < 3 and target_date:
                for widen_days in [14, 30, 60]:
                    try:
                        t_d = datetime.strptime(target_date[:10], "%Y-%m-%d").date()
                        w_from = (t_d - timedelta(days=widen_days)).isoformat()
                        w_to = (t_d + timedelta(days=widen_days)).isoformat()
                        cal_nodes = self.db.get_nodes_by_date_range(w_from, w_to, limit=200)
                        if len(cal_nodes) >= 3:
                            break
                    except ValueError:
                        break

            for cn in cal_nodes:
                candidate_nodes[cn.id] = cn

        # Strategy B: Global Semantic + Lexical Retrieval
        # Vector Semantic Search
        vector_hits = self.vector_store.search(query, limit=limit * 4)
        for h in vector_hits:
            vector_scores[h["id"]] = float(h["score"])

        # FTS Lexical Search
        fts_hits = self.db.fts_search(query, limit=limit * 4, tag_filter=request.tag_filter)
        for node, score in fts_hits:
            lexical_scores[node.id] = score
            if node.id not in candidate_nodes:
                candidate_nodes[node.id] = node

        # If we have calendar-first candidates, ensure their vector scores are computed
        query_vec = None
        for cid, cnode in candidate_nodes.items():
            if cid not in vector_scores:
                if query_vec is None:
                    query_vec = self.vector_store.embed_text(query)
                # Compute on-demand vector similarity
                c_vec = self.vector_store.embed_text(cnode.full_searchable_text or cnode.name)
                # Cosine similarity
                import numpy as np
                qv = np.array(query_vec, dtype=np.float32)
                cv = np.array(c_vec, dtype=np.float32)
                norm_q = np.linalg.norm(qv)
                norm_c = np.linalg.norm(cv)
                if norm_q > 0 and norm_c > 0:
                    sim = float(np.dot(qv, cv) / (norm_q * norm_c))
                    vector_scores[cid] = max(0.0, min(1.0, (sim + 1.0) / 2.0))
                else:
                    vector_scores[cid] = 0.0

        all_candidate_ids = set(candidate_nodes.keys()).union(set(vector_scores.keys())).union(set(lexical_scores.keys()))

        # Pre-fetch nodes from DB for any missing candidate IDs
        missing_ids = [cid for cid in all_candidate_ids if cid not in candidate_nodes]
        if missing_ids:
            hydrated = self.db.get_nodes(missing_ids)
            for h in hydrated:
                candidate_nodes[h.id] = h

        scored_candidates: List[Tuple[TanaNode, float, float, float, float, str]] = []
        # (node, final_score, base_score, sem_score, lex_score, rel_classification)

        target_year = target_date[:4] if target_date else (date_from[:4] if date_from else None)

        for cid in all_candidate_ids:
            node = candidate_nodes.get(cid)
            if not node or node.in_trash:
                continue

            # Tag filter check if requested
            if request.tag_filter:
                node_tag_names = [t.tag_name.lower() for t in node.supertags]
                if not any(request.tag_filter.lower() in tn for tn in node_tag_names):
                    continue

            s_score = vector_scores.get(cid, 0.0)
            l_score = lexical_scores.get(cid, 0.0)

            # Base hybrid score
            if request.hybrid:
                base_score = (alpha * s_score) + ((1.0 - alpha) * l_score)
            else:
                base_score = s_score

            # Temporal proximity score and relationship classification
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

            # Apply Temporal Mode Logic
            if temporal_mode == "none":
                final_score = base_score

            elif temporal_mode == "boost":
                # Multi-factor proximity fusion
                final_score = ((1.0 - temporal_weight) * base_score) + (temporal_weight * temp_score)

            elif temporal_mode == "filter":
                # Exclude candidates outside range or without date
                if not node.effective_date or temp_score <= 0.0:
                    continue
                if date_from and node.effective_date < date_from:
                    continue
                if date_to and node.effective_date > date_to:
                    continue
                final_score = base_score

            elif temporal_mode == "strict":
                # Invariant: Must have verified calendar/occurrence date in period
                # Weak fallbacks or undated/conflicting are rejected from strict evidence
                if not node.effective_date or node.date_source not in ["day_node", "field", "explicit_rel"]:
                    continue

                if rel_class == "conflicting":
                    continue

                # Wrong-year Day-node descendants talking about the target period are retrospective
                if rel_class == "retrospective":
                    continue

                if target_year and node.effective_date[:4] != target_year:
                    continue

                if date_from and node.effective_date < date_from:
                    continue
                if date_to and node.effective_date > date_to:
                    continue

                final_score = (base_score * 0.70) + (temp_score * 0.30)

            else:
                final_score = base_score

            # Bare calendar date containers get demoted relative to substantive content notes
            is_bare_cal, _ = is_day_node(node)
            if is_bare_cal and not node.description and not node.fields:
                final_score *= 0.50
            elif node.description or node.fields:
                final_score += 0.05

            scored_candidates.append((node, final_score, base_score, s_score, temp_score, rel_class))

        # Sort by final score descending
        scored_candidates.sort(key=lambda x: x[1], reverse=True)
        top_candidates = scored_candidates[:limit]

        results: List[SearchResultItem] = []
        for node, f_score, b_score, s_score, t_score, rel_class in top_candidates:
            results.append(SearchResultItem(
                id=node.id,
                name=node.name,
                description=node.description or "",
                score=round(f_score, 4),
                semantic_score=round(s_score, 4),
                lexical_score=round(b_score, 4),
                breadcrumbs=node.breadcrumbs,
                tags=[t.tag_name for t in node.supertags],
                parent_id=node.parent_id,
                last_updated=node.updated_at,
                effective_date=node.effective_date,
                date_source=node.date_source,
                date_source_node_id=node.date_source_node_id,
                calendar_path=node.calendar_path,
                ancestor_day_node_id=node.ancestor_day_node_id,
                ancestor_month_node_id=node.ancestor_month_node_id,
                ancestor_year_node_id=node.ancestor_year_node_id,
                temporal_relationship=rel_class,
                temporal_score=round(t_score, 4)
            ))

        latency_ms = round((time.time() - start_t) * 1000.0, 2)
        insufficient = (len(results) == 0 and temporal_mode == "strict" and target_date is not None)

        return SearchResponse(
            query=query,
            total_hits=len(results),
            results=results,
            latency_ms=latency_ms,
            target_date=target_date,
            temporal_mode=temporal_mode,
            insufficient_evidence=insufficient
        )
