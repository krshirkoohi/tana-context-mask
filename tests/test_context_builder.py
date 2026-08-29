import pytest
import tempfile
import os
from tana_context_mask.storage.db import SQLiteStore
from tana_context_mask.storage.vector_store import VectorStore
from tana_context_mask.engine.context_builder import ContextBuilder
from tana_context_mask.models.node import TanaNode, NodeTag, NodeField
from tana_context_mask.models.context import ContextAcquisitionRequest

@pytest.fixture
def context_builder_setup():
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)
    vec_dir = tempfile.mkdtemp()
    
    db = SQLiteStore(db_path=db_path)
    vector_store = VectorStore(db_path=vec_dir)
    
    parent = TanaNode(id="p_hub", name="APOPHIS Core Intelligence Hub")
    child_1 = TanaNode(
        id="c_mask",
        name="Tana Context Mask Architecture",
        description="Graph-aware semantic acquisition layer for LLMs",
        parent_id="p_hub",
        supertags=[NodeTag(tag_id="t_app", tag_name="App")],
        fields=[NodeField(field_id="f_status", field_name="Stage", value_text="Building")]
    )
    child_2 = TanaNode(
        id="c_sync",
        name="Mirror Sync Engine",
        description="Incremental crawl and export loader",
        parent_id="c_mask"
    )
    
    nodes = [parent, child_1, child_2]
    db.bulk_upsert_nodes(nodes)
    vector_store.upsert_vectors([
        {"id": n.id, "name": n.name, "description": n.description, "hash": n.content_hash}
        for n in nodes
    ])
    
    builder = ContextBuilder(db=db, vector_store=vector_store)
    yield db, builder
    
    if os.path.exists(db_path):
        os.remove(db_path)

def test_acquire_context_packet(context_builder_setup):
    _, builder = context_builder_setup
    
    req = ContextAcquisitionRequest(
        task="Explain how Tana Context Mask handles semantic search and mirror sync",
        max_nodes=5
    )
    packet = builder.acquire_context(req)
    
    assert packet.trace_id is not None
    assert len(packet.nodes) >= 1
    assert "Tana Context Mask: Retrieved Knowledge Graph" in packet.formatted_context_markdown
    assert "tana:c_mask" in packet.formatted_context_markdown
    assert packet.latency_ms >= 0.0
