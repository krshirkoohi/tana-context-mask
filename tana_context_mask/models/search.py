from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any

class SemanticSearchRequest(BaseModel):
    query: str
    limit: int = 25
    tag_filter: Optional[str] = None
    scope_node_id: Optional[str] = None
    hybrid: bool = True
    alpha: float = 0.7  # 1.0 = pure vector, 0.0 = pure lexical
    
    # Temporal Parameters
    target_date: Optional[str] = None  # Format: YYYY-MM-DD
    date_from: Optional[str] = None  # Format: YYYY-MM-DD
    date_to: Optional[str] = None  # Format: YYYY-MM-DD
    temporal_mode: str = "none"  # "none", "boost", "filter", "strict"
    temporal_weight: float = 0.40  # Weight for date proximity in 'boost' mode

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
    
    # Temporal Metadata & Provenance
    effective_date: Optional[str] = None
    date_source: Optional[str] = None
    date_source_node_id: Optional[str] = None
    calendar_path: Optional[str] = None
    ancestor_day_node_id: Optional[str] = None
    ancestor_month_node_id: Optional[str] = None
    ancestor_year_node_id: Optional[str] = None
    temporal_relationship: Optional[str] = None  # 'contemporaneous', 'retrospective', 'undated', 'conflicting'
    temporal_score: Optional[float] = None

class SearchResponse(BaseModel):
    query: str
    total_hits: int
    results: List[SearchResultItem]
    latency_ms: float
    target_date: Optional[str] = None
    temporal_mode: Optional[str] = None
    insufficient_evidence: bool = False
