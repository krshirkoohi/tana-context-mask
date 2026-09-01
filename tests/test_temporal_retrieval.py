import pytest
import tempfile
import os
import shutil
from datetime import date
from tana_context_mask.storage.db import SQLiteStore
from tana_context_mask.storage.vector_store import VectorStore
from tana_context_mask.engine.semantic_search import SemanticSearchEngine
from tana_context_mask.engine.context_builder import ContextBuilder
from tana_context_mask.models.node import TanaNode, NodeTag, NodeField
from tana_context_mask.models.search import SemanticSearchRequest
from tana_context_mask.models.context import ContextAcquisitionRequest
from tana_context_mask.engine.temporal import (
    parse_iso_date,
    is_day_node,
    resolve_node_provenance,
    compute_temporal_score,
    classify_temporal_relationship,
    parse_temporal_intent
)

@pytest.fixture
def temporal_setup():
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)
    vec_dir = tempfile.mkdtemp()
    
    db = SQLiteStore(db_path=db_path)
    vector_store = VectorStore(db_path=vec_dir)
    
    # Create Day nodes and historical notes
    # 1. 2019 Day node and contemporaneous note
    day_2019 = TanaNode(
        id="day_2019_09_01",
        name="2019-09-01",
        supertags=[NodeTag(tag_id="tag_day", tag_name="Day")],
        effective_date="2019-09-01",
        date_source="day_node",
        date_source_node_id="day_2019_09_01"
    )
    note_2019 = TanaNode(
        id="note_2019_physics",
        name="Studying particle physics and quantum field theory",
        description="Reading Griffiths chapter 4 on electrodynamics at the university library",
        parent_id="day_2019_09_01",
        effective_date="2019-09-01",
        date_source="day_node",
        date_source_node_id="day_2019_09_01",
        ancestor_day_node_id="day_2019_09_01",
        ancestor_year_node_id="2019"
    )

    # 2. 2026 Day node and retrospective note
    day_2026 = TanaNode(
        id="day_2026_09_01",
        name="2026-09-01",
        supertags=[NodeTag(tag_id="tag_day", tag_name="Day")],
        effective_date="2026-09-01",
        date_source="day_node",
        date_source_node_id="day_2026_09_01"
    )
    note_2026_retro = TanaNode(
        id="note_2026_reflection",
        name="Reflecting on how my 2019 university physics years shaped my career",
        description="Looking back at September 2019 studying quantum field theory and physics",
        parent_id="day_2026_09_01",
        effective_date="2026-09-01",
        date_source="day_node",
        date_source_node_id="day_2026_09_01",
        ancestor_day_node_id="day_2026_09_01",
        ancestor_year_node_id="2026"
    )

    # 3. Conflicting note: 2026 parent Day node, but explicit field says 2019-09-01
    note_conflicting = TanaNode(
        id="note_conflict",
        name="Imported archive note about 2019 physics exam",
        description="Physics exam notes from 2019",
        parent_id="day_2026_09_01",
        effective_date="2026-09-01",
        date_source="day_node",
        date_source_node_id="day_2026_09_01",
        fields=[NodeField(field_id="f_date", field_name="Date", value_text="2019-09-01")]
    )

    all_nodes = [day_2019, note_2019, day_2026, note_2026_retro, note_conflicting]
    db.bulk_upsert_nodes(all_nodes)

    vector_store.upsert_vectors([
        {"id": n.id, "name": n.name, "description": n.description, "hash": n.content_hash}
        for n in all_nodes
    ])

    search_engine = SemanticSearchEngine(db=db, vector_store=vector_store)
    context_builder = ContextBuilder(db=db, vector_store=vector_store, search_engine=search_engine)

    yield db, vector_store, search_engine, context_builder

    if os.path.exists(db_path):
        os.remove(db_path)
    if os.path.exists(vec_dir):
        shutil.rmtree(vec_dir, ignore_errors=True)

def test_day_node_date_extraction():
    assert parse_iso_date("2019-09-01") == "2019-09-01"
    assert parse_iso_date("September 1, 2019") == "2019-09-01"
    assert parse_iso_date("1 Sep 2019") == "2019-09-01"
    assert parse_iso_date("1st September 2019") == "2019-09-01"
    assert parse_iso_date("Sun, Sep 1 2019") == "2019-09-01"

    d_node = TanaNode(id="d1", name="September 1, 2019", supertags=[NodeTag(tag_id="t", tag_name="Day")])
    is_day, d_str = is_day_node(d_node)
    assert is_day is True
    assert d_str == "2019-09-01"

def test_nested_descendant_inheritance():
    day_node = TanaNode(id="d_2019", name="2019-09-01", supertags=[NodeTag(tag_id="t", tag_name="Day")])
    parent_section = TanaNode(id="sec_1", name="Morning Routine", parent_id="d_2019")
    child_task = TanaNode(id="task_1", name="Review physics equations", parent_id="sec_1")

    prov = resolve_node_provenance(child_task, [parent_section, day_node])
    assert prov["effective_date"] == "2019-09-01"
    assert prov["date_source"] == "day_node"
    assert prov["date_source_node_id"] == "d_2019"
    assert prov["calendar_distance"] == 2
    assert prov["ancestor_year_node_id"] == "2019"

def test_conflicting_provenance_detection():
    day_node = TanaNode(id="d_2026", name="2026-09-01", supertags=[NodeTag(tag_id="t", tag_name="Day")])
    conflict_node = TanaNode(
        id="c1",
        name="Imported archive entry",
        parent_id="d_2026",
        effective_date="2026-09-01",
        date_source="day_node",
        fields=[NodeField(field_id="f1", field_name="Date", value_text="2019-09-01")]
    )
    rel = classify_temporal_relationship(conflict_node, target_date_str="2019-09-01")
    assert rel == "conflicting"

def test_retrospective_classification():
    retro_node = TanaNode(
        id="r1",
        name="Thinking about 2019 university days",
        description="Looking back at September 2019",
        effective_date="2026-09-01",
        date_source="day_node"
    )
    rel = classify_temporal_relationship(retro_node, target_date_str="2019-09-01")
    assert rel == "retrospective"

def test_temporal_intent_parser():
    intent = parse_temporal_intent("What was I doing this time in 2019?", reference_date=date(2026, 9, 1))
    assert intent["has_temporal_intent"] is True
    assert intent["target_date"] == "2019-09-01"
    assert intent["temporal_mode"] == "strict"

    intent_month = parse_temporal_intent("What happened in September 2018?")
    assert intent_month["has_temporal_intent"] is True
    assert intent_month["target_date"] == "2018-09-15"
    assert intent_month["date_from"] == "2018-09-01"
    assert intent_month["date_to"] == "2018-09-30"

def test_historical_particle_physics_regression(temporal_setup):
    """
    Test 1: 2019 Day node note 'Studying particle physics' must outrank
    2026 Day node note 'Reflecting on 2019 physics years' for the query:
    'What was I doing this time in 2019?'
    """
    _, _, engine, _ = temporal_setup

    req = SemanticSearchRequest(
        query="What was I doing this time in 2019?",
        target_date="2019-09-01",
        temporal_mode="strict",
        limit=5
    )
    resp = engine.search(req)

    assert resp.total_hits >= 1
    # Contemporaneous 2019 note must be the #1 result
    assert resp.results[0].id == "note_2019_physics"
    assert resp.results[0].temporal_relationship == "contemporaneous"
    assert resp.results[0].effective_date == "2019-09-01"

    # Retrospective 2026 note must NOT be present in strict results
    result_ids = [r.id for r in resp.results]
    assert "note_2026_reflection" not in result_ids
    assert "note_conflict" not in result_ids

def test_strict_temporal_gating_wrong_year_exclusion(temporal_setup):
    """
    Test 2: Wrong-year Day-node descendants must never pass strict historical retrieval.
    """
    _, _, engine, _ = temporal_setup

    req = SemanticSearchRequest(
        query="quantum field theory physics",
        target_date="2019-09-01",
        temporal_mode="strict",
        limit=5
    )
    resp = engine.search(req)

    result_ids = [r.id for r in resp.results]
    assert "note_2019_physics" in result_ids
    assert "note_2026_reflection" not in result_ids

def test_boost_mode_scoring(temporal_setup):
    """
    Test 3: Boost mode allows cross-year results but boosts in-period notes.
    """
    _, _, engine, _ = temporal_setup

    req = SemanticSearchRequest(
        query="quantum field theory physics",
        target_date="2019-09-01",
        temporal_mode="boost",
        limit=5
    )
    resp = engine.search(req)

    assert resp.total_hits >= 2
    # 2019 contemporaneous note is boosted above 2026 note
    assert resp.results[0].id == "note_2019_physics"
    assert resp.results[0].temporal_score >= 0.95

def test_sparse_historical_data_insufficient_evidence(temporal_setup):
    """
    Test 4: Sparse historical data must return insufficient evidence rather than recent thematic matches.
    """
    _, _, engine, _ = temporal_setup

    req = SemanticSearchRequest(
        query="What was I doing this time in 2015?",
        target_date="2015-09-01",
        temporal_mode="strict",
        limit=5
    )
    resp = engine.search(req)

    assert resp.insufficient_evidence is True
    assert len(resp.results) == 0

def test_context_builder_packet_provenance(temporal_setup):
    """
    Test 5: Context builder outputs formatted markdown with contemporaneous badges.
    """
    _, _, _, context_builder = temporal_setup

    req = ContextAcquisitionRequest(
        task="What was I doing this time in 2019?",
        target_date="2019-09-01",
        temporal_mode="strict"
    )
    packet = context_builder.acquire_context(req)

    assert len(packet.nodes) >= 1
    assert packet.nodes[0].id == "note_2019_physics"
    assert packet.nodes[0].temporal_relationship == "contemporaneous"
    assert "Contemporaneous" in packet.formatted_context_markdown
    assert "2019-09-01" in packet.formatted_context_markdown
