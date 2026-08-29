import pytest
from fastapi.testclient import TestClient
from tana_context_mask.api.server import app
from tana_context_mask.storage.db import SQLiteStore
from tana_context_mask.storage.vector_store import VectorStore
from tana_context_mask.models.node import TanaNode, NodeTag

client = TestClient(app)

@pytest.fixture(autouse=True)
def seed_test_data():
    db = SQLiteStore()
    vstore = VectorStore()
    node = TanaNode(
        id="api_test_node",
        name="FastAPI Test Node for Context Mask",
        description="Verifies endpoint responses",
        supertags=[NodeTag(tag_id="t_test", tag_name="TestTag")]
    )
    db.upsert_node(node)
    vstore.upsert_vectors([{"id": node.id, "name": node.name, "description": node.description, "hash": node.content_hash}])

def test_root_and_health():
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.json()["status"] == "online"
    
    h_resp = client.get("/api/v1/health")
    assert h_resp.status_code == 200
    assert h_resp.json()["status"] == "healthy"

def test_acquire_context_endpoint():
    payload = {
        "task": "Test task for FastAPI",
        "max_nodes": 3
    }
    resp = client.post("/api/v1/context/acquire", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert "trace_id" in data
    assert "formatted_context_markdown" in data
    assert len(data["nodes"]) >= 1

def test_search_endpoint():
    payload = {
        "query": "FastAPI Test",
        "limit": 5
    }
    resp = client.post("/api/v1/search", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_hits"] >= 1
    assert any(r["id"] == "api_test_node" for r in data["results"])

def test_node_details_endpoint():
    resp = client.get("/api/v1/nodes/api_test_node")
    assert resp.status_code == 200
    data = resp.json()
    assert data["node"]["id"] == "api_test_node"
    assert "TestTag" in data["supertags"]

def test_openapi_json():
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    schema = resp.json()
    assert "openapi" in schema
    assert "/api/v1/context/acquire" in schema["paths"]
