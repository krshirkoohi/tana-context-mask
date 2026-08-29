import os
import json
import requests
from typing import Optional, Dict, Any, List
from ..config import settings

class TanaMCPClient:
    def __init__(self, token: Optional[str] = None, url: Optional[str] = None):
        self.token = token or settings.tana_token
        self.url = url or settings.tana_url

    def _call(self, tool_name: str, arguments: Dict[str, Any], timeout: int = 30) -> Optional[Any]:
        payload = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments
            },
            "id": 1
        }
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream"
        }
        try:
            r = requests.post(self.url, json=payload, headers=headers, timeout=timeout)
            if r.status_code != 200:
                return None
            res = r.json().get("result", {})
            if "content" in res and isinstance(res["content"], list):
                for item in res["content"]:
                    if item.get("type") == "text":
                        txt = item.get("text", "")
                        try:
                            return json.loads(txt)
                        except Exception:
                            return txt
            return res
        except Exception:
            return None

    def list_workspaces(self) -> Optional[List[Dict[str, Any]]]:
        return self._call("list_workspaces", {})

    def search_nodes(self, query: Dict[str, Any], limit: int = 100) -> Optional[List[Dict[str, Any]]]:
        res = self._call("search_nodes", {"query": query, "limit": limit})
        if isinstance(res, list):
            return res
        return []

    def read_node(self, node_id: str, max_depth: int = 1) -> Optional[str]:
        res = self._call("read_node", {"nodeId": node_id, "maxDepth": max_depth})
        if isinstance(res, str):
            return res
        elif isinstance(res, dict):
            return res.get("text", str(res))
        return None

    def get_children(self, node_id: str, limit: int = 200) -> List[Dict[str, Any]]:
        res = self._call("get_children", {"nodeId": node_id, "limit": limit})
        if isinstance(res, dict):
            return res.get("children", [])
        elif isinstance(res, list):
            return res
        return []

    def list_tags(self) -> List[Dict[str, Any]]:
        res = self._call("list_tags", {})
        if isinstance(res, list):
            return res
        elif isinstance(res, dict):
            return res.get("tags", [])
        return []

    def get_tag_schema(self, tag_id: str) -> Optional[Dict[str, Any]]:
        return self._call("get_tag_schema", {"tagId": tag_id})

    def import_tana_paste(self, parent_node_id: str, content: str) -> bool:
        res = self._call("import_tana_paste", {"parentNodeId": parent_node_id, "content": content})
        return res is not None

    def edit_node(self, node_id: str, old_string: str, new_string: str) -> bool:
        res = self._call("edit_node", {
            "nodeId": node_id,
            "name": {"old_string": old_string, "new_string": new_string}
        })
        return res is not None

    def set_field_content(self, node_id: str, attribute_id: str, content: str) -> bool:
        res = self._call("set_field_content", {
            "nodeId": node_id,
            "attributeId": attribute_id,
            "content": content
        })
        return res is not None

    def trash_node(self, node_id: str) -> bool:
        res = self._call("trash_node", {"nodeId": node_id})
        return res is not None
