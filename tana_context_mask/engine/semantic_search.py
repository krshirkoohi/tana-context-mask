import time
from typing import List, Dict, Optional, Tuple, Any
from ..config import settings
from ..storage.db import SQLiteStore
from ..storage.vector_store import VectorStore
from ..models.search import SemanticSearchRequest, SearchResultItem, SearchResponse
from ..models.node import TanaNode

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

        # 1. Vector Semantic Search
        vector_hits = self.vector_store.search(query, limit=limit * 2)
        vector_scores: Dict[str, float] = {h["id"]: float(h["score"]) for h in vector_hits}

        # 2. FTS Lexical Search
        fts_hits = self.db.fts_search(query, limit=limit * 2, tag_filter=request.tag_filter)
        lexical_scores: Dict[str, float] = {node.id: score for node, score in fts_hits}

        # 3. Combine Candidates
        all_candidate_ids = set(vector_scores.keys()).union(set(lexical_scores.keys()))
        combined_scores: List[Tuple[str, float, float, float]] = [] # (id, final_score, sem_score, lex_score)

        for cid in all_candidate_ids:
            s_score = vector_scores.get(cid, 0.0)
            l_score = lexical_scores.get(cid, 0.0)
            
            if request.hybrid:
                # Weighted hybrid combination
                final_score = (alpha * s_score) + ((1.0 - alpha) * l_score)
            else:
                final_score = s_score

            combined_scores.append((cid, final_score, s_score, l_score))

        # Sort by final score descending
        combined_scores.sort(key=lambda x: x[1], reverse=True)
        top_candidates = combined_scores[:limit]

        # 4. Hydrate full nodes and construct response items
        results: List[SearchResultItem] = []
        for cid, f_score, s_score, l_score in top_candidates:
            node = self.db.get_node(cid)
            if not node or node.in_trash:
                continue

            # Tag filter check if requested
            if request.tag_filter:
                node_tag_names = [t.tag_name.lower() for t in node.supertags]
                if not any(request.tag_filter.lower() in tn for tn in node_tag_names):
                    continue

            results.append(SearchResultItem(
                id=node.id,
                name=node.name,
                description=node.description or "",
                score=round(f_score, 4),
                semantic_score=round(s_score, 4),
                lexical_score=round(l_score, 4),
                breadcrumbs=node.breadcrumbs,
                tags=[t.tag_name for t in node.supertags],
                parent_id=node.parent_id,
                last_updated=node.updated_at
            ))

        latency_ms = round((time.time() - start_t) * 1000.0, 2)
        return SearchResponse(
            query=query,
            total_hits=len(results),
            results=results,
            latency_ms=latency_ms
        )
