from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any

class ContextAcquisitionRequest(BaseModel):
    task: str
    query: Optional[str] = None
    scope: Optional[str] = None
    max_nodes: int = 8
    include_children: bool = True
    include_references: bool = True

class ContextNode(BaseModel):
    id: str
    name: str
    description: Optional[str] = ""
    supertags: List[str] = Field(default_factory=list)
    fields: Dict[str, Any] = Field(default_factory=dict)
    breadcrumbs: List[str] = Field(default_factory=list)
    children_snippets: List[str] = Field(default_factory=list)
    references: List[str] = Field(default_factory=list)
    backlinks: List[str] = Field(default_factory=list)
    inclusion_reason: str = ""
    relevance_score: float = 0.0
    deep_link: str = ""

class ContextPacket(BaseModel):
    trace_id: str
    task: str
    summary: str
    formatted_context_markdown: str
    nodes: List[ContextNode]
    total_candidates_examined: int
    graph_expansion_count: int
    latency_ms: float
    timestamp: str

class TraceRecord(BaseModel):
    trace_id: str
    task: str
    inferred_query: str
    seed_nodes: List[Dict[str, Any]]
    expanded_nodes: List[Dict[str, Any]]
    reranked_nodes: List[Dict[str, Any]]
    excluded_nodes: List[Dict[str, Any]]
    latency_ms: float
    created_at: str
