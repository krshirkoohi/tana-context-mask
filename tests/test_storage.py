import pytest
import tempfile
import os
from tana_context_mask.storage.db import SQLiteStore
from tana_context_mask.storage.vector_store import VectorStore
from tana_context_mask.models.node import TanaNode, NodeTag, NodeField
from tana_context_mask.models.context import TraceRecord

@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    store = SQLiteStore(db_path=path)
    yield store
    if os.path.exists(path):
        os.remove(path)

@pytest.fixture
def temp_vector_store():
    temp_dir = tempfile.mkdtemp()
    store = VectorStore(db_path=temp_dir)
    yield store

def test_sqlite_node_upsert_and_retrieve(temp_db):
    node = TanaNode(
        id="node_123",
        name="Build Tana Context Mask",
        description="Core AI retrieval engine",
        parent_id="parent_root",
        supertags=[NodeTag(tag_id="tag_proj", tag_name="Project")],
        fields=[NodeField(field_id="f1", field_name="Status", value_text="Active")],
        children=["child_subtask_1"]
    )
    temp_db.upsert_node(node)
    
    retrieved = temp_db.get_node("node_123")
    assert retrieved is not None
    assert retrieved.id == "node_123"
    assert retrieved.name == "Build Tana Context Mask"
    assert retrieved.description == "Core AI retrieval engine"
    assert len(retrieved.supertags) == 1
    assert retrieved.supertags[0].tag_name == "Project"
    assert len(retrieved.fields) == 1
    assert retrieved.fields[0].field_name == "Status"
    assert retrieved.children == ["child_subtask_1"]

def test_fts5_search(temp_db):
    nodes = [
        TanaNode(id="n1", name="Quantum Physics Research", description="Notes on quantum entanglement"),
        TanaNode(id="n2", name="Groceries shopping list", description="Apples, milk, bread"),
        TanaNode(id="n3", name="AI Agent architecture for Tana", description="Using vector embeddings and graph")
    ]
    temp_db.bulk_upsert_nodes(nodes)
    
    hits = temp_db.fts_search("quantum", limit=5)
    assert len(hits) == 1
    assert hits[0][0].id == "n1"

    agent_hits = temp_db.fts_search("architecture agent", limit=5)
    assert len(agent_hits) >= 1
    assert agent_hits[0][0].id == "n3"

def test_content_hashing_stability():
    h1 = SQLiteStore.calculate_content_hash("My Project", "Description")
    h2 = SQLiteStore.calculate_content_hash("My Project", "Description")
    h3 = SQLiteStore.calculate_content_hash("My Project", "Different Description")
    assert h1 == h2
    assert h1 != h3

def test_trace_logging(temp_db):
    trace = TraceRecord(
        trace_id="tr_001",
        task="Test Task",
        inferred_query="test query",
        seed_nodes=[{"id": "n1", "name": "Node 1"}],
        expanded_nodes=[{"id": "n2", "name": "Node 2"}],
        reranked_nodes=[{"id": "n1", "name": "Node 1", "score": 0.95}],
        excluded_nodes=[],
        latency_ms=12.5,
        created_at="2026-08-29T12:00:00"
    )
    temp_db.save_trace(trace)
    saved = temp_db.get_trace("tr_001")
    assert saved is not None
    assert saved["task"] == "Test Task"
    assert len(saved["seed_nodes"]) == 1
    assert saved["latency_ms"] == 12.5

def test_vector_store_in_memory_fallback(temp_vector_store):
    records = [
        {"id": "v1", "name": "Quantum mechanics and wavefunctions", "description": "Schrodinger equation notes", "hash": "h1"},
        {"id": "v2", "name": "Baking sourdough bread at home", "description": "Flour, water, salt recipe", "hash": "h2"}
    ]
    temp_vector_store.upsert_vectors(records)
    
    search_res = temp_vector_store.search("quantum physics", limit=2)
    assert len(search_res) >= 1
    assert search_res[0]["id"] == "v1"
    assert search_res[0]["score"] > 0.5
