import json
from pathlib import Path
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from tana_context_mask.config import settings, resolve_secret_from_bitwarden
from tana_context_mask.storage.db import SQLiteStore
from tana_context_mask.storage.vector_store import VectorStore
from tana_context_mask.sync.mirror_engine import MirrorEngine
from tana_context_mask.engine.semantic_search import SemanticSearchEngine
from tana_context_mask.engine.graph_engine import GraphEngine
from tana_context_mask.engine.context_builder import ContextBuilder
from tana_context_mask.models.search import SemanticSearchRequest
from tana_context_mask.models.context import ContextAcquisitionRequest
from tana_context_mask.models.mutation import MutationRequest
from tana_context_mask.models.node import TanaNode, NodeTag
from tana_context_mask.api.server import app
from tana_context_mask.api.mcp_server import MCPServer


def _auth_headers() -> dict:
    if settings.api_key:
        return {"X-API-Key": settings.api_key}
    return {}


class DummyVectorStore(VectorStore):
    """Deterministic fast in-memory vector store for end-to-end sync validation tests."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.model_name = "deterministic-test"
        self.table_name = "tana_nodes"
        self._table = None
        self._db = None
        self._model = None
        self._in_memory_vectors = {}
        self.embed_call_count = 0

    def _get_table(self):
        return None

    def embed_text(self, text: str):
        self.embed_call_count += 1
        vec = [0.0] * 16
        for idx, ch in enumerate(text.lower().encode("utf-8")):
            vec[idx % 16] += float(ch) / 255.0
        norm = sum(x * x for x in vec) ** 0.5 or 1.0
        return [x / norm for x in vec]

    def embed_batch(self, texts, batch_size: int = 128):
        return [self.embed_text(t) for t in texts]

    def upsert_vectors(self, records):
        if not records:
            return
        for r in records:
            vec = r.get("vector")
            if vec is None:
                txt = f"{r.get('name', '')}\n{r.get('description', '')}".strip() or "empty"
                vec = self.embed_text(txt)
            self._in_memory_vectors[str(r["id"])] = {
                "vector": vec,
                "id": str(r["id"]),
                "name": str(r.get("name", "")),
                "description": str(r.get("description", "")),
                "hash": str(r.get("hash", "")),
            }

    def count_vectors(self) -> int:
        return len(self._in_memory_vectors)


def test_bitwarden_zero_trust_secret_resolution(tmp_path: Path):
    """Issue #9: Verify TANA_TOKEN resolution from secrets.env and Bitwarden CLI."""
    secrets_file = tmp_path / "secrets.env"
    secrets_file.write_text("# Comment\nexport TANA_TOKEN='bw_secret_token_123'\n", encoding="utf-8")
    resolved = resolve_secret_from_bitwarden("TANA_TOKEN", env_Fallback_file=secrets_file)
    assert resolved == "bw_secret_token_123"

    missing_file = tmp_path / "nonexistent.env"
    with patch.dict("os.environ", {"BW_SESSION": "mock_session"}, clear=False):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="bw_cli_vault_token_456\n")
            resolved_cli = resolve_secret_from_bitwarden("TANA_TOKEN", env_Fallback_file=missing_file)
            assert resolved_cli == "bw_cli_vault_token_456"


def test_safe_mutation_proxy_and_idempotency(tmp_path: Path):
    """Issue #7: Verify dry-run validation, Tana formatting guardrails, and idempotency keys."""
    db = SQLiteStore(str(tmp_path / "mut.db"))
    vs = DummyVectorStore(str(tmp_path / "vec"))
    mock_client = MagicMock()
    mock_client.import_tana_paste.return_value = True
    engine = MirrorEngine(db=db, vector_store=vs, client=mock_client)

    # 1. Dry-run create_node does not call TanaMCPClient
    dry_req = MutationRequest(
        action="create_node",
        parent_node_id="INBOX",
        name="Quarterly Architecture Review",
        content="Verify D1 query batching",
        supertags=["Task"],
        dry_run=True,
    )
    dry_res = engine.execute_mutation(dry_req)
    assert dry_res.success is True
    assert dry_res.dry_run is True
    assert "- Quarterly Architecture Review #Task" in dry_res.preview_payload["content"]
    mock_client.import_tana_paste.assert_not_called()

    # 2. Live execution with idempotency_key
    live_req = MutationRequest(
        action="create_node",
        target_node_id="node_arch_1",
        parent_node_id="INBOX",
        name="Quarterly Architecture Review",
        content="Verify D1 query batching",
        supertags=["Task"],
        dry_run=False,
        idempotency_key="idem-key-001",
    )
    live_res1 = engine.execute_mutation(live_req)
    assert live_res1.success is True
    assert live_res1.idempotent_replay is False
    assert db.get_node("node_arch_1") is not None

    live_res2 = engine.execute_mutation(live_req)
    assert live_res2.idempotent_replay is True
    assert live_res2.audit_id == live_res1.audit_id
    assert mock_client.import_tana_paste.call_count == 1

    # 3. Guardrails reject #Gemini_CLI and Markdown in node names
    try:
        engine.execute_mutation(
            MutationRequest(action="create_node", name="Test", supertags=["Gemini_CLI"])
        )
        assert False, "Expected ValueError for #Gemini_CLI"
    except ValueError as exc:
        assert "Gemini_CLI" in str(exc)

    try:
        engine.execute_mutation(
            MutationRequest(action="create_node", name="**Bold Markdown Title**")
        )
        assert False, "Expected ValueError for Markdown in node name"
    except ValueError as exc:
        assert "plain text" in str(exc)


def test_mass_background_indexer_cpu_throttling_and_resume(tmp_path: Path):
    """Issue #13: Verify CPU load throttling and resumable batch indexing."""
    db = SQLiteStore(str(tmp_path / "daemon.db"))
    vs = DummyVectorStore(str(tmp_path / "vec"))
    engine = MirrorEngine(db=db, vector_store=vs, client=MagicMock())

    nodes = [
        TanaNode(id=f"hist_{i:03d}", name=f"Historical Research Note {i}", description=f"Content {i}")
        for i in range(10)
    ]
    db.bulk_upsert_nodes(nodes)

    # 1. High CPU (85% > 70%) throttles execution
    throttled_res = engine.run_mass_background_indexer(
        batch_size=4,
        cpu_threshold=70.0,
        min_sleep_sec=0.0,
        max_batches=2,
        cpu_load_fn=lambda: 85.0,
    )
    assert throttled_res["indexed_nodes"] == 0
    assert throttled_res["throttled_cycles"] == 2
    assert "throttled_cpu" in throttled_res["status"]

    # 2. Idle CPU (25% <= 70%) indexes 1 batch of 4 nodes and saves offset=4
    batch1_res = engine.run_mass_background_indexer(
        batch_size=4,
        cpu_threshold=70.0,
        min_sleep_sec=0.0,
        max_batches=1,
        cpu_load_fn=lambda: 25.0,
    )
    assert batch1_res["indexed_nodes"] == 4
    assert batch1_res["next_offset"] == 4
    assert batch1_res["status"] == "running"

    # 3. Resume remaining batches to completion
    finish_res = engine.run_mass_background_indexer(
        batch_size=4,
        cpu_threshold=70.0,
        min_sleep_sec=0.0,
        max_batches=5,
        cpu_load_fn=lambda: 20.0,
    )
    assert finish_res["indexed_nodes"] == 6
    assert finish_res["next_offset"] == 10
    assert finish_res["status"] == "complete"
    assert vs.count_vectors() == 10


def test_e2e_semantic_sync_lifecycle_reconciliation_and_recovery(tmp_path: Path):
    """
    Issues #20, #21, #22:
    1. Create a new Tana node -> confirm retrievable.
    2. Edit semantic content -> confirm old vector replaced.
    3. Change only structure/tags -> confirm metadata updates with 0 embedding churn.
    4. Delete/exclude node -> confirm reconciliation removes stale results while preserving descendants
       and never deleting live nodes on transient MCP failure.
    5. Simulate concurrent lock, interrupted sync, and terminal auth failure -> confirm safe recovery.
    """
    db = SQLiteStore(str(tmp_path / "e2e.db"))
    vs = DummyVectorStore(str(tmp_path / "vec"))
    mock_client = MagicMock()
    mock_client.last_error_type = None
    mock_client.last_error_message = None

    engine = MirrorEngine(db=db, vector_store=vs, client=mock_client)
    search_engine = SemanticSearchEngine(db, vs)
    graph_engine = GraphEngine(db)
    context_builder = ContextBuilder(db, vs, search_engine, graph_engine)

    # --- Scenario 1: Create new Tana node and confirm retrievable ---
    mock_client.search_nodes.side_effect = [
        [
            {
                "id": "node_alpha",
                "name": "Quantum Photonics Calibration",
                "description": "Laser interferometer phase alignment protocol",
                "tags": [{"id": "tag_exp", "name": "Experiment"}],
                "children": ["node_child_survives"],
            },
            {
                "id": "node_child_survives",
                "name": "Sub-assembly optical bench",
                "description": "Descendant child node that must survive parent deletion",
                "parentId": "node_alpha",
            },
        ],
        [],  # edited
        [],  # day nodes
    ]
    res1 = engine.sync_incremental(lookback_days=1)
    assert res1["status"] == "success"
    assert res1["updated_count"] == 2
    assert res1["vectors_updated_count"] == 2

    hits1 = search_engine.search(SemanticSearchRequest(query="interferometer phase alignment", limit=5))
    assert any(r.id == "node_alpha" for r in hits1.results)

    # --- Scenario 2: Edit semantic content and confirm vector is replaced ---
    embeds_before_edit = vs.embed_call_count
    mock_client.search_nodes.side_effect = [
        [],
        [
            {
                "id": "node_alpha",
                "name": "Quantum Photonics Calibration",
                "description": "Updated cryostat superconducting resonator telemetry",
                "tags": [{"id": "tag_exp", "name": "Experiment"}],
                "children": ["node_child_survives"],
            }
        ],
        [],
    ]
    res2 = engine.sync_incremental(lookback_days=1)
    assert res2["updated_count"] == 1
    assert res2["vectors_updated_count"] == 1
    assert vs.embed_call_count > embeds_before_edit
    assert "superconducting resonator" in (db.get_node("node_alpha").description or "")

    # --- Scenario 3: Change ONLY structure/tags -> confirm 0 embedding churn ---
    embeds_before_struct = vs.embed_call_count
    mock_client.search_nodes.side_effect = [
        [],
        [
            {
                "id": "node_alpha",
                "name": "Quantum Photonics Calibration",
                "description": "Updated cryostat superconducting resonator telemetry",
                "tags": [
                    {"id": "tag_exp", "name": "Experiment"},
                    {"id": "tag_pri", "name": "Priority"},
                ],
                "children": ["node_child_survives"],
            }
        ],
        [],
    ]
    res3 = engine.sync_incremental(lookback_days=1)
    assert res3["updated_count"] == 1
    assert res3["vectors_updated_count"] == 0  # Zero embedding churn!
    assert vs.embed_call_count == embeds_before_struct
    tag_names = [t.tag_name for t in db.get_node("node_alpha").supertags]
    assert "Priority" in tag_names

    # --- Scenario 4: Unchanged node is skipped completely ---
    mock_client.search_nodes.side_effect = [
        [],
        [
            {
                "id": "node_alpha",
                "name": "Quantum Photonics Calibration",
                "description": "Updated cryostat superconducting resonator telemetry",
                "tags": [
                    {"id": "tag_exp", "name": "Experiment"},
                    {"id": "tag_pri", "name": "Priority"},
                ],
                "children": ["node_child_survives"],
            }
        ],
        [],
    ]
    res4 = engine.sync_incremental(lookback_days=1)
    assert res4["updated_count"] == 0
    assert res4["skipped_count"] == 1
    assert res4["vectors_updated_count"] == 0

    # --- Scenario 5: Concurrency lock, interrupted sync, and terminal auth signal ---
    assert db.acquire_sync_lock("external-worker", ttl_seconds=60) is True
    locked_res = engine.sync_incremental(lookback_days=1)
    assert locked_res["status"] == "skipped_concurrent_lock"
    db.release_sync_lock("external-worker")

    # Simulate transient interruption during facet 1
    mock_client.last_error_type = "transient_network"
    mock_client.last_error_message = "Connection reset by peer"
    mock_client.search_nodes.side_effect = [None]
    interrupted_res = engine.sync_incremental(lookback_days=1)
    assert interrupted_res["status"] == "error_transient_mcp"
    # Existing data remains intact
    assert db.get_node("node_alpha") is not None

    # Simulate terminal 401 auth error
    mock_client.last_error_type = "auth_terminal"
    mock_client.last_error_message = "HTTP 401 Unauthorized"
    mock_client.search_nodes.side_effect = [None]
    auth_res = engine.sync_incremental(lookback_days=1)
    assert auth_res["status"] == "error_reauth_required"
    assert engine.get_sync_stats()["reauth_required"] is True

    # Reset client error state
    mock_client.last_error_type = None
    mock_client.last_error_message = None

    # --- Scenario 6: Stale-node reconciliation removes deleted parent while preserving child
    #     and NEVER removes a live node when a transient read failure occurs ---
    def fake_call(tool_name, arguments, timeout=30, raise_on_error=False):
        nid = arguments.get("nodeId")
        if nid == "node_alpha":
            mock_client.last_error_type = None
            return {"deleted": True}
        if nid == "node_child_survives":
            # Simulate a transient read failure on the surviving child!
            mock_client.last_error_type = "transient_http"
            return None
        return {"id": nid}

    mock_client._call.side_effect = fake_call
    rec_res = engine.reconcile_stale_nodes(candidate_ids=["node_alpha", "node_child_survives"])
    assert rec_res["status"] == "success"
    assert rec_res["removed_count"] == 1
    assert "node_alpha" in rec_res["removed_node_ids"]
    assert rec_res["transient_failures"] == 1

    # Confirm deleted node_alpha is gone from SQLite & VectorStore, while node_child_survives is preserved!
    assert db.get_node("node_alpha") is None
    surviving_child = db.get_node("node_child_survives")
    assert surviving_child is not None
    assert surviving_child.parent_id is None  # Safely detached from deleted parent

    # Confirm ContextBuilder & trace persistence work on surviving graph
    pkt = context_builder.acquire_context(ContextAcquisitionRequest(task="optical bench sub-assembly"))
    assert pkt.trace_id
    traces = db.list_traces(limit=10)
    assert any(t["trace_id"] == pkt.trace_id for t in traces)


def test_trace_visualizer_and_new_api_endpoints():
    """Issue #15, #7, #20: Verify /traces UI, /api/v1/traces, and /api/v1/nodes/mutate endpoints."""
    client = TestClient(app)

    # 1. Public /traces HTML visualizer
    ui_res = client.get("/traces")
    assert ui_res.status_code == 200
    assert "Retrieval Trace Visualizer & Candidate Inspector" in ui_res.text

    # 2. Authenticated /api/v1/traces
    traces_res = client.get("/api/v1/traces", headers=_auth_headers())
    assert traces_res.status_code == 200
    assert "traces" in traces_res.json()

    # 3. Authenticated dry-run mutation proxy endpoint
    mut_res = client.post(
        "/api/v1/nodes/mutate",
        headers=_auth_headers(),
        json={
            "action": "create_node",
            "parent_node_id": "INBOX",
            "name": "API Dry Run Node",
            "supertags": ["Resource"],
            "dry_run": True,
        },
    )
    assert mut_res.status_code == 200
    body = mut_res.json()
    assert body["success"] is True
    assert body["dry_run"] is True
