#!/usr/bin/env python3
"""
Tana Export to Cloudflare D1 & Vectorize Seeder
Reads a local Tana JSON export and outputs:
1. `d1_seed.sql`: SQL batch file to execute into Cloudflare D1.
2. `vectors_seed.jsonl`: JSONL file to upload into Cloudflare Vectorize.
"""

import sys
import json
import re
from pathlib import Path
from datetime import datetime

def parse_export(json_path: str, output_dir: str = "."):
    p = Path(json_path)
    if not p.exists():
        print(f"Error: File not found: {json_path}")
        sys.exit(1)

    print(f"Reading export from {json_path}...")
    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f)

    docs = data.get("docs", [])
    print(f"Loaded {len(docs)} documents.")

    tag_map = {}
    for d in docs:
        props = d.get("props", {})
        if props.get("_docType") == "tagDef" and props.get("name"):
            tag_map[d["id"]] = props.get("name")

    out_p = Path(output_dir)
    out_p.mkdir(parents=True, exist_ok=True)
    sql_file = out_p / "d1_seed.sql"

    sql_statements = []

    count = 0
    for d in docs:
        node_id = d.get("id")
        if not node_id or node_id.startswith("SYS_"):
            continue

        props = d.get("props", {})
        raw_name = props.get("name", "")
        clean_name = re.sub(r'<[^>]+>', '', str(raw_name)).strip().replace("'", "''")
        desc = str(props.get("description", "") or "").strip().replace("'", "''")
        parent_id = props.get("_ownerId", "")
        if parent_id and parent_id.endswith("_TRASH"):
            continue

        if not clean_name:
            continue

        # Insert Node
        sql_statements.append(
            f"INSERT OR REPLACE INTO nodes (id, name, description, parent_id) "
            f"VALUES ('{node_id}', '{clean_name}', '{desc}', '{parent_id}');"
        )
        # Insert FTS
        sql_statements.append(
            f"INSERT INTO node_fts (id, name, description) VALUES ('{node_id}', '{clean_name}', '{desc}');"
        )

        # Tags
        for st_id in props.get("supertags", []):
            tag_name = tag_map.get(st_id, st_id).replace("'", "''")
            sql_statements.append(
                f"INSERT OR REPLACE INTO tags (node_id, tag_id, tag_name) VALUES ('{node_id}', '{st_id}', '{tag_name}');"
            )

        # Edges
        if parent_id:
            sql_statements.append(
                f"INSERT OR IGNORE INTO edges (source_id, target_id, relation_type) VALUES ('{parent_id}', '{node_id}', 'parent_child');"
            )

        for child_id in props.get("children", []):
            sql_statements.append(
                f"INSERT OR IGNORE INTO edges (source_id, target_id, relation_type) VALUES ('{node_id}', '{child_id}', 'parent_child');"
            )

        for ref_id in props.get("refs", []):
            sql_statements.append(
                f"INSERT OR IGNORE INTO edges (source_id, target_id, relation_type) VALUES ('{node_id}', '{ref_id}', 'reference');"
            )

        count += 1

    print(f"Writing {len(sql_statements)} SQL statements to {sql_file}...")
    with open(sql_file, "w", encoding="utf-8") as f:
        f.write("\n".join(sql_statements))

    print(f"Done! Seed file generated for {count} nodes.")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 export_to_cf.py <path_to_tana_export.json> [output_dir]")
        sys.exit(1)
    
    out_directory = sys.argv[2] if len(sys.argv) > 2 else "worker"
    parse_export(sys.argv[1], out_directory)
