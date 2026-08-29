import argparse
import sys
import json
import uvicorn
from ..config import settings
from ..storage.db import SQLiteStore
from ..storage.vector_store import VectorStore
from ..sync.mirror_engine import MirrorEngine
from ..engine.semantic_search import SemanticSearchEngine
from ..engine.graph_engine import GraphEngine
from ..engine.context_builder import ContextBuilder
from ..models.search import SemanticSearchRequest
from ..models.context import ContextAcquisitionRequest
from ..api.mcp_server import MCPServer

def main():
    parser = argparse.ArgumentParser(
        prog="tana-context-mask",
        description="Tana Semantic Engine & Graph Context Acquisition Layer"
    )
    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    # Command: context
    ctx_parser = subparsers.add_parser("context", help="Acquire background context for an AI task")
    ctx_parser.add_argument("task", type=str, help="The AI task or prompt")
    ctx_parser.add_argument("--query", "-q", type=str, default=None, help="Specific search keywords")
    ctx_parser.add_argument("--scope", "-s", type=str, default=None, help="Filter scope by supertag name")
    ctx_parser.add_argument("--max-nodes", "-m", type=int, default=8, help="Maximum context nodes to return")
    ctx_parser.add_argument("--json", action="store_true", help="Output raw JSON packet")

    # Command: search
    search_parser = subparsers.add_parser("search", help="Perform hybrid semantic search across Tana")
    search_parser.add_argument("query", type=str, help="Search query")
    search_parser.add_argument("--limit", "-l", type=int, default=10, help="Number of results")
    search_parser.add_argument("--tag", "-t", type=str, default=None, help="Tag filter")

    # Command: node
    node_parser = subparsers.add_parser("node", help="Inspect a node's full graph lineage")
    node_parser.add_argument("node_id", type=str, help="Tana node ID")

    # Command: sync
    sync_parser = subparsers.add_parser("sync", help="Synchronize Tana mirror and embeddings")
    sync_parser.add_argument("--mode", choices=["incremental", "bootstrap"], default="incremental", help="Sync mode")
    sync_parser.add_argument("--file", "-f", type=str, default=None, help="Path to Tana JSON export file for bootstrap")
    sync_parser.add_argument("--max-nodes", "-n", type=int, default=None, help="Limit number of nodes to import")
    sync_parser.add_argument("--lookback", type=int, default=1, help="Days to look back for incremental sync")

    # Command: stats
    subparsers.add_parser("stats", help="Show current mirror and vector database statistics")

    # Command: serve
    serve_parser = subparsers.add_parser("serve", help="Start FastAPI REST API server")
    serve_parser.add_argument("--host", type=str, default=settings.host, help="Bind host")
    serve_parser.add_argument("--port", type=int, default=settings.port, help="Bind port")
    serve_parser.add_argument("--reload", action="store_true", help="Auto-reload on code changes")

    # Command: mcp
    subparsers.add_parser("mcp", help="Start Model Context Protocol (MCP) server over stdio")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    db = SQLiteStore()
    vector_store = VectorStore()
    mirror_engine = MirrorEngine(db, vector_store)
    search_engine = SemanticSearchEngine(db, vector_store)
    graph_engine = GraphEngine(db)
    context_builder = ContextBuilder(db, vector_store, search_engine, graph_engine)

    if args.command == "stats":
        stats = mirror_engine.get_sync_stats()
        print("\n📊 Tana Context Mask System Stats:")
        print(f"  • SQLite Nodes:        {stats['total_nodes_sqlite']}")
        print(f"  • LanceDB Vectors:     {stats['total_vectors_lancedb']}")
        print(f"  • Embedding Model:     {stats['embedding_model']}")
        print(f"  • Last Incremental:    {stats['last_incremental_sync'] or 'Never'}")
        print(f"  • Last Bootstrap:      {stats['last_export_bootstrap'] or 'Never'}")
        print(f"  • Engine Status:       {stats['status']}\n")

    elif args.command == "sync":
        if args.mode == "bootstrap":
            export_file = args.file or getattr(settings, 'tana_export_file', None)
            if not export_file:
                print("Error: --file argument is required for bootstrap mode.")
                return
            print(f"Starting Export Bootstrap from: {export_file}")
            res = mirror_engine.bootstrap_from_export(export_file, max_nodes=args.max_nodes)
            print("Bootstrap Complete:", json.dumps(res, indent=2))
        else:
            print(f"Starting Incremental Sync (lookback: {args.lookback} days)...")
            res = mirror_engine.sync_incremental(lookback_days=args.lookback)
            print("Incremental Sync Complete:", json.dumps(res, indent=2))

    elif args.command == "search":
        req = SemanticSearchRequest(
            query=args.query,
            limit=args.limit,
            tag_filter=args.tag
        )
        resp = search_engine.search(req)
        print(f"\n🔍 Search Results for: '{resp.query}' ({resp.total_hits} hits in {resp.latency_ms}ms)\n")
        for idx, item in enumerate(resp.results, start=1):
            tags_str = f" [{' '.join('#' + t for t in item.tags)}]" if item.tags else ""
            crumbs_str = f" ({' › '.join(item.breadcrumbs)})" if item.breadcrumbs else ""
            print(f"{idx}. [{item.score:.2f}] {item.name}{tags_str}{crumbs_str}")
            print(f"   ID: tana:{item.id}")
            if item.description:
                print(f"   Desc: {item.description[:100]}")
            print("-" * 50)

    elif args.command == "context":
        req = ContextAcquisitionRequest(
            task=args.task,
            query=args.query,
            scope=args.scope,
            max_nodes=args.max_nodes
        )
        packet = context_builder.acquire_context(req)
        if args.json:
            print(packet.model_dump_json(indent=2))
        else:
            print(packet.formatted_context_markdown)

    elif args.command == "node":
        ctx = graph_engine.get_full_node_context(args.node_id)
        if not ctx:
            print(f"❌ Node '{args.node_id}' not found.")
            sys.exit(1)
        node = ctx["node"]
        print(f"\n📄 Node: {node.name} (tana:{node.id})")
        print(f"  • Breadcrumbs:  {' › '.join(ctx['breadcrumbs']) if ctx['breadcrumbs'] else 'Root'}")
        print(f"  • Supertags:    {', '.join(ctx['supertags']) if ctx['supertags'] else 'None'}")
        print(f"  • Fields:       {json.dumps(ctx['fields'])}")
        print(f"  • Children:     {len(ctx['children'])} sub-items")
        print(f"  • References:   {len(ctx['references'])} references")
        print(f"  • Backlinks:    {len(ctx['backlinks'])} incoming backlinks\n")

    elif args.command == "serve":
        print(f"🚀 Starting Tana Context Mask Server on http://{args.host}:{args.port}...")
        uvicorn.run("tana_context_mask.api.server:app", host=args.host, port=args.port, reload=args.reload)

    elif args.command == "mcp":
        server = MCPServer()
        server.run_stdio()

def cli_entrypoint():
    main()

if __name__ == "__main__":
    main()
