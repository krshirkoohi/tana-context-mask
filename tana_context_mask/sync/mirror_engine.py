import os
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple
from ..config import settings
from ..storage.db import SQLiteStore
from ..storage.vector_store import VectorStore
from ..client.tana_mcp_client import TanaMCPClient
from ..models.node import TanaNode, NodeTag, NodeField
from ..engine.temporal import resolve_node_provenance

class MirrorEngine:
    def __init__(self, db: Optional[SQLiteStore] = None, vector_store: Optional[VectorStore] = None, client: Optional[TanaMCPClient] = None):
        self.db = db or SQLiteStore()
        self.vector_store = vector_store or VectorStore()
        self.client = client or TanaMCPClient()

    def sync_incremental(self, lookback_days: int = 1) -> Dict[str, Any]:
        """
        Poll Tana Remote MCP across multiple facets (created, edited, day calendar nodes)
        and update local mirror and vector index with full coverage.
        """
        node_map: Dict[str, Dict[str, Any]] = {}

        # Facet 1: Created nodes in lookback
        created_nodes = self.client.search_nodes({"created": {"last": lookback_days}}, limit=1000) or []
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
        
        for raw in raw_nodes:
            node_id = raw.get("id")
            if not node_id:
                continue
            
            raw_name = raw.get("name", "")
            name = re.sub(r'<[^>]+>', '', str(raw_name)).strip()
            desc = (raw.get("description") or "").strip()
            parent_id = raw.get("parentId")
            created_at = raw.get("created")
            doc_type = raw.get("docType")
            
            # Check existing content hash
            existing_node = self.db.get_node(node_id)
            c_hash = SQLiteStore.calculate_content_hash(name, desc)
            
            if existing_node and existing_node.content_hash == c_hash and existing_node.parent_id == parent_id:
                continue # No content or hierarchy change
            
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
            s_hash = SQLiteStore.calculate_structure_hash(parent_id, children, [t.tag_name for t in supertags])

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
            
            if name or desc:
                vectors_to_update.append({
                    "id": node_id,
                    "name": name,
                    "description": desc,
                    "hash": c_hash
                })

        if updated_nodes:
            self.db.bulk_upsert_nodes(updated_nodes)
        
        if vectors_to_update:
            self.vector_store.upsert_vectors(vectors_to_update)

        self.db.set_sync_meta("last_incremental_sync", datetime.now().isoformat())
        self.db.set_sync_meta("last_incremental_count", str(len(updated_nodes)))

        return {
            "status": "success",
            "lookback_days": lookback_days,
            "polled_count": len(raw_nodes),
            "updated_count": len(updated_nodes),
            "timestamp": datetime.now().isoformat()
        }

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

    def get_sync_stats(self) -> Dict[str, Any]:
        """
        Return system health and sync statistics.
        """
        total_nodes = self.db.count_nodes()
        total_vectors = self.vector_store.count_vectors()
        last_sync = self.db.get_sync_meta("last_incremental_sync")
        last_bootstrap = self.db.get_sync_meta("last_export_bootstrap")

        return {
            "total_nodes_sqlite": total_nodes,
            "total_vectors_lancedb": total_vectors,
            "last_incremental_sync": last_sync,
            "last_export_bootstrap": last_bootstrap,
            "embedding_model": settings.embedding_model,
            "status": "ready" if total_nodes > 0 else "uninitialised"
        }
