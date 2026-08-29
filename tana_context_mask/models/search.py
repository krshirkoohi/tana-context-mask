from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any

class SemanticSearchRequest(BaseModel):
    query: str
    limit: int = 25
    tag_filter: Optional[str] = None
    scope_node_id: Optional[str] = None
    hybrid: bool = True
    alpha: float = 0.7  # 1.0 = pure vector, 0.0 = pure lexical

class SearchResultItem(BaseModel):
    id: str
    name: str
    description: Optional[str] = ""
    score: float
    semantic_score: Optional[float] = None
    lexical_score: Optional[float] = None
    breadcrumbs: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    parent_id: Optional[str] = None
    last_updated: Optional[str] = None

class SearchResponse(BaseModel):
    query: str
    total_hits: int
    results: List[SearchResultItem]
    latency_ms: float
