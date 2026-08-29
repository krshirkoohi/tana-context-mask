from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any

class MutationRequest(BaseModel):
    action: str  # 'create_node', 'set_field', 'add_tag', 'edit_name'
    target_node_id: Optional[str] = None
    parent_node_id: Optional[str] = None
    name: Optional[str] = None
    content: Optional[str] = None
    tag_id: Optional[str] = None
    field_id: Optional[str] = None
    field_value: Optional[str] = None
    idempotency_key: Optional[str] = None

class MutationResult(BaseModel):
    success: bool
    action: str
    affected_node_id: Optional[str] = None
    message: str
    audit_id: str
    timestamp: str
