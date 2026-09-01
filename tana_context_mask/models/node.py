from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any

class NodeTag(BaseModel):
    tag_id: str
    tag_name: str

class NodeField(BaseModel):
    field_id: str
    field_name: str
    value_text: Optional[str] = None
    value_node_id: Optional[str] = None

class NodeEdge(BaseModel):
    source_id: str
    target_id: str
    relation_type: str  # 'parent_child', 'reference', 'tag', 'field'
    attribute_id: Optional[str] = None
    attribute_name: Optional[str] = None

class TanaNode(BaseModel):
    id: str
    name: str = ""
    description: Optional[str] = ""
    doc_type: Optional[str] = None
    parent_id: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    content_hash: Optional[str] = None
    structure_hash: Optional[str] = None
    is_done: bool = False
    in_trash: bool = False
    
    # Associated graph attributes
    supertags: List[NodeTag] = Field(default_factory=list)
    fields: List[NodeField] = Field(default_factory=list)
    children: List[str] = Field(default_factory=list)
    references: List[str] = Field(default_factory=list)
    backlinks: List[str] = Field(default_factory=list)
    breadcrumbs: List[str] = Field(default_factory=list)

    # Calendar and Temporal Provenance
    effective_date: Optional[str] = None  # Format: YYYY-MM-DD
    date_source: Optional[str] = None  # 'day_node', 'field', 'explicit_rel', 'fallback_created', 'none'
    date_source_node_id: Optional[str] = None
    calendar_distance: Optional[int] = None
    calendar_path: Optional[str] = None
    ancestor_day_node_id: Optional[str] = None
    ancestor_week_node_id: Optional[str] = None
    ancestor_month_node_id: Optional[str] = None
    ancestor_year_node_id: Optional[str] = None

    @property
    def deep_link(self) -> str:
        return f"https://app.tana.inc/?nodeid={self.id}"

    @property
    def full_searchable_text(self) -> str:
        parts = [self.name]
        if self.description:
            parts.append(self.description)
        for f in self.fields:
            if f.value_text:
                parts.append(f"{f.field_name}: {f.value_text}")
        return "\n".join(parts).strip()
