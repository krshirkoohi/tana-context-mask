from fastapi import FastAPI, HTTPException, Query, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
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

def verify_api_key(x_api_key: Optional[str] = Header(None)):
    if settings.api_key and x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="Invalid API Key")
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
            if not export_file:
                # Use default export if present
                default_export = "/Users/krshirkoohi/Documents/Notes Repo/2026/--D3QJHnLgSk@2026-03-07.json"
                export_file = default_export
            res = mirror_engine.bootstrap_from_export(export_file, max_nodes=max_nodes)
            return res
        else:
            res = mirror_engine.sync_incremental(lookback_days=lookback_days)
            return res
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Sync failed: {str(e)}")

@app.get(
    "/api/v1/sync/status",
    summary="Get Sync & Mirror Status",
    description="Returns current node count, vector count, and last sync timestamps."
)
def get_sync_status(auth: bool = Depends(verify_api_key)):
    return mirror_engine.get_sync_stats()

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
