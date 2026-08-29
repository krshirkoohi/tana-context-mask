import sys
import json
import logging
from typing import Dict, Any, List, Optional
from ..storage.db import SQLiteStore
from ..storage.vector_store import VectorStore
from ..engine.context_builder import ContextBuilder
from ..engine.semantic_search import SemanticSearchEngine
from ..engine.graph_engine import GraphEngine
from ..sync.mirror_engine import MirrorEngine
from ..models.context import ContextAcquisitionRequest
from ..models.search import SemanticSearchRequest

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

class MCPServer:
    def __init__(self):
        self.db = SQLiteStore()
        self.vector_store = VectorStore()
        self.search_engine = SemanticSearchEngine(self.db, self.vector_store)
        self.graph_engine = GraphEngine(self.db)
        self.context_builder = ContextBuilder(self.db, self.vector_store, self.search_engine, self.graph_engine)
        self.mirror_engine = MirrorEngine(self.db, self.vector_store)

    def get_tool_definitions(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "acquire_context",
                "description": "Proactively retrieve background context, hierarchy, and references from Tana knowledge graph for a given AI task or conversation topic.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "task": {"type": "string", "description": "The current task, question, or user objective."},
                        "query": {"type": "string", "description": "Optional specific search query keywords."},
                        "scope": {"type": "string", "description": "Optional supertag filter (e.g. 'Project', 'Task')."},
                        "max_nodes": {"type": "integer", "description": "Maximum context nodes to return (default 8)."}
                    },
                    "required": ["task"]
                }
            },
            {
                "name": "search_nodes",
                "description": "Perform hybrid semantic and keyword search across the entire Tana workspace.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "The search term or conceptual phrase."},
                        "limit": {"type": "integer", "description": "Number of results to return (default 10)."},
                        "tag_filter": {"type": "string", "description": "Optional tag name to filter by."}
                    },
                    "required": ["query"]
                }
            },
            {
                "name": "get_node_details",
                "description": "Inspect a single Tana node with its parent breadcrumbs, immediate children, tags, and field values.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "node_id": {"type": "string", "description": "The Tana node ID."}
                    },
                    "required": ["node_id"]
                }
            },
            {
                "name": "expand_graph",
                "description": "Expand 1-2 hops of graph edges (parents, sub-items, references, backlinks) around a Tana node.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "node_id": {"type": "string", "description": "The seed node ID to expand from."},
                        "hops": {"type": "integer", "description": "Graph depth (1 or 2)."}
                    },
                    "required": ["node_id"]
                }
            },
            {
                "name": "sync_mirror",
                "description": "Trigger synchronization with Tana Remote MCP or bootstrap from a local export.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "mode": {"type": "string", "enum": ["incremental", "bootstrap"], "description": "Sync mode."},
                        "lookback_days": {"type": "integer", "description": "Days to look back for incremental sync."}
                    }
                }
            },
            {
                "name": "get_system_status",
                "description": "Get current indexing status, total nodes in SQLite, and vector count in LanceDB.",
                "parameters": {
                    "type": "object",
                    "properties": {}
                }
            }
        ]

    def handle_tool_call(self, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        if name == "acquire_context":
            req = ContextAcquisitionRequest(
                task=args.get("task", ""),
                query=args.get("query"),
                scope=args.get("scope"),
                max_nodes=args.get("max_nodes", 8)
            )
            packet = self.context_builder.acquire_context(req)
            return {
                "summary": packet.summary,
                "formatted_markdown": packet.formatted_context_markdown,
                "node_count": len(packet.nodes),
                "trace_id": packet.trace_id,
                "latency_ms": packet.latency_ms
            }

        elif name == "search_nodes":
            req = SemanticSearchRequest(
                query=args.get("query", ""),
                limit=args.get("limit", 10),
                tag_filter=args.get("tag_filter")
            )
            resp = self.search_engine.search(req)
            return resp.model_dump()

        elif name == "get_node_details":
            node_id = args.get("node_id", "")
            ctx = self.graph_engine.get_full_node_context(node_id)
            if not ctx:
                return {"error": f"Node {node_id} not found"}
            node = ctx["node"]
            return {
                "id": node.id,
                "name": node.name,
                "description": node.description,
                "breadcrumbs": ctx["breadcrumbs"],
                "supertags": ctx["supertags"],
                "fields": ctx["fields"],
                "children": [{"id": c.id, "name": c.name} for c in ctx["children"]],
                "references": [{"id": r.id, "name": r.name} for r in ctx["references"]],
                "backlinks": [{"id": b.id, "name": b.name} for b in ctx["backlinks"]]
            }

        elif name == "expand_graph":
            node_id = args.get("node_id", "")
            node = self.db.get_node(node_id)
            if not node:
                return {"error": f"Node {node_id} not found"}
            hops = args.get("hops", 1)
            expanded = self.graph_engine.expand_subgraph([node], max_hops=hops)
            return {
                "seed_id": node_id,
                "expanded_count": len(expanded),
                "nodes": [{"id": n.id, "name": n.name, "reason": r} for n, r in expanded]
            }

        elif name == "sync_mirror":
            mode = args.get("mode", "incremental")
            if mode == "bootstrap":
                target_export = args.get("export_file") or getattr(settings, 'tana_export_file', None)
                if not target_export:
                    return {"error": "export_file must be specified in arguments for bootstrap mode"}
                return self.mirror_engine.bootstrap_from_export(target_export)
            else:
                lookback = args.get("lookback_days", 1)
                return self.mirror_engine.sync_incremental(lookback_days=lookback)

        elif name == "get_system_status":
            return self.mirror_engine.get_sync_stats()

        else:
            return {"error": f"Unknown tool: {name}"}

    def run_stdio(self):
        """Run standard I/O JSON-RPC loop for MCP clients."""
        for line in sys.stdin:
            if not line.strip():
                continue
            try:
                msg = json.loads(line)
                msg_id = msg.get("id")
                method = msg.get("method")

                if method == "tools/list":
                    resp = {
                        "jsonrpc": "2.0",
                        "id": msg_id,
                        "result": {"tools": self.get_tool_definitions()}
                    }
                    sys.stdout.write(json.dumps(resp) + "\n")
                    sys.stdout.flush()

                elif method == "tools/call":
                    params = msg.get("params", {})
                    tool_name = params.get("name")
                    arguments = params.get("arguments", {})
                    result_data = self.handle_tool_call(tool_name, arguments)
                    resp = {
                        "jsonrpc": "2.0",
                        "id": msg_id,
                        "result": {
                            "content": [
                                {
                                    "type": "text",
                                    "text": json.dumps(result_data, indent=2)
                                }
                            ]
                        }
                    }
                    sys.stdout.write(json.dumps(resp) + "\n")
                    sys.stdout.flush()

                else:
                    # Echo generic ack
                    resp = {"jsonrpc": "2.0", "id": msg_id, "result": {}}
                    sys.stdout.write(json.dumps(resp) + "\n")
                    sys.stdout.flush()

            except Exception as e:
                err_resp = {
                    "jsonrpc": "2.0",
                    "id": msg.get("id") if 'msg' in locals() else None,
                    "error": {"code": -32603, "message": str(e)}
                }
                sys.stdout.write(json.dumps(err_resp) + "\n")
                sys.stdout.flush()

def main():
    server = MCPServer()
    server.run_stdio()

if __name__ == "__main__":
    main()
