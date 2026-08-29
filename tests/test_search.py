import pytest
import tempfile
import os
from tana_context_mask.storage.db import SQLiteStore
from tana_context_mask.storage.vector_store import VectorStore
from tana_context_mask.engine.semantic_search import SemanticSearchEngine
from tana_context_mask.models.node import TanaNode, NodeTag
from tana_context_mask.models.search import SemanticSearchRequest

@pytest.fixture
def search_setup():
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)
    vec_dir = tempfile.mkdtemp()
    
    db = SQLiteStore(db_path=db_path)
    vector_store = VectorStore(db_path=vec_dir)
    
    nodes = [
        TanaNode(
            id="n_ai",
            name="Personal Context Engine for LLMs",
            description="A structured memory and semantic search layer for ChatGPT",
            supertags=[NodeTag(tag_id="t1", tag_name="Project")]
        ),
        TanaNode(
            id="n_cooking",
            name="Sourdough Pizza Recipe",
            description="Fermentation process and baking instructions at 250C",
            supertags=[NodeTag(tag_id="t2", tag_name="Recipe")]
        ),
        TanaNode(
            id="n_workout",
            name="Weekly Push Pull Legs Workout Routine",
            description="Upper body hypertrophy and strength training",
            supertags=[NodeTag(tag_id="t3", tag_name="Fitness")]
        )
    ]
    db.bulk_upsert_nodes(nodes)
    
    vector_store.upsert_vectors([
        {"id": n.id, "name": n.name, "description": n.description, "hash": n.content_hash}
        for n in nodes
    ])
    
    engine = SemanticSearchEngine(db=db, vector_store=vector_store)
    yield db, vector_store, engine
    
    if os.path.exists(db_path):
        os.remove(db_path)

def test_semantic_query_retrieval(search_setup):
    _, _, engine = search_setup
    
    req = SemanticSearchRequest(query="AI memory layer for ChatGPT", limit=3)
    resp = engine.search(req)
    
    assert resp.total_hits >= 1
    assert resp.results[0].id == "n_ai"
    assert resp.results[0].score > 0.4

def test_hybrid_search_tag_filter(search_setup):
    _, _, engine = search_setup
    
    req = SemanticSearchRequest(query="routine", limit=3, tag_filter="Fitness")
    resp = engine.search(req)
    
    assert len(resp.results) == 1
    assert resp.results[0].id == "n_workout"
