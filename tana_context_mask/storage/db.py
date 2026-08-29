import sqlite3
import json
import hashlib
import re
from typing import List, Dict, Optional, Tuple, Any
from pathlib import Path
from datetime import datetime
from ..config import settings
from ..models.node import TanaNode, NodeTag, NodeField, NodeEdge
from ..models.context import TraceRecord

class SQLiteStore:
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or settings.sqlite_db_path
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.init_db()

    def get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def init_db(self):
        with self.get_connection() as conn:
            # Nodes Table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS nodes (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    doc_type TEXT,
                    parent_id TEXT,
                    created_at TEXT,
                    updated_at TEXT,
                    content_hash TEXT,
                    structure_hash TEXT,
                    is_done INTEGER DEFAULT 0,
                    in_trash INTEGER DEFAULT 0,
                    raw_json TEXT
                )
            """)
            
            # Indexes on parent and status
            conn.execute("CREATE INDEX IF NOT EXISTS idx_nodes_parent ON nodes(parent_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_nodes_doc_type ON nodes(doc_type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_nodes_trash ON nodes(in_trash)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_nodes_updated ON nodes(updated_at)")

            # Edges Table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS edges (
                    source_id TEXT,
                    target_id TEXT,
                    relation_type TEXT,
                    attribute_id TEXT DEFAULT '',
                    attribute_name TEXT DEFAULT '',
                    PRIMARY KEY (source_id, target_id, relation_type, attribute_id)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_edges_rel ON edges(relation_type)")

            # Tags Table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tags (
                    node_id TEXT,
                    tag_id TEXT,
                    tag_name TEXT,
                    PRIMARY KEY (node_id, tag_id)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tags_node ON tags(node_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tags_name ON tags(tag_name)")

            # Fields Table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS fields (
                    node_id TEXT,
                    field_id TEXT,
                    field_name TEXT,
                    value_text TEXT,
                    value_node_id TEXT,
                    PRIMARY KEY (node_id, field_id, value_text, value_node_id)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_fields_node ON fields(node_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_fields_name ON fields(field_name)")

            # Traces Table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS retrieval_traces (
                    trace_id TEXT PRIMARY KEY,
                    task TEXT,
                    inferred_query TEXT,
                    data_json TEXT,
                    latency_ms REAL,
                    created_at TEXT
                )
            """)

            # Metadata Table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sync_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TEXT
                )
            """)

            # Full-Text Search Table (FTS5)
            try:
                conn.execute("""
                    CREATE VIRTUAL TABLE IF NOT EXISTS node_fts USING fts5(
                        id UNINDEXED,
                        name,
                        description,
                        tokenize='porter unicode61'
                    )
                """)
            except sqlite3.OperationalError:
                # Fallback if fts5 without unicode options
                conn.execute("""
                    CREATE VIRTUAL TABLE IF NOT EXISTS node_fts USING fts5(
                        id UNINDEXED,
                        name,
                        description
                    )
                """)
            conn.commit()

    @staticmethod
    def calculate_content_hash(name: str, description: str = "", fields: Optional[List[NodeField]] = None) -> str:
        f_str = ""
        if fields:
            f_str = "|".join(f"{f.field_name}:{f.value_text or f.value_node_id or ''}" for f in sorted(fields, key=lambda x: x.field_id))
        raw = f"{name.strip()}|{(description or '').strip()}|{f_str}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def calculate_structure_hash(parent_id: Optional[str], children: List[str], supertags: List[str]) -> str:
        c_str = ",".join(sorted(children))
        t_str = ",".join(sorted(supertags))
        raw = f"{parent_id or ''}|{c_str}|{t_str}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def upsert_node(self, node: TanaNode):
        self.bulk_upsert_nodes([node])

    def bulk_upsert_nodes(self, nodes: List[TanaNode]):
        if not nodes:
            return

        with self.get_connection() as conn:
            for node in nodes:
                c_hash = node.content_hash or self.calculate_content_hash(node.name, node.description or "", node.fields)
                s_hash = node.structure_hash or self.calculate_structure_hash(node.parent_id, node.children, [t.tag_name for t in node.supertags])
                
                # Upsert into nodes
                conn.execute("""
                    INSERT INTO nodes (
                        id, name, description, doc_type, parent_id,
                        created_at, updated_at, content_hash, structure_hash,
                        is_done, in_trash, raw_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        name = excluded.name,
                        description = excluded.description,
                        doc_type = excluded.doc_type,
                        parent_id = excluded.parent_id,
                        updated_at = excluded.updated_at,
                        content_hash = excluded.content_hash,
                        structure_hash = excluded.structure_hash,
                        is_done = excluded.is_done,
                        in_trash = excluded.in_trash
                """, (
                    node.id, node.name, node.description or "", node.doc_type, node.parent_id,
                    node.created_at or datetime.now().isoformat(),
                    node.updated_at or datetime.now().isoformat(),
                    c_hash, s_hash,
                    1 if node.is_done else 0,
                    1 if node.in_trash else 0,
                    None
                ))

                # Update FTS
                conn.execute("DELETE FROM node_fts WHERE id = ?", (node.id,))
                if not node.in_trash and node.name:
                    conn.execute("INSERT INTO node_fts (id, name, description) VALUES (?, ?, ?)",
                                 (node.id, node.name, node.description or ""))

                # Update Tags
                conn.execute("DELETE FROM tags WHERE node_id = ?", (node.id,))
                for tag in node.supertags:
                    conn.execute("INSERT OR REPLACE INTO tags (node_id, tag_id, tag_name) VALUES (?, ?, ?)",
                                 (node.id, tag.tag_id, tag.tag_name))

                # Update Fields
                conn.execute("DELETE FROM fields WHERE node_id = ?", (node.id,))
                for field in node.fields:
                    conn.execute("INSERT OR REPLACE INTO fields (node_id, field_id, field_name, value_text, value_node_id) VALUES (?, ?, ?, ?, ?)",
                                 (node.id, field.field_id, field.field_name, field.value_text or "", field.value_node_id or ""))

                # Update Edges
                conn.execute("DELETE FROM edges WHERE source_id = ?", (node.id,))
                if node.parent_id:
                    conn.execute("INSERT OR IGNORE INTO edges (source_id, target_id, relation_type, attribute_id) VALUES (?, ?, 'parent_child', '')",
                                 (node.parent_id, node.id))
                for child_id in node.children:
                    conn.execute("INSERT OR IGNORE INTO edges (source_id, target_id, relation_type, attribute_id) VALUES (?, ?, 'parent_child', '')",
                                 (node.id, child_id))
                for ref_id in node.references:
                    conn.execute("INSERT OR IGNORE INTO edges (source_id, target_id, relation_type, attribute_id) VALUES (?, ?, 'reference', '')",
                                 (node.id, ref_id))
                for tag in node.supertags:
                    conn.execute("INSERT OR IGNORE INTO edges (source_id, target_id, relation_type, attribute_id) VALUES (?, ?, 'tag', '')",
                                 (node.id, tag.tag_id))
            conn.commit()

    def get_node(self, node_id: str) -> Optional[TanaNode]:
        with self.get_connection() as conn:
            row = conn.execute("SELECT * FROM nodes WHERE id = ?", (node_id,)).fetchone()
            if not row:
                return None
            return self._row_to_node(conn, row)

    def _row_to_node(self, conn: sqlite3.Connection, row: sqlite3.Row) -> TanaNode:
        node_id = row["id"]
        
        # Supertags
        tag_rows = conn.execute("SELECT DISTINCT tag_id, tag_name FROM tags WHERE node_id = ?", (node_id,)).fetchall()
        supertags = [NodeTag(tag_id=r["tag_id"], tag_name=r["tag_name"]) for r in tag_rows]

        # Fields
        field_rows = conn.execute("SELECT DISTINCT field_id, field_name, value_text, value_node_id FROM fields WHERE node_id = ?", (node_id,)).fetchall()
        fields = [NodeField(field_id=r["field_id"], field_name=r["field_name"], value_text=r["value_text"], value_node_id=r["value_node_id"]) for r in field_rows]

        # Children
        child_rows = conn.execute("SELECT DISTINCT target_id FROM edges WHERE source_id = ? AND relation_type = 'parent_child'", (node_id,)).fetchall()
        children = [r["target_id"] for r in child_rows]

        # References
        ref_rows = conn.execute("SELECT DISTINCT target_id FROM edges WHERE source_id = ? AND relation_type = 'reference'", (node_id,)).fetchall()
        references = [r["target_id"] for r in ref_rows]

        # Backlinks (nodes that reference this node)
        backlink_rows = conn.execute("SELECT DISTINCT source_id FROM edges WHERE target_id = ? AND relation_type = 'reference'", (node_id,)).fetchall()
        backlinks = [r["source_id"] for r in backlink_rows]

        # Breadcrumbs (computed dynamically or up to 5 steps)
        breadcrumbs = self._get_breadcrumb_names(conn, row["parent_id"])

        return TanaNode(
            id=node_id,
            name=row["name"],
            description=row["description"],
            doc_type=row["doc_type"],
            parent_id=row["parent_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            content_hash=row["content_hash"],
            structure_hash=row["structure_hash"],
            is_done=bool(row["is_done"]),
            in_trash=bool(row["in_trash"]),
            supertags=supertags,
            fields=fields,
            children=children,
            references=references,
            backlinks=backlinks,
            breadcrumbs=breadcrumbs
        )

    def _get_breadcrumb_names(self, conn: sqlite3.Connection, start_parent_id: Optional[str], max_depth: int = 8) -> List[str]:
        crumbs = []
        curr_id = start_parent_id
        depth = 0
        while curr_id and depth < max_depth:
            p_row = conn.execute("SELECT id, name, parent_id FROM nodes WHERE id = ?", (curr_id,)).fetchone()
            if not p_row:
                break
            clean_name = re.sub(r'<[^>]+>', '', p_row["name"]).strip()
            if clean_name:
                crumbs.append(clean_name)
            curr_id = p_row["parent_id"]
            depth += 1
        return list(reversed(crumbs))

    def get_children(self, node_id: str, limit: int = 50) -> List[TanaNode]:
        with self.get_connection() as conn:
            rows = conn.execute("""
                SELECT DISTINCT n.* FROM nodes n
                JOIN edges e ON n.id = e.target_id
                WHERE e.source_id = ? AND e.relation_type = 'parent_child' AND n.in_trash = 0
                LIMIT ?
            """, (node_id, limit)).fetchall()
            return [self._row_to_node(conn, r) for r in rows]

    def get_parents_chain(self, node_id: str, max_depth: int = 10) -> List[TanaNode]:
        parents = []
        with self.get_connection() as conn:
            curr_row = conn.execute("SELECT parent_id FROM nodes WHERE id = ?", (node_id,)).fetchone()
            curr_id = curr_row["parent_id"] if curr_row else None
            depth = 0
            while curr_id and depth < max_depth:
                p_row = conn.execute("SELECT * FROM nodes WHERE id = ?", (curr_id,)).fetchone()
                if not p_row:
                    break
                parents.append(self._row_to_node(conn, p_row))
                curr_id = p_row["parent_id"]
                depth += 1
        return parents

    def get_references(self, node_id: str) -> List[TanaNode]:
        with self.get_connection() as conn:
            rows = conn.execute("""
                SELECT DISTINCT n.* FROM nodes n
                JOIN edges e ON n.id = e.target_id
                WHERE e.source_id = ? AND e.relation_type = 'reference' AND n.in_trash = 0
            """, (node_id,)).fetchall()
            return [self._row_to_node(conn, r) for r in rows]

    def get_backlinks(self, node_id: str) -> List[TanaNode]:
        with self.get_connection() as conn:
            rows = conn.execute("""
                SELECT DISTINCT n.* FROM nodes n
                JOIN edges e ON n.id = e.source_id
                WHERE e.target_id = ? AND e.relation_type = 'reference' AND n.in_trash = 0
            """, (node_id,)).fetchall()
            return [self._row_to_node(conn, r) for r in rows]

    def fts_search(self, query: str, limit: int = 25, tag_filter: Optional[str] = None) -> List[Tuple[TanaNode, float]]:
        # Sanitise query for FTS5
        clean_terms = re.findall(r'\w+', query)
        if not clean_terms:
            return []
        
        fts_query = " OR ".join(f'"{t}"*' for t in clean_terms)
        results = []
        with self.get_connection() as conn:
            if tag_filter:
                sql = """
                    SELECT n.*, bm25(node_fts) as rank_score
                    FROM node_fts f
                    JOIN nodes n ON f.id = n.id
                    JOIN tags t ON n.id = t.node_id
                    WHERE node_fts MATCH ? AND n.in_trash = 0 AND t.tag_name LIKE ?
                    ORDER BY rank_score ASC
                    LIMIT ?
                """
                rows = conn.execute(sql, (fts_query, f"%{tag_filter}%", limit)).fetchall()
            else:
                sql = """
                    SELECT n.*, bm25(node_fts) as rank_score
                    FROM node_fts f
                    JOIN nodes n ON f.id = n.id
                    WHERE node_fts MATCH ? AND n.in_trash = 0
                    ORDER BY rank_score ASC
                    LIMIT ?
                """
                rows = conn.execute(sql, (fts_query, limit)).fetchall()

            for r in rows:
                node = self._row_to_node(conn, r)
                # BM25 scores in sqlite are negative (lower is better, e.g. -5.2), convert to positive normalised
                raw_bm25 = abs(float(r["rank_score"]))
                norm_score = 1.0 / (1.0 + raw_bm25)
                results.append((node, norm_score))
        return results

    def get_all_nodes(self, limit: Optional[int] = None) -> List[TanaNode]:
        with self.get_connection() as conn:
            if limit:
                rows = conn.execute("SELECT * FROM nodes WHERE in_trash = 0 LIMIT ?", (limit,)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM nodes WHERE in_trash = 0").fetchall()
            return [self._row_to_node(conn, r) for r in rows]

    def count_nodes(self) -> int:
        with self.get_connection() as conn:
            row = conn.execute("SELECT COUNT(*) as count FROM nodes WHERE in_trash = 0").fetchone()
            return row["count"] if row else 0

    def save_trace(self, trace: TraceRecord):
        with self.get_connection() as conn:
            data_json = json.dumps({
                "seed_nodes": trace.seed_nodes,
                "expanded_nodes": trace.expanded_nodes,
                "reranked_nodes": trace.reranked_nodes,
                "excluded_nodes": trace.excluded_nodes
            })
            conn.execute("""
                INSERT OR REPLACE INTO retrieval_traces (
                    trace_id, task, inferred_query, data_json, latency_ms, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
            """, (
                trace.trace_id, trace.task, trace.inferred_query,
                data_json, trace.latency_ms, trace.created_at
            ))
            conn.commit()

    def get_trace(self, trace_id: str) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            row = conn.execute("SELECT * FROM retrieval_traces WHERE trace_id = ?", (trace_id,)).fetchone()
            if not row:
                return None
            data = json.loads(row["data_json"])
            return {
                "trace_id": row["trace_id"],
                "task": row["task"],
                "inferred_query": row["inferred_query"],
                "latency_ms": row["latency_ms"],
                "created_at": row["created_at"],
                **data
            }

    def get_sync_meta(self, key: str) -> Optional[str]:
        with self.get_connection() as conn:
            row = conn.execute("SELECT value FROM sync_metadata WHERE key = ?", (key,)).fetchone()
            return row["value"] if row else None

    def set_sync_meta(self, key: str, value: str):
        with self.get_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO sync_metadata (key, value, updated_at)
                VALUES (?, ?, ?)
            """, (key, value, datetime.now().isoformat()))
            conn.commit()
