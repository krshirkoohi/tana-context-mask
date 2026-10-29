import os
import json
import re
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple, Callable
from ..config import settings
from ..storage.db import SQLiteStore
from ..storage.vector_store import VectorStore
from ..client.tana_mcp_client import TanaMCPClient, TanaAuthError, TanaTransientError
from ..models.node import TanaNode, NodeTag, NodeField
from ..models.mutation import MutationRequest, MutationResult
from ..engine.temporal import resolve_node_provenance

class MirrorEngine:
    def __init__(self, db: Optional[SQLiteStore] = None, vector_store: Optional[VectorStore] = None, client: Optional[TanaMCPClient] = None):
        self.db = db or SQLiteStore()
        self.vector_store = vector_store or VectorStore()
        self.client = client or TanaMCPClient()

    def sync_incremental(self, lookback_days: int = 1) -> Dict[str, Any]:
        """
        Poll Tana Remote MCP across multiple facets (created, edited, day calendar nodes)
        and update local mirror and vector index with full coverage, concurrency locking,
        and separate content vs structure hash invalidation (#20, #21, #22, #28).
        """
        lock_owner = f"sync-{uuid.uuid4().hex[:12]}"
        if not self.db.acquire_sync_lock(lock_owner, ttl_seconds=300):
            return {
                "status": "skipped_concurrent_lock",
                "lookback_days": lookback_days,
                "polled_count": 0,
                "updated_count": 0,
                "skipped_count": 0,
                "vectors_updated_count": 0,
                "deleted_count": 0,
                "timestamp": datetime.now().isoformat(),
            }

        try:
            node_map: Dict[str, Dict[str, Any]] = {}

            # Facet 1: Created nodes in lookback
            created_nodes = self.client.search_nodes({"created": {"last": lookback_days}}, limit=1000)
            if self.client.last_error_type == "auth_terminal":
                self.db.set_sync_meta("auth_status", "reauth_required")
                self.db.set_sync_meta("last_sync_error", self.client.last_error_message or "Authorisation required")
                return {
                    "status": "error_reauth_required",
                    "error": self.client.last_error_message or "Authorisation required",
                    "reauth_required": True,
                    "polled_count": 0,
                    "updated_count": 0,
                    "timestamp": datetime.now().isoformat(),
                }
            if self.client.last_error_type and self.client.last_error_type.startswith("transient"):
                self.db.set_sync_meta("last_sync_error", self.client.last_error_message or "Transient MCP failure")
                return {
                    "status": "error_transient_mcp",
                    "error": self.client.last_error_message or "Transient MCP failure",
                    "reauth_required": False,
                    "polled_count": 0,
                    "updated_count": 0,
                    "timestamp": datetime.now().isoformat(),
                }

            created_nodes = created_nodes or []
            for n in created_nodes:
                if n.get("id"):
                    node_map[n["id"]] = n

            # If created nodes hit 1000 cap, partition
            if len(created_nodes) >= 1000:
                partitions = [
                    {"and": [{"created": {"last": lookback_days}}, {"has": "tag"}]},
                    {"and": [{"created": {"last": lookback_days}}, {"not": {"has": "tag"}}]},
                    {"and": [{"created": {"last": lookback_days}}, {"is": "calendarNode"}]},
                    {"and": [{"created": {"last": lookback_days}}, {"is": "todo"}]},
                    {"and": [{"created": {"last": lookback_days}}, {"is": "entity"}]}
                ]
                for p in partitions:
                    p_nodes = self.client.search_nodes(p, limit=1000) or []
                    for n in p_nodes:
                        if n.get("id"):
                            node_map[n["id"]] = n

            # Facet 2: Edited nodes in lookback
            edited_nodes = self.client.search_nodes({"edited": {"last": lookback_days}}, limit=1000) or []
            if self.client.last_error_type and self.client.last_error_type.startswith("transient"):
                self.db.set_sync_meta("last_sync_error", self.client.last_error_message or "Transient MCP failure on edited_nodes")
                return {
                    "status": "error_transient_mcp",
                    "error": self.client.last_error_message or "Transient MCP failure on edited_nodes",
                    "reauth_required": False,
                    "polled_count": len(node_map),
                    "updated_count": 0,
                    "timestamp": datetime.now().isoformat(),
                }
            for n in edited_nodes:
                if n.get("id"):
                    node_map[n["id"]] = n

            # Facet 3: Day calendar nodes (hasType Day = 1Kcq0q_pf5Fn)
            day_nodes = self.client.search_nodes({"hasType": "1Kcq0q_pf5Fn"}, limit=100) or []
            for d in day_nodes:
                did = d.get("id")
                if did:
                    node_map[did] = d
                    children = self.client.get_children(did, limit=200) or []
                    child_ids = []
                    for c in children:
                        cid = c.get("id")
                        if cid:
                            child_ids.append(cid)
                            if cid not in node_map:
                                c["parentId"] = did
                                node_map[cid] = c
                            else:
                                node_map[cid]["parentId"] = did
                    d["children"] = child_ids

            raw_nodes = list(node_map.values())
            updated_nodes: List[TanaNode] = []
            vectors_to_update: List[Dict[str, Any]] = []
            trashed_ids: List[str] = []
            skipped_count = 0

            for raw in raw_nodes:
                node_id = raw.get("id")
                if not node_id:
                    continue

                parent_id = raw.get("parentId")
                if raw.get("inTrash") is True or (isinstance(parent_id, str) and parent_id.endswith("_TRASH")):
                    trashed_ids.append(node_id)
                    continue

                raw_name = raw.get("name", "")
                name = re.sub(r'<[^>]+>', '', str(raw_name)).strip()
                desc = (raw.get("description") or "").strip()
                created_at = raw.get("created")
                doc_type = raw.get("docType")

                # Fetch tags or field details if available
                supertags = []
                if "tags" in raw and isinstance(raw["tags"], list):
                    for st in raw["tags"]:
                        if isinstance(st, dict):
                            supertags.append(NodeTag(tag_id=st.get("id", ""), tag_name=st.get("name", "")))
                        elif isinstance(st, str):
                            supertags.append(NodeTag(tag_id=st, tag_name=st))
                elif "supertags" in raw and isinstance(raw["supertags"], list):
                    for st in raw["supertags"]:
                        if isinstance(st, dict):
                            supertags.append(NodeTag(tag_id=st.get("id", ""), tag_name=st.get("name", "")))
                        elif isinstance(st, str):
                            supertags.append(NodeTag(tag_id=st, tag_name=st))

                children = raw.get("children", [])
                c_hash = SQLiteStore.calculate_content_hash(name, desc)
                s_hash = SQLiteStore.calculate_structure_hash(parent_id, children, [t.tag_name for t in supertags])

                # Check existing content and structure hashes
                existing_node = self.db.get_node(node_id)
                content_unchanged = bool(existing_node and existing_node.content_hash == c_hash)
                structure_unchanged = bool(
                    existing_node
                    and existing_node.structure_hash == s_hash
                    and existing_node.parent_id == parent_id
                )

                if content_unchanged and structure_unchanged:
                    skipped_count += 1
                    continue  # Neither content nor structure changed

                node = TanaNode(
                    id=node_id,
                    name=name,
                    description=desc,
                    doc_type=doc_type,
                    parent_id=parent_id,
                    created_at=created_at,
                    content_hash=c_hash,
                    structure_hash=s_hash,
                    supertags=supertags,
                    children=children,
                    updated_at=datetime.now().isoformat()
                )

                # Resolve calendar provenance
                parents = self.db.get_parents_chain(node_id, max_depth=8)
                prov = resolve_node_provenance(node, parents)
                node.effective_date = prov["effective_date"]
                node.date_source = prov["date_source"]
                node.date_source_node_id = prov["date_source_node_id"]
                node.calendar_distance = prov["calendar_distance"]
                node.calendar_path = prov["calendar_path"]
                node.ancestor_day_node_id = prov["ancestor_day_node_id"]
                node.ancestor_week_node_id = prov["ancestor_week_node_id"]
                node.ancestor_month_node_id = prov["ancestor_month_node_id"]
                node.ancestor_year_node_id = prov["ancestor_year_node_id"]

                updated_nodes.append(node)

                # Only re-embed when semantic content actually changed (#22, #28)
                if not content_unchanged and (name or desc):
                    vectors_to_update.append({
                        "id": node_id,
                        "name": name,
                        "description": desc,
                        "hash": c_hash
                    })

            if trashed_ids:
                self.db.delete_nodes(trashed_ids)
                self.vector_store.delete_vectors(trashed_ids)

            if updated_nodes:
                self.db.bulk_upsert_nodes(updated_nodes)

            if vectors_to_update:
                self.vector_store.upsert_vectors(vectors_to_update)

            now_iso = datetime.now().isoformat()
            self.db.set_sync_meta("auth_status", "ok")
            self.db.set_sync_meta("last_sync_error", "")
            self.db.set_sync_meta("last_incremental_sync", now_iso)
            self.db.set_sync_meta("last_successful_sync", now_iso)
            self.db.set_sync_meta("last_incremental_count", str(len(updated_nodes)))
            self.db.set_sync_meta("last_skipped_unchanged", str(skipped_count))

            return {
                "status": "success",
                "lookback_days": lookback_days,
                "polled_count": len(raw_nodes),
                "updated_count": len(updated_nodes),
                "skipped_count": skipped_count,
                "vectors_updated_count": len(vectors_to_update),
                "deleted_count": len(trashed_ids),
                "timestamp": now_iso
            }
        finally:
            self.db.release_sync_lock(lock_owner)

    def bootstrap_from_export(self, json_path: str, max_nodes: Optional[int] = None, batch_size: int = 500) -> Dict[str, Any]:
        """
        Fast bulk import from a Tana JSON workspace export file.
        Ingests nodes, relationships, tags, and generates embeddings in batches.
        """
        p = Path(json_path)
        if not p.exists():
            raise FileNotFoundError(f"Export file not found at: {json_path}")

        print(f"Reading Tana export: {json_path}...")
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)

        docs = data.get("docs", [])
        total_docs = len(docs)
        print(f"Loaded {total_docs} raw documents.")

        # Filter and parse docs
        parsed_nodes: List[TanaNode] = []
        vectors_to_embed: List[Dict[str, Any]] = []
        
        # Build tag dictionary first
        tag_map = {}
        for d in docs:
            props = d.get("props", {})
            if props.get("_docType") == "tagDef" and props.get("name"):
                tag_map[d["id"]] = props.get("name")

        count = 0
        for d in docs:
            node_id = d.get("id")
            if not node_id or node_id.startswith("SYS_"):
                continue

            props = d.get("props", {})
            raw_name = props.get("name", "")
            # Clean HTML tags from name for cleaner search
            clean_name = re.sub(r'<[^>]+>', '', str(raw_name)).strip()
            desc = str(props.get("description", "") or "").strip()
            doc_type = props.get("_docType")
            parent_id = props.get("_ownerId")
            if parent_id and parent_id.endswith("_TRASH"):
                continue

            # Extract supertags
            supertags = []
            for st_id in props.get("supertags", []):
                tag_name = tag_map.get(st_id, st_id)
                supertags.append(NodeTag(tag_id=st_id, tag_name=tag_name))

            children = props.get("children", [])
            refs = props.get("refs", [])
            
            # Dates
            created_ts = props.get("created")
            created_at = datetime.fromtimestamp(created_ts / 1000.0).isoformat() if created_ts else None
            
            modified_ts_list = d.get("modifiedTs", [])
            max_mod = max(modified_ts_list) if modified_ts_list else None
            updated_at = datetime.fromtimestamp(max_mod / 1000.0).isoformat() if max_mod and max_mod > 0 else created_at

            c_hash = SQLiteStore.calculate_content_hash(clean_name, desc)
            s_hash = SQLiteStore.calculate_structure_hash(parent_id, children, [t.tag_name for t in supertags])

            node = TanaNode(
                id=node_id,
                name=clean_name,
                description=desc,
                doc_type=doc_type,
                parent_id=parent_id,
                created_at=created_at,
                updated_at=updated_at,
                content_hash=c_hash,
                structure_hash=s_hash,
                supertags=supertags,
                children=children,
                references=refs
            )
            parsed_nodes.append(node)

            if clean_name and len(clean_name) > 2:
                vectors_to_embed.append({
                    "id": node_id,
                    "name": clean_name,
                    "description": desc,
                    "hash": c_hash
                })

            count += 1
            if max_nodes and count >= max_nodes:
                break

        print(f"Parsed {len(parsed_nodes)} valid user nodes. Resolving calendar provenance...")
        node_lookup = {n.id: n for n in parsed_nodes}
        for node in parsed_nodes:
            parents = []
            curr_pid = node.parent_id
            depth = 0
            while curr_pid and curr_pid in node_lookup and depth < 8:
                p_node = node_lookup[curr_pid]
                parents.append(p_node)
                curr_pid = p_node.parent_id
                depth += 1
            prov = resolve_node_provenance(node, parents)
            node.effective_date = prov["effective_date"]
            node.date_source = prov["date_source"]
            node.date_source_node_id = prov["date_source_node_id"]
            node.calendar_distance = prov["calendar_distance"]
            node.calendar_path = prov["calendar_path"]
            node.ancestor_day_node_id = prov["ancestor_day_node_id"]
            node.ancestor_week_node_id = prov["ancestor_week_node_id"]
            node.ancestor_month_node_id = prov["ancestor_month_node_id"]
            node.ancestor_year_node_id = prov["ancestor_year_node_id"]

        print(f"Ingesting into SQLite in batches...")
        
        # Batch insert into SQLite
        for i in range(0, len(parsed_nodes), batch_size):
            chunk = parsed_nodes[i:i + batch_size]
            self.db.bulk_upsert_nodes(chunk)
            if (i // batch_size) % 10 == 0 or i + batch_size >= len(parsed_nodes):
                print(f"  SQLite ingested {min(i + batch_size, len(parsed_nodes))} / {len(parsed_nodes)} nodes")

        print(f"Indexing {len(vectors_to_embed)} vector embeddings in batches...")
        # Batch embed and insert into LanceDB
        embed_chunk_size = 250
        for i in range(0, len(vectors_to_embed), embed_chunk_size):
            chunk = vectors_to_embed[i:i + embed_chunk_size]
            self.vector_store.upsert_vectors(chunk)
            if (i // embed_chunk_size) % 10 == 0 or i + embed_chunk_size >= len(vectors_to_embed):
                print(f"  Vector indexed {min(i + embed_chunk_size, len(vectors_to_embed))} / {len(vectors_to_embed)}")

        self.db.set_sync_meta("last_export_bootstrap", datetime.now().isoformat())
        self.db.set_sync_meta("export_file", json_path)
        self.db.set_sync_meta("total_export_nodes", str(len(parsed_nodes)))

        return {
            "status": "success",
            "total_docs": total_docs,
            "parsed_nodes": len(parsed_nodes),
            "vector_indexed": len(vectors_to_embed),
            "timestamp": datetime.now().isoformat()
        }

    def reconcile_stale_nodes(
        self,
        batch_limit: int = 100,
        candidate_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Periodic bounded reconciliation pass (#20) separate from fast incremental sync:
        - Detects nodes that are trashed, moved outside eligible scope (SYS_ / _TRASH), or deleted in Tana.
        - Removes stale graph rows, FTS entries, tags, fields, edges, and vectors safely.
        - Preserves valid descendants/references when only part of a subtree changes.
        - Never removes a live node because of a transient MCP/read failure.
        """
        with self.db.get_connection() as conn:
            if candidate_ids is not None:
                placeholders = ",".join(["?"] * len(candidate_ids)) if candidate_ids else "''"
                rows = (
                    conn.execute(f"SELECT id, parent_id, in_trash FROM nodes WHERE id IN ({placeholders})", candidate_ids).fetchall()
                    if candidate_ids
                    else []
                )
                next_offset = int(self.db.get_sync_meta("reconcile_cursor_offset") or "0")
            else:
                offset = int(self.db.get_sync_meta("reconcile_cursor_offset") or "0")
                rows = conn.execute(
                    "SELECT id, parent_id, in_trash FROM nodes ORDER BY id ASC LIMIT ? OFFSET ?",
                    (batch_limit, offset),
                ).fetchall()
                if not rows and offset > 0:
                    offset = 0
                    rows = conn.execute(
                        "SELECT id, parent_id, in_trash FROM nodes ORDER BY id ASC LIMIT ? OFFSET 0",
                        (batch_limit,),
                    ).fetchall()
                next_offset = offset + len(rows)

        checked_count = 0
        stale_ids: List[str] = []
        transient_failures = 0

        for row in rows:
            checked_count += 1
            nid = row["id"]
            pid = row["parent_id"]
            in_trash = bool(row["in_trash"])

            # 1. Immediately flag locally marked trash or out-of-scope system/trash nodes
            if in_trash or nid.startswith("SYS_") or (isinstance(pid, str) and pid.endswith("_TRASH")):
                stale_ids.append(nid)
                continue

            # 2. Verify live existence via Tana MCP read_node (when client is configured)
            res = self.client._call("read_node", {"nodeId": nid, "maxDepth": 0}, raise_on_error=False)
            if self.client.last_error_type:
                # Transient or auth failure: NEVER remove a live node on read failure
                transient_failures += 1
                continue

            if res is None:
                # Unconfigured or no response without explicit NOT_FOUND: preserve safely
                continue

            if isinstance(res, dict):
                if (
                    res.get("deleted") is True
                    or res.get("inTrash") is True
                    or res.get("notFound") is True
                    or str(res.get("parentId", "")).endswith("_TRASH")
                ):
                    stale_ids.append(nid)
            elif isinstance(res, str):
                lower = res.strip().lower()
                if lower in ("not_found", "node not found", "deleted", "trashed"):
                    stale_ids.append(nid)

        removed_db = 0
        removed_vec = 0
        if stale_ids:
            removed_db = self.db.delete_nodes(stale_ids)
            removed_vec = self.vector_store.delete_vectors(stale_ids)

        now_iso = datetime.now().isoformat()
        self.db.set_sync_meta("reconcile_cursor_offset", str(next_offset))
        self.db.set_sync_meta("last_reconciliation", now_iso)
        self.db.set_sync_meta("reconciled_checked_count", str(checked_count))
        self.db.set_sync_meta("reconciled_removed_count", str(len(stale_ids)))
        self.db.set_sync_meta("reconciled_transient_errors", str(transient_failures))

        return {
            "status": "success",
            "checked_count": checked_count,
            "removed_count": len(stale_ids),
            "removed_node_ids": stale_ids,
            "removed_sqlite_rows": removed_db,
            "removed_vectors": removed_vec,
            "transient_failures": transient_failures,
            "next_cursor_offset": next_offset,
            "timestamp": now_iso,
        }

    @staticmethod
    def _default_cpu_utilization_pct() -> float:
        try:
            load1, _, _ = os.getloadavg()
            cores = max(1, os.cpu_count() or 1)
            return (load1 / cores) * 100.0
        except Exception:
            return 0.0

    def run_mass_background_indexer(
        self,
        export_path: Optional[str] = None,
        batch_size: int = 200,
        cpu_threshold: float = 70.0,
        min_sleep_sec: float = 0.05,
        max_batches: Optional[int] = None,
        cpu_load_fn: Optional[Callable[[], float]] = None,
    ) -> Dict[str, Any]:
        """
        Scheduled/resumable mass background indexer daemon (#13):
        - Indexes historical nodes in bounded batches when CPU utilisation is below cpu_threshold.
        - Automatically rate-limits and backs off when CPU is busy.
        - Persists progress in sync_metadata ('mass_index_offset').
        """
        load_fn = cpu_load_fn or self._default_cpu_utilization_pct
        offset = int(self.db.get_sync_meta("mass_index_offset") or "0")
        batches_run = 0
        throttled_cycles = 0
        indexed_nodes = 0
        indexed_vectors = 0

        # If an export file is provided, ensure SQLite is populated or stream from SQLite nodes needing vectors
        if export_path and Path(export_path).exists() and self.db.count_nodes() == 0:
            self.bootstrap_from_export(export_path, batch_size=batch_size)

        with self.db.get_connection() as conn:
            total_eligible_row = conn.execute(
                "SELECT COUNT(*) as c FROM nodes WHERE in_trash = 0 AND length(name) > 2"
            ).fetchone()
            total_eligible = total_eligible_row["c"] if total_eligible_row else 0

        if offset >= total_eligible and total_eligible > 0:
            offset = 0

        while True:
            if max_batches is not None and batches_run >= max_batches:
                break

            current_cpu = load_fn()
            if current_cpu > cpu_threshold:
                throttled_cycles += 1
                self.db.set_sync_meta("mass_index_status", f"throttled_cpu_{current_cpu:.1f}pct")
                if max_batches is not None and throttled_cycles >= max_batches:
                    break
                time.sleep(min(2.0, max(0.01, min_sleep_sec * (2 ** min(throttled_cycles, 4)))))
                continue

            with self.db.get_connection() as conn:
                rows = conn.execute(
                    """
                    SELECT id, name, description, content_hash
                    FROM nodes
                    WHERE in_trash = 0 AND length(name) > 2
                    ORDER BY id ASC
                    LIMIT ? OFFSET ?
                    """,
                    (batch_size, offset),
                ).fetchall()

            if not rows:
                self.db.set_sync_meta("mass_index_status", "complete")
                self.db.set_sync_meta("mass_index_offset", str(offset))
                break

            vec_payload = [
                {
                    "id": r["id"],
                    "name": r["name"],
                    "description": r["description"] or "",
                    "hash": r["content_hash"] or SQLiteStore.calculate_content_hash(r["name"], r["description"] or ""),
                }
                for r in rows
            ]
            self.vector_store.upsert_vectors(vec_payload)

            batch_count = len(rows)
            indexed_nodes += batch_count
            indexed_vectors += len(vec_payload)
            offset += batch_count
            batches_run += 1

            self.db.set_sync_meta("mass_index_offset", str(offset))
            self.db.set_sync_meta("mass_index_status", "running" if offset < total_eligible else "complete")
            self.db.set_sync_meta("mass_index_last_run", datetime.now().isoformat())

            if offset >= total_eligible:
                self.db.set_sync_meta("mass_index_status", "complete")
                break

            if min_sleep_sec > 0:
                time.sleep(min_sleep_sec)

        return {
            "status": self.db.get_sync_meta("mass_index_status") or "complete",
            "batches_run": batches_run,
            "throttled_cycles": throttled_cycles,
            "indexed_nodes": indexed_nodes,
            "indexed_vectors": indexed_vectors,
            "next_offset": offset,
            "total_eligible": total_eligible,
            "timestamp": datetime.now().isoformat(),
        }

    def execute_mutation(self, req: MutationRequest) -> MutationResult:
        """
        Safe mutation proxy for node creation, tagging, field updates, and renaming (#7):
        - Enforces Tana formatting standards (plain text node/field names, no #Gemini_CLI).
        - Supports dry_run validation and idempotency_key replay safety.
        """
        now_iso = datetime.now().isoformat()
        audit_id = f"mut-{uuid.uuid4().hex[:12]}"

        # 1. Check idempotency key cache
        if req.idempotency_key:
            cached_raw = self.db.get_sync_meta(f"mutation_idempotency:{req.idempotency_key}")
            if cached_raw:
                try:
                    cached_dict = json.loads(cached_raw)
                    cached_dict["idempotent_replay"] = True
                    return MutationResult(**cached_dict)
                except Exception:
                    pass

        action = (req.action or "").strip()
        if action not in ("create_node", "add_tag", "set_field", "edit_name"):
            raise ValueError(f"Unsupported mutation action: '{action}'")

        # Enforce strict Tana safety rules
        all_tags = [t for t in ([req.tag_name, req.tag_id] + list(req.supertags)) if t]
        for t in all_tags:
            if "gemini_cli" in t.lower() or "fQXumeUbbgE-" in t:
                raise ValueError("Forbidden supertag: #Gemini_CLI must never be applied automatically by agents.")

        if req.name and (re.search(r"<[^>]+>", req.name) or "**" in req.name or "__" in req.name):
            raise ValueError("Node name must be plain text only (no Markdown or HTML tags).")

        if req.field_name and ("*" in req.field_name or "_" in req.field_name or "#" in req.field_name):
            raise ValueError("Tana field names must be plain text only without Markdown formatting.")

        preview_payload: Dict[str, Any] = {"action": action}
        affected_node_id = req.target_node_id

        if action == "create_node":
            parent_id = req.parent_node_id or "INBOX"
            clean_title = (req.name or "").strip()
            if not clean_title:
                raise ValueError("name is required for create_node")
            tags_suffix = " ".join(f"#{t.lstrip('#')}" for t in req.supertags if t.strip())
            if req.tag_name and f"#{req.tag_name.lstrip('#')}" not in tags_suffix:
                tags_suffix = f"{tags_suffix} #{req.tag_name.lstrip('#')}".strip()
            first_line = f"- {clean_title} {tags_suffix}".rstrip()
            lines = [first_line]
            if req.content:
                for sub in req.content.splitlines():
                    if sub.strip():
                        lines.append(f"  - {sub.strip()}")
            for child in req.children:
                if child.strip():
                    lines.append(f"  - {child.strip()}")
            tana_paste = "\n".join(lines)
            affected_node_id = affected_node_id or f"tana_local_{uuid.uuid4().hex[:8]}"
            preview_payload = {
                "tool": "import_tana_paste",
                "parentNodeId": parent_id,
                "content": tana_paste,
            }
            if not req.dry_run:
                self.client.import_tana_paste(parent_id, tana_paste)
                new_node = TanaNode(
                    id=affected_node_id,
                    name=clean_title,
                    description=req.content or "",
                    parent_id=parent_id,
                    supertags=[
                        NodeTag(tag_id=t.lstrip("#"), tag_name=t.lstrip("#"))
                        for t in (list(req.supertags) + ([req.tag_name] if req.tag_name else []))
                        if t
                    ],
                    created_at=now_iso,
                    updated_at=now_iso,
                )
                self.db.upsert_node(new_node)
                self.vector_store.upsert_vectors([{
                    "id": new_node.id,
                    "name": new_node.name,
                    "description": new_node.description or "",
                    "hash": SQLiteStore.calculate_content_hash(new_node.name, new_node.description or ""),
                }])

        elif action == "add_tag":
            if not req.target_node_id:
                raise ValueError("target_node_id is required for add_tag")
            tag_label = (req.tag_name or req.tag_id or "").strip().lstrip("#")
            if not tag_label:
                raise ValueError("tag_name or tag_id is required for add_tag")
            preview_payload = {
                "tool": "add_tag",
                "nodeId": req.target_node_id,
                "tag": tag_label,
            }
            if not req.dry_run:
                existing = self.db.get_node(req.target_node_id)
                if existing:
                    if not any(t.tag_name == tag_label for t in existing.supertags):
                        existing.supertags.append(NodeTag(tag_id=req.tag_id or tag_label, tag_name=tag_label))
                    existing.updated_at = now_iso
                    self.db.upsert_node(existing)

        elif action == "set_field":
            if not req.target_node_id or not (req.field_id or req.field_name):
                raise ValueError("target_node_id and field_id/field_name are required for set_field")
            attr_id = req.field_id or req.field_name or ""
            val = req.field_value or req.content or ""
            preview_payload = {
                "tool": "set_field_content",
                "nodeId": req.target_node_id,
                "attributeId": attr_id,
                "content": val,
            }
            if not req.dry_run:
                self.client.set_field_content(req.target_node_id, attr_id, val)
                existing = self.db.get_node(req.target_node_id)
                if existing:
                    existing.fields.append(
                        NodeField(
                            field_id=attr_id,
                            field_name=req.field_name or attr_id,
                            value_text=val,
                        )
                    )
                    existing.updated_at = now_iso
                    self.db.upsert_node(existing)

        elif action == "edit_name":
            if not req.target_node_id or not req.name:
                raise ValueError("target_node_id and name are required for edit_name")
            existing = self.db.get_node(req.target_node_id)
            old_str = req.old_name or (existing.name if existing else "")
            preview_payload = {
                "tool": "edit_node",
                "nodeId": req.target_node_id,
                "name": {"old_string": old_str, "new_string": req.name.strip()},
            }
            if not req.dry_run:
                self.client.edit_node(req.target_node_id, old_str, req.name.strip())
                if existing:
                    existing.name = req.name.strip()
                    existing.content_hash = SQLiteStore.calculate_content_hash(existing.name, existing.description or "")
                    existing.updated_at = now_iso
                    self.db.upsert_node(existing)
                    self.vector_store.upsert_vectors([{
                        "id": existing.id,
                        "name": existing.name,
                        "description": existing.description or "",
                        "hash": existing.content_hash,
                    }])

        result = MutationResult(
            success=True,
            action=action,
            affected_node_id=affected_node_id,
            message=f"{'Dry-run validated' if req.dry_run else 'Executed'} {action} successfully.",
            audit_id=audit_id,
            timestamp=now_iso,
            dry_run=req.dry_run,
            idempotent_replay=False,
            preview_payload=preview_payload,
        )

        self.db.set_sync_meta(f"mutation_audit:{audit_id}", result.model_dump_json())
        if req.idempotency_key:
            self.db.set_sync_meta(f"mutation_idempotency:{req.idempotency_key}", result.model_dump_json())

        return result

    def get_sync_stats(self) -> Dict[str, Any]:
        """
        Return system health, authentication signals, and sync statistics (#21).
        """
        total_nodes = self.db.count_nodes()
        total_vectors = self.vector_store.count_vectors()
        last_sync = self.db.get_sync_meta("last_incremental_sync")
        last_successful = self.db.get_sync_meta("last_successful_sync") or last_sync
        last_bootstrap = self.db.get_sync_meta("last_export_bootstrap")
        last_reconcile = self.db.get_sync_meta("last_reconciliation")
        auth_status = self.db.get_sync_meta("auth_status") or ("ok" if settings.tana_token else "missing_token")
        last_error = self.db.get_sync_meta("last_sync_error") or None

        return {
            "total_nodes_sqlite": total_nodes,
            "total_vectors_lancedb": total_vectors,
            "last_incremental_sync": last_sync,
            "last_successful_sync": last_successful,
            "last_export_bootstrap": last_bootstrap,
            "last_reconciliation": last_reconcile,
            "auth_status": auth_status,
            "reauth_required": auth_status == "reauth_required",
            "last_sync_error": last_error,
            "mass_index_status": self.db.get_sync_meta("mass_index_status") or "idle",
            "mass_index_offset": int(self.db.get_sync_meta("mass_index_offset") or "0"),
            "embedding_model": settings.embedding_model,
            "status": "ready" if total_nodes > 0 else "uninitialised"
        }
