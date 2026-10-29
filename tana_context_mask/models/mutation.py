from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any

class MutationRequest(BaseModel):
    action: str  # 'create_node', 'set_field', 'add_tag', 'edit_name'
    target_node_id: Optional[str] = None
    parent_node_id: Optional[str] = None
    name: Optional[str] = None
    old_name: Optional[str] = None
    content: Optional[str] = None
    tag_id: Optional[str] = None
    tag_name: Optional[str] = None
    supertags: List[str] = Field(default_factory=list)
    children: List[str] = Field(default_factory=list)
    field_id: Optional[str] = None
    field_name: Optional[str] = None
    field_value: Optional[str] = None
    dry_run: bool = False
    idempotency_key: Optional[str] = None

class MutationResult(BaseModel):
    success: bool
    action: str
    affected_node_id: Optional[str] = None
    message: str
    audit_id: str
    timestamp: str
    dry_run: bool = False
    idempotent_replay: bool = False
    preview_payload: Optional[Dict[str, Any]] = None
