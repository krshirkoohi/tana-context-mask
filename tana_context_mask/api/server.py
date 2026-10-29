from fastapi import FastAPI, HTTPException, Query, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse
from typing import Optional, Dict, Any, List
from ..config import settings
from ..storage.db import SQLiteStore
from ..storage.vector_store import VectorStore
from ..engine.context_builder import ContextBuilder
from ..engine.semantic_search import SemanticSearchEngine
from ..engine.graph_engine import GraphEngine
from ..sync.mirror_engine import MirrorEngine
from ..models.context import ContextAcquisitionRequest, ContextPacket
from ..models.search import SemanticSearchRequest, SearchResponse
from ..models.node import TanaNode
from ..models.mutation import MutationRequest, MutationResult

app = FastAPI(
    title="Tana Context Mask API",
    version="1.0.0",
    description="An embedding-backed, graph-aware semantic context acquisition layer for Tana Outliner and ChatGPT Actions.",
    servers=[{"url": f"http://localhost:{settings.port}", "description": "Local server"}]
)

# Enable CORS for web clients & OpenAPI inspection
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global component instances
db = SQLiteStore()
vector_store = VectorStore()
search_engine = SemanticSearchEngine(db, vector_store)
graph_engine = GraphEngine(db)
context_builder = ContextBuilder(db, vector_store, search_engine, graph_engine)
mirror_engine = MirrorEngine(db, vector_store)

def verify_api_key(
    x_api_key: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None)
):
    if not settings.api_key:
        return True
    token = None
    if authorization:
        token = authorization[7:].strip() if authorization.startswith("Bearer ") else authorization.strip()
    elif x_api_key:
        token = x_api_key.strip()
    if token != settings.api_key:
        raise HTTPException(status_code=401, detail="Invalid or missing API Key")
    return True

@app.get("/", summary="Root Health & Overview")
def root():
    stats = mirror_engine.get_sync_stats()
    return {
        "name": "Tana Context Mask",
        "status": "online",
        "stats": stats,
        "docs_url": "/docs",
        "openapi_url": "/openapi.json"
    }

@app.get("/api/v1/health", summary="Health Check")
def health_check():
    return {
        "status": "healthy",
        "sqlite_nodes": db.count_nodes(),
        "lancedb_vectors": vector_store.count_vectors()
    }

@app.post(
    "/api/v1/context/acquire",
    response_model=ContextPacket,
    summary="Acquire Context for AI Task",
    operation_id="acquireContext",
    description="Proactively discovers, expands, and reranks relevant background context from Tana knowledge graph before answering a user request."
)
def acquire_context(req: ContextAcquisitionRequest, auth: bool = Depends(verify_api_key)):
    try:
        packet = context_builder.acquire_context(req)
        return packet
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Context acquisition failed: {str(e)}")

@app.post(
    "/api/v1/search",
    response_model=SearchResponse,
    summary="Semantic & Hybrid Search",
    operation_id="searchNodes",
    description="Direct hybrid semantic and BM25 keyword search across full Tana corpus."
)
def search_nodes(req: SemanticSearchRequest, auth: bool = Depends(verify_api_key)):
    try:
        return search_engine.search(req)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")

@app.get(
    "/api/v1/nodes/{node_id}",
    summary="Inspect Single Node with Graph Lineage",
    operation_id="getNode",
    description="Returns node details, parent chain, children, references, backlinks, supertags, and fields."
)
def get_node(node_id: str, auth: bool = Depends(verify_api_key)):
    context = graph_engine.get_full_node_context(node_id)
    if not context:
        raise HTTPException(status_code=404, detail=f"Node {node_id} not found")
    return context

@app.post(
    "/api/v1/nodes/{node_id}/expand",
    summary="Expand Graph Around Node",
    operation_id="expandNode",
    description="Traverses 1-2 hops of parent, child, reference, and backlink edges around a target node."
)
def expand_node(node_id: str, hops: int = Query(1, ge=1, le=3), auth: bool = Depends(verify_api_key)):
    node = db.get_node(node_id)
    if not node:
        raise HTTPException(status_code=404, detail=f"Node {node_id} not found")
    
    expanded = graph_engine.expand_subgraph([node], max_hops=hops, max_expansion_nodes=20)
    return {
        "seed_id": node_id,
        "hops": hops,
        "total_expanded": len(expanded),
        "nodes": [{"id": n.id, "name": n.name, "reason": r, "tags": [t.tag_name for t in n.supertags]} for n, r in expanded]
    }

@app.post(
    "/api/v1/sync",
    summary="Trigger Mirror Sync",
    description="Trigger an incremental sync via Tana Remote MCP or bootstrap from a local export file."
)
def trigger_sync(
    mode: str = Query("incremental", enum=["incremental", "bootstrap"]),
    lookback_days: int = Query(1, ge=1, le=30),
    export_file: Optional[str] = None,
    max_nodes: Optional[int] = None,
    auth: bool = Depends(verify_api_key)
):
    try:
        if mode == "bootstrap":
            target_export = export_file or getattr(settings, 'tana_export_file', None)
            if not target_export:
                raise HTTPException(status_code=400, detail="export_file must be specified for bootstrap mode")
            res = mirror_engine.bootstrap_from_export(target_export, max_nodes=max_nodes)
            return res
        else:
            res = mirror_engine.sync_incremental(lookback_days=lookback_days)
            return res
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Sync failed: {str(e)}")

@app.get(
    "/api/v1/sync/status",
    summary="Get Sync & Mirror Status",
    description="Returns current node count, vector count, auth health, and last sync timestamps."
)
def get_sync_status(auth: bool = Depends(verify_api_key)):
    return mirror_engine.get_sync_stats()

@app.post(
    "/api/v1/sync/reconcile",
    summary="Reconcile Deleted & Stale Nodes",
    description="Runs a bounded reconciliation pass to remove deleted, trashed, or out-of-scope nodes from SQLite and LanceDB while preserving valid descendants."
)
def reconcile_stale_nodes(
    batch_limit: int = Query(100, ge=1, le=1000),
    candidate_ids: Optional[List[str]] = None,
    auth: bool = Depends(verify_api_key)
):
    try:
        return mirror_engine.reconcile_stale_nodes(batch_limit=batch_limit, candidate_ids=candidate_ids)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Reconciliation failed: {str(e)}")

@app.post(
    "/api/v1/sync/daemon",
    summary="Run Mass Background Indexer Batch",
    description="Runs CPU-gated background indexing batches over historical nodes with automatic rate limiting."
)
def run_mass_indexer(
    batch_size: int = Query(200, ge=1, le=2000),
    cpu_threshold: float = Query(70.0, ge=5.0, le=100.0),
    max_batches: int = Query(5, ge=1, le=500),
    export_file: Optional[str] = None,
    auth: bool = Depends(verify_api_key)
):
    try:
        return mirror_engine.run_mass_background_indexer(
            export_path=export_file,
            batch_size=batch_size,
            cpu_threshold=cpu_threshold,
            max_batches=max_batches,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Background indexer failed: {str(e)}")

@app.post(
    "/api/v1/nodes/mutate",
    response_model=MutationResult,
    summary="Safe Mutation Proxy for Tana Nodes",
    operation_id="mutateNode",
    description="Safely create child nodes, apply supertags, set fields, or rename nodes with dry-run validation and idempotency keys."
)
def mutate_node(req: MutationRequest, auth: bool = Depends(verify_api_key)):
    try:
        return mirror_engine.execute_mutation(req)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Mutation failed: {str(e)}")

@app.get(
    "/api/v1/traces",
    summary="List Recent Retrieval Traces",
    description="Returns recent context acquisition traces with candidate counts and latency."
)
def list_traces(limit: int = Query(25, ge=1, le=100), auth: bool = Depends(verify_api_key)):
    return {"traces": db.list_traces(limit=limit)}

@app.get(
    "/api/v1/traces/{trace_id}",
    summary="Inspect Retrieval Trace",
    description="Returns the exact candidate nodes, scores, decisions, and graph expansion paths for a trace."
)
def get_trace(trace_id: str, auth: bool = Depends(verify_api_key)):
    trace = db.get_trace(trace_id)
    if not trace:
        raise HTTPException(status_code=404, detail=f"Trace {trace_id} not found")
    return trace


TRACE_VIEWER_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Tana Context Mask — Retrieval Trace Visualizer & Candidate Inspector</title>
  <style>
    :root { --bg: #0f172a; --panel: #1e293b; --border: #334155; --text: #f8fafc; --muted: #94a3b8; --accent: #6366f1; --green: #10b981; --amber: #f59e0b; }
    * { box-sizing: border-box; }
    body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: var(--bg); color: var(--text); }
    header { padding: 16px 24px; border-bottom: 1px solid var(--border); display: flex; justify-content: space-between; align-items: center; background: #0b1120; }
    header h1 { margin: 0; font-size: 18px; font-weight: 600; }
    .container { display: grid; grid-template-columns: 340px 1fr; height: calc(100vh - 61px); }
    .sidebar { border-right: 1px solid var(--border); overflow-y: auto; padding: 16px; background: #111827; }
    .main { overflow-y: auto; padding: 24px; }
    .trace-item { padding: 12px; border: 1px solid var(--border); border-radius: 8px; margin-bottom: 10px; cursor: pointer; background: var(--panel); transition: border-color 0.15s; }
    .trace-item:hover, .trace-item.active { border-color: var(--accent); }
    .trace-task { font-weight: 600; font-size: 14px; margin-bottom: 6px; }
    .trace-meta { font-size: 12px; color: var(--muted); display: flex; justify-content: space-between; }
    .grid-cols { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 16px; margin-top: 16px; }
    .card { background: var(--panel); border: 1px solid var(--border); border-radius: 10px; padding: 16px; }
    .card h3 { margin-top: 0; font-size: 15px; border-bottom: 1px solid var(--border); padding-bottom: 8px; }
    .node-pill { padding: 10px; border-radius: 6px; background: #0f172a; border: 1px solid var(--border); margin-bottom: 8px; font-size: 13px; }
    .badge { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; font-weight: 600; background: rgba(99,102,241,0.2); color: #a5b4fc; margin-left: 6px; }
    .reason { color: var(--green); font-size: 12px; margin-top: 4px; }
    input { background: var(--panel); border: 1px solid var(--border); color: var(--text); padding: 6px 12px; border-radius: 6px; width: 240px; }
  </style>
</head>
<body>
  <header>
    <h1>🌐 Tana Context Mask — Retrieval Trace Visualizer & Candidate Inspector</h1>
    <div>
      <input id="apiKeyInput" type="password" placeholder="Optional API Key (X-API-Key)" onchange="loadTraces()" />
    </div>
  </header>
  <div class="container">
    <div class="sidebar">
      <div style="font-size:13px;font-weight:600;margin-bottom:12px;color:var(--muted);">RECENT RETRIEVAL TRACES</div>
      <div id="traceList">Loading traces...</div>
    </div>
    <div class="main" id="traceDetail">
      <div class="card">
        <h3>Select a trace from the left sidebar to inspect graph expansion & candidate reranking</h3>
        <p style="color:var(--muted);font-size:14px;">Displays seed hybrid matches, 1-hop graph expansion nodes, final selected context nodes with why-included reasoning, and excluded candidates.</p>
      </div>
    </div>
  </div>
  <script>
    function getHeaders() {
      const k = document.getElementById('apiKeyInput').value.trim();
      return k ? { 'X-API-Key': k } : {};
    }
    async function loadTraces() {
      try {
        const res = await fetch('/api/v1/traces?limit=30', { headers: getHeaders() });
        const data = await res.json();
        const listEl = document.getElementById('traceList');
        const traces = data.traces || [];
        if (!traces.length) {
          listEl.innerHTML = '<div style="color:var(--muted);font-size:13px;">No traces recorded yet. Call POST /api/v1/context/acquire first.</div>';
          return;
        }
        listEl.innerHTML = traces.map(t => `
          <div class="trace-item" onclick="inspectTrace('${t.trace_id}')">
            <div class="trace-task">${t.task || t.inferred_query || t.trace_id}</div>
            <div class="trace-meta">
              <span>Selected: ${t.reranked_count} / Exp: ${t.expanded_count}</span>
              <span>${Math.round(t.latency_ms || 0)}ms</span>
            </div>
          </div>
        `).join('');
        inspectTrace(traces[0].trace_id);
      } catch (e) {
        document.getElementById('traceList').innerHTML = `<div style="color:#f87171;">Failed to load traces: ${e.message}</div>`;
      }
    }
    function renderNodeList(items) {
      if (!items || !items.length) return '<div style="color:var(--muted);font-size:12px;">None</div>';
      return items.map(n => `
        <div class="node-pill">
          <div><strong>${n.name || n.id}</strong> <span class="badge">${typeof n.score === 'number' ? n.score.toFixed(3) : (n.relevance_score ? n.relevance_score.toFixed(3) : 'node')}</span></div>
          <div style="font-size:11px;color:var(--muted);">ID: <code>${n.id}</code></div>
          ${n.reason || n.inclusion_reason ? `<div class="reason">↳ Why included: ${n.reason || n.inclusion_reason}</div>` : ''}
        </div>
      `).join('');
    }
    async function inspectTrace(traceId) {
      const res = await fetch(`/api/v1/traces/${traceId}`, { headers: getHeaders() });
      if (!res.ok) return;
      const t = await res.json();
      document.getElementById('traceDetail').innerHTML = `
        <div class="card">
          <h3>Trace <code>${t.trace_id}</code> — ${t.task}</h3>
          <div style="font-size:13px;color:var(--muted);">Inferred Query: <code>${t.inferred_query}</code> · Latency: <strong>${Math.round(t.latency_ms || 0)}ms</strong> · Timestamp: ${t.created_at}</div>
        </div>
        <div class="grid-cols">
          <div class="card"><h3>1. Seed Hybrid Hits (${(t.seed_nodes || []).length})</h3>${renderNodeList(t.seed_nodes)}</div>
          <div class="card"><h3>2. Graph Expansion Pool (${(t.expanded_nodes || []).length})</h3>${renderNodeList(t.expanded_nodes)}</div>
          <div class="card"><h3>3. Reranked Selected Context (${(t.reranked_nodes || []).length})</h3>${renderNodeList(t.reranked_nodes)}</div>
          <div class="card"><h3>4. Excluded Candidates (${(t.excluded_nodes || []).length})</h3>${renderNodeList(t.excluded_nodes)}</div>
        </div>
      `;
    }
    loadTraces();
  </script>
</body>
</html>"""


@app.get(
    "/traces",
    response_class=HTMLResponse,
    summary="Web Retrieval Trace Visualizer & Candidate Inspector",
    description="Interactive HTML/JS diagnostics viewer displaying graph expansion nodes, scores, and why-included reasoning."
)
def trace_visualizer_ui():
    return HTMLResponse(content=TRACE_VIEWER_HTML)
