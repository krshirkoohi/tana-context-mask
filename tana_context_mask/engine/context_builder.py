import time
import uuid
import re
from datetime import datetime
from typing import Optional, List, Dict, Any, Tuple
from ..config import settings
from ..storage.db import SQLiteStore
from ..storage.vector_store import VectorStore
from ..models.node import TanaNode
from ..models.context import ContextAcquisitionRequest, ContextPacket, ContextNode, TraceRecord
from ..models.search import SemanticSearchRequest
from .semantic_search import SemanticSearchEngine
from .graph_engine import GraphEngine
from .reranker import TaskReranker
from .temporal import parse_temporal_intent, classify_temporal_relationship

class ContextBuilder:
    def __init__(
        self,
        db: Optional[SQLiteStore] = None,
        vector_store: Optional[VectorStore] = None,
        search_engine: Optional[SemanticSearchEngine] = None,
        graph_engine: Optional[GraphEngine] = None,
        reranker: Optional[TaskReranker] = None
    ):
        self.db = db or SQLiteStore()
        self.vector_store = vector_store or VectorStore()
        self.search_engine = search_engine or SemanticSearchEngine(self.db, self.vector_store)
        self.graph_engine = graph_engine or GraphEngine(self.db)
        self.reranker = reranker or TaskReranker()

    def acquire_context(self, request: ContextAcquisitionRequest) -> ContextPacket:
        start_t = time.time()
        trace_id = str(uuid.uuid4())
        task_text = request.task.strip()
        search_query = request.query.strip() if request.query else task_text

        # 0. Intent and Temporal Parameters
        target_date = request.target_date
        date_from = request.date_from
        date_to = request.date_to
        temporal_mode = request.temporal_mode or "none"

        if temporal_mode == "none" and not target_date and not date_from and not date_to:
            intent = parse_temporal_intent(search_query)
            if intent["has_temporal_intent"] and intent["target_date"]:
                target_date = intent["target_date"]
                date_from = intent["date_from"]
                date_to = intent["date_to"]
                temporal_mode = intent["temporal_mode"]

        # 1. Whole-Workspace Semantic Discovery with Temporal Gating
        search_req = SemanticSearchRequest(
            query=search_query,
            limit=25,
            tag_filter=request.scope,
            hybrid=True,
            alpha=settings.hybrid_alpha,
            target_date=target_date,
            date_from=date_from,
            date_to=date_to,
            temporal_mode=temporal_mode
        )
        search_resp = self.search_engine.search(search_req)
        
        seed_nodes: List[TanaNode] = []
        seed_scores: Dict[str, float] = {}
        
        result_ids = [item.id for item in search_resp.results]
        nodes = self.db.get_nodes(result_ids)
        node_map = {n.id: n for n in nodes}
        
        for item in search_resp.results:
            node = node_map.get(item.id)
            if node:
                seed_nodes.append(node)
                seed_scores[node.id] = item.score

        # 2. Graph Expansion Around Semantic Hits
        expanded_pairs: List[Tuple[TanaNode, str]] = self.graph_engine.expand_subgraph(
            seed_nodes=seed_nodes,
            max_hops=settings.max_expansion_hops,
            max_expansion_nodes=15
        )

        # 3. Form Candidate Pool with Scores
        candidate_pool: List[Tuple[TanaNode, float, str]] = []
        for node, reason in expanded_pairs:
            base_s = seed_scores.get(node.id, 0.40)
            candidate_pool.append((node, base_s, reason))

        # 4. Multi-Factor Reranking & Deduplication with Temporal Anchoring
        max_nodes = request.max_nodes or settings.default_max_context_nodes
        ranked_candidates = self.reranker.rerank(
            task_query=task_text,
            candidates=candidate_pool,
            max_nodes=max_nodes,
            target_date=target_date,
            date_from=date_from,
            date_to=date_to,
            temporal_mode=temporal_mode
        )

        # 5. Build Formatted Context Nodes & Markdown Packet
        context_nodes: List[ContextNode] = []
        md_sections: List[str] = []

        md_sections.append(f"### 🌐 Tana Context Mask: Retrieved Knowledge Graph")
        md_sections.append(f"> **Task:** {task_text}")
        if target_date:
            md_sections.append(f"> **Target Date:** `{target_date}` | **Temporal Mode:** `{temporal_mode}`")
        md_sections.append(f"> **Trace ID:** `{trace_id}` | **Nodes Selected:** {len(ranked_candidates)} of {len(candidate_pool)} evaluated\n")

        if search_resp.insufficient_evidence:
            md_sections.append("> ⚠️ **Notice:** Insufficient contemporaneous evidence found in Tana for the requested historical period.\n")

        # Group nodes by temporal classification if temporal intent active
        target_year = target_date[:4] if target_date else None

        for idx, (node, score, reason) in enumerate(ranked_candidates, start=1):
            fields_dict = {f.field_name: (f.value_text or f.value_node_id) for f in node.fields}
            
            child_snippets: List[str] = []
            if request.include_children and node.children:
                child_nodes = self.db.get_children(node.id, limit=6)
                for c in child_nodes:
                    clean_cname = re.sub(r'<[^>]+>', '', c.name).strip()
                    if clean_cname:
                        done_mark = " [x]" if c.is_done else ""
                        child_snippets.append(f"{clean_cname}{done_mark}")

            tags_list = [t.tag_name for t in node.supertags]

            rel_class = classify_temporal_relationship(
                node=node,
                target_date_str=target_date,
                target_year=target_year
            )

            ctx_node = ContextNode(
                id=node.id,
                name=node.name,
                description=node.description or "",
                supertags=tags_list,
                fields=fields_dict,
                breadcrumbs=node.breadcrumbs,
                children_snippets=child_snippets,
                references=node.references,
                backlinks=node.backlinks,
                inclusion_reason=reason,
                relevance_score=score,
                deep_link=node.deep_link,
                effective_date=node.effective_date,
                date_source=node.date_source,
                temporal_relationship=rel_class
            )
            context_nodes.append(ctx_node)

            # Build Markdown item
            tags_badge = " ".join([f"`#{t}`" for t in tags_list]) if tags_list else ""
            crumb_path = " › ".join(node.breadcrumbs) if node.breadcrumbs else "Workspace Root"
            
            # Temporal badge
            rel_badge = ""
            if rel_class == "contemporaneous":
                rel_badge = f" `[Contemporaneous · {node.effective_date or 'verified'}]`"
            elif rel_class == "retrospective":
                rel_badge = f" `[Retrospective · recorded {node.effective_date or 'later'}]`"
            elif rel_class == "conflicting":
                rel_badge = " `[Conflicting Date]`"

            md_item = []
            md_item.append(f"#### {idx}. [{node.name}]({node.deep_link}) {tags_badge}{rel_badge}")
            md_item.append(f"- **Tana ID:** `tana:{node.id}` | **Relevance:** {score:.2f}")
            md_item.append(f"- **Path:** `{crumb_path}`")
            md_item.append(f"- **Why included:** {reason}")
            if node.effective_date:
                md_item.append(f"- **Calendar Provenance:** `{node.effective_date}` (source: `{node.date_source}`)")
            
            if node.description:
                md_item.append(f"- **Description:** {node.description}")

            if fields_dict:
                f_formatted = ", ".join(f"`{k}`: {v}" for k, v in fields_dict.items() if v)
                if f_formatted:
                    md_item.append(f"- **Fields:** {f_formatted}")

            if child_snippets:
                md_item.append("- **Children / Sub-items:**")
                for cs in child_snippets:
                    md_item.append(f"  - {cs}")

            md_sections.append("\n".join(md_item))

        total_latency_ms = round((time.time() - start_t) * 1000.0, 2)
        formatted_md = "\n\n".join(md_sections)

        # 6. Record Trace
        trace_record = TraceRecord(
            trace_id=trace_id,
            task=task_text,
            inferred_query=search_query,
            seed_nodes=[{"id": s.id, "name": s.name, "score": seed_scores.get(s.id, 0.0)} for s in seed_nodes],
            expanded_nodes=[{"id": n.id, "name": n.name, "reason": r} for n, r in expanded_pairs],
            reranked_nodes=[{"id": n.id, "name": n.name, "score": s, "reason": r} for n, s, r in ranked_candidates],
            excluded_nodes=[{"id": n.id, "name": n.name} for n, _, _ in candidate_pool if n.id not in {rc.id for rc, _, _ in ranked_candidates}],
            latency_ms=total_latency_ms,
            created_at=datetime.now().isoformat()
        )
        self.db.save_trace(trace_record)

        summary_text = f"Acquired {len(context_nodes)} contextual nodes across Tana hierarchy with latency {total_latency_ms}ms."

        return ContextPacket(
            trace_id=trace_id,
            task=task_text,
            summary=summary_text,
            formatted_context_markdown=formatted_md,
            nodes=context_nodes,
            total_candidates_examined=len(candidate_pool),
            graph_expansion_count=len(expanded_pairs) - len(seed_nodes),
            latency_ms=total_latency_ms,
            timestamp=datetime.now().isoformat(),
            target_date=target_date,
            temporal_mode=temporal_mode,
            insufficient_evidence=search_resp.insufficient_evidence
        )
