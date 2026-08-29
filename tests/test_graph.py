import pytest
import tempfile
import os
from tana_context_mask.storage.db import SQLiteStore
from tana_context_mask.engine.graph_engine import GraphEngine
from tana_context_mask.models.node import TanaNode, NodeTag

@pytest.fixture
def graph_setup():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = SQLiteStore(db_path=path)
    
    # Hierarchy: Workspace -> Projects Area -> Tana Context Mask -> Subtask 1
    #                                                             -> Referenced Doc
    nodes = [
        TanaNode(id="root_ws", name="My Knowledge Workspace", parent_id=None),
        TanaNode(id="area_dev", name="Developer Area", parent_id="root_ws"),
        TanaNode(
            id="proj_tana",
            name="Tana Context Mask Project",
            parent_id="area_dev",
            supertags=[NodeTag(tag_id="t_proj", tag_name="Project")],
            children=["task_1", "task_2"],
            references=["doc_spec"]
        ),
        TanaNode(id="task_1", name="Build SQLite Graph Mirror", parent_id="proj_tana", is_done=True),
        TanaNode(id="task_2", name="Implement FastEmbed Vector Store", parent_id="proj_tana"),
        TanaNode(id="doc_spec", name="Context Mask Architecture Spec", parent_id="area_dev")
    ]
    db.bulk_upsert_nodes(nodes)
    
    engine = GraphEngine(db=db)
    yield db, engine
    
    if os.path.exists(path):
        os.remove(path)

def test_breadcrumbs_lineage(graph_setup):
    db, _ = graph_setup
    node = db.get_node("task_1")
    assert node is not None
    assert "My Knowledge Workspace" in node.breadcrumbs
    assert "Developer Area" in node.breadcrumbs
    assert "Tana Context Mask Project" in node.breadcrumbs

def test_graph_expansion(graph_setup):
    db, engine = graph_setup
    seed = db.get_node("proj_tana")
    assert seed is not None
    
    expanded = engine.expand_subgraph([seed], max_hops=1, max_expansion_nodes=10)
    expanded_ids = [n.id for n, _ in expanded]
    
    assert "proj_tana" in expanded_ids # Primary seed
    assert "area_dev" in expanded_ids  # Parent container
    assert "task_1" in expanded_ids    # Child subtask
    assert "task_2" in expanded_ids    # Child subtask
    assert "doc_spec" in expanded_ids  # Reference

def test_single_node_context(graph_setup):
    _, engine = graph_setup
    ctx = engine.get_full_node_context("proj_tana")
    assert ctx is not None
    assert len(ctx["children"]) == 2
    assert len(ctx["references"]) == 1
    assert "Project" in ctx["supertags"]
