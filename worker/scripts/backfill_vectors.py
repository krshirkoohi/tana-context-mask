#!/usr/bin/env python3
"""
Mass Vector Backfill Tool for Tana Context Mask
Fetches nodes from Cloudflare D1, generates 384-dim BGE embeddings locally using FastEmbed,
and upserts them in bulk directly to Cloudflare Vectorize (tana-nodes-index).
"""

import os
import sys
import json
import time
import subprocess
from pathlib import Path

# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tana_context_mask.storage.vector_store import VectorStore

WORKER_DIR = BASE_DIR / "worker"
NDJSON_FILE = Path("/tmp/tana_all_vectors.ndjson")
INDEX_NAME = "tana-nodes-index"
FETCH_BATCH_SIZE = 2500
EMBED_BATCH_SIZE = 256


def execute_d1_query(query: str):
    cmd = [
        "npx", "wrangler", "d1", "execute", "tana-db",
        "--remote", "--json",
        "--command", query
    ]
    res = subprocess.run(
        cmd,
        cwd=str(WORKER_DIR),
        capture_output=True,
        text=True,
        check=True
    )
    data = json.loads(res.stdout)
    if isinstance(data, list) and len(data) > 0 and "results" in data[0]:
        return data[0]["results"]
    return []


def main():
    print("=" * 60)
    print("🚀 Starting Tana Local Vector Backfill to Cloudflare Vectorize")
    print("=" * 60)

    print("📦 Loading local FastEmbed BGE model (BAAI/bge-small-en-v1.5)...")
    t0 = time.time()
    vs = VectorStore()
    _ = vs.model # trigger model load
    print(f"✅ FastEmbed model loaded in {time.time() - t0:.2f}s")

    last_id = ""
    total_fetched = 0
    total_embedded = 0

    if NDJSON_FILE.exists():
        NDJSON_FILE.unlink()

    print("\n🔍 Fetching nodes from Cloudflare D1 and generating embeddings...")
    start_time = time.time()

    with open(NDJSON_FILE, "a", encoding="utf-8") as ndjson_out:
        batch_num = 0
        while True:
            batch_num += 1
            if not last_id:
                query = f"SELECT id, name, description, in_trash FROM nodes ORDER BY id ASC LIMIT {FETCH_BATCH_SIZE};"
            else:
                escaped_id = last_id.replace("'", "''")
                query = f"SELECT id, name, description, in_trash FROM nodes WHERE id > '{escaped_id}' ORDER BY id ASC LIMIT {FETCH_BATCH_SIZE};"

            print(f"  [Batch {batch_num}] Fetching up to {FETCH_BATCH_SIZE} nodes from D1 (cursor: '{last_id}')...")
            rows = execute_d1_query(query)
            if not rows:
                print("  🏁 No more nodes returned. Completed D1 extraction.")
                break

            total_fetched += len(rows)
            last_id = rows[-1]["id"]

            # Filter valid nodes
            valid_nodes = []
            for r in rows:
                if r.get("in_trash") == 1:
                    continue
                name = (r.get("name") or "").strip()
                desc = (r.get("description") or "").strip()
                if not name and not desc:
                    continue
                text = f"{name}\n{desc}".strip() if desc else name
                valid_nodes.append((r["id"], name, text))

            if not valid_nodes:
                continue

            # Batch embed
            texts = [n[2] for n in valid_nodes]
            embed_t0 = time.time()
            embeddings = vs.embed_batch(texts, batch_size=EMBED_BATCH_SIZE)
            embed_duration = time.time() - embed_t0

            for (node_id, node_name, _), vec in zip(valid_nodes, embeddings):
                record = {
                    "id": node_id,
                    "values": vec,
                    "metadata": {"name": node_name[:100]}
                }
                ndjson_out.write(json.dumps(record) + "\n")

            total_embedded += len(valid_nodes)
            print(f"  ✨ Embedded {len(valid_nodes)} nodes in {embed_duration:.2f}s (Total so far: {total_embedded})")

            if len(rows) < FETCH_BATCH_SIZE:
                print("  🏁 Reached end of table.")
                break

    extraction_time = time.time() - start_time
    file_size_mb = NDJSON_FILE.stat().st_size / (1024 * 1024)
    print(f"\n🎉 Extraction & Embedding Complete in {extraction_time:.2f}s!")
    print(f"📊 Total nodes fetched from D1: {total_fetched}")
    print(f"📊 Total vector records created: {total_embedded}")
    print(f"📁 Output file: {NDJSON_FILE} ({file_size_mb:.2f} MB)")

    print("\n⚡ Upserting vectors to Cloudflare Vectorize (index: tana-nodes-index)...")
    upsert_t0 = time.time()
    upsert_cmd = [
        "npx", "wrangler", "vectorize", "upsert", INDEX_NAME,
        "--file", str(NDJSON_FILE),
        "--batch-size", "5000"
    ]
    upsert_res = subprocess.run(
        upsert_cmd,
        cwd=str(WORKER_DIR),
        capture_output=True,
        text=True
    )
    print(upsert_res.stdout)
    if upsert_res.stderr:
        print(upsert_res.stderr)

    if upsert_res.returncode != 0:
        print("❌ Error during Vectorize upsert.")
        sys.exit(1)

    print(f"✅ Vectorize upsert complete in {time.time() - upsert_t0:.2f}s!")
    print("=" * 60)
    print("🏁 Mass Vector Backfill Successfully Finished!")
    print("=" * 60)


if __name__ == "__main__":
    main()
