import pytest
from tana_context_mask.api.mcp_server import MCPServer
from tana_context_mask.models.node import TanaNode

@pytest.fixture
def mcp_server():
    server = MCPServer()
    node = TanaNode(
        id="mcp_test_node",
        name="MCP Protocol Interface Test",
        description="Testing JSON-RPC tool dispatches"
    )
    server.db.upsert_node(node)
    server.vector_store.upsert_vectors([{"id": node.id, "name": node.name, "description": node.description, "hash": node.content_hash}])
    return server

def test_tool_definitions(mcp_server):
    tools = mcp_server.get_tool_definitions()
    tool_names = [t["name"] for t in tools]
    assert "acquire_context" in tool_names
    assert "search_nodes" in tool_names
    assert "get_node_details" in tool_names
    assert "expand_graph" in tool_names
    assert "sync_mirror" in tool_names

def test_tool_execution(mcp_server):
    res = mcp_server.handle_tool_call("acquire_context", {"task": "MCP Interface Test", "max_nodes": 2})
    assert "formatted_markdown" in res
    assert res["node_count"] >= 1

    search_res = mcp_server.handle_tool_call("search_nodes", {"query": "Protocol Interface"})
    assert search_res["total_hits"] >= 1
