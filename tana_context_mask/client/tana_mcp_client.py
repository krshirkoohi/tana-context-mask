import os
import json
import time
import random
import logging
import requests
from typing import Optional, Dict, Any, List
from ..config import settings

logger = logging.getLogger(__name__)


class TanaAuthError(RuntimeError):
    """Terminal authentication or authorisation failure (HTTP 401/403). Manual re-authorisation required."""


class TanaTransientError(RuntimeError):
    """Retryable transient MCP transport, rate-limit (429), or upstream 5xx error."""


class TanaMCPClient:
    def __init__(self, token: Optional[str] = None, url: Optional[str] = None, max_retries: int = 3, base_backoff: float = 0.25):
        self.token = token if token is not None else settings.tana_token
        self.url = url or settings.tana_url
        self.max_retries = max_retries
        self.base_backoff = base_backoff
        self.last_error_type: Optional[str] = None
        self.last_error_message: Optional[str] = None

    def _call(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        timeout: int = 30,
        raise_on_error: bool = False,
    ) -> Optional[Any]:
        if not self.token:
            self.last_error_type = "missing_token"
            self.last_error_message = "TANA_TOKEN is not configured"
            if raise_on_error:
                raise TanaAuthError(self.last_error_message)
            return None

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

        attempt = 0
        while attempt < max(1, self.max_retries):
            attempt += 1
            try:
                r = requests.post(self.url, json=payload, headers=headers, timeout=timeout)
                if r.status_code in (401, 403):
                    self.last_error_type = "auth_terminal"
                    self.last_error_message = f"Tana MCP authorisation rejected (HTTP {r.status_code})"
                    logger.error("[TanaMCP] Terminal auth error on %s: HTTP %d", tool_name, r.status_code)
                    if raise_on_error:
                        raise TanaAuthError(self.last_error_message)
                    return None

                if r.status_code == 429 or (500 <= r.status_code <= 504):
                    self.last_error_type = "transient_http"
                    self.last_error_message = f"Tana MCP transient HTTP {r.status_code} on {tool_name}"
                    logger.warning("[TanaMCP] Retryable HTTP %d on %s (attempt %d/%d)", r.status_code, tool_name, attempt, self.max_retries)
                    if attempt < self.max_retries:
                        sleep_s = min(4.0, self.base_backoff * (2 ** (attempt - 1))) + random.uniform(0.0, 0.1)
                        time.sleep(sleep_s)
                        continue
                    if raise_on_error:
                        raise TanaTransientError(self.last_error_message)
                    return None

                if r.status_code != 200:
                    self.last_error_type = "terminal_http"
                    self.last_error_message = f"Tana MCP non-retryable HTTP {r.status_code}"
                    if raise_on_error:
                        raise RuntimeError(self.last_error_message)
                    return None

                body = r.json()
                if "error" in body and body["error"]:
                    err_obj = body["error"]
                    err_msg = err_obj.get("message", str(err_obj)) if isinstance(err_obj, dict) else str(err_obj)
                    self.last_error_type = "rpc_error"
                    self.last_error_message = err_msg
                    if raise_on_error:
                        raise TanaTransientError(err_msg)
                    return None

                self.last_error_type = None
                self.last_error_message = None
                res = body.get("result", {})
                if "content" in res and isinstance(res["content"], list):
                    for item in res["content"]:
                        if item.get("type") == "text":
                            txt = item.get("text", "")
                            try:
                                return json.loads(txt)
                            except Exception:
                                return txt
                return res
            except TanaAuthError:
                raise
            except (requests.RequestException, TanaTransientError) as exc:
                self.last_error_type = "transient_network"
                self.last_error_message = str(exc)
                logger.warning("[TanaMCP] Transient failure on %s (attempt %d/%d): %s", tool_name, attempt, self.max_retries, exc)
                if attempt < self.max_retries:
                    sleep_s = min(4.0, self.base_backoff * (2 ** (attempt - 1))) + random.uniform(0.0, 0.1)
                    time.sleep(sleep_s)
                    continue
                if raise_on_error:
                    raise TanaTransientError(str(exc)) from exc
                return None
            except Exception as exc:
                self.last_error_type = "unexpected_error"
                self.last_error_message = str(exc)
                if raise_on_error:
                    raise
                return None
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
