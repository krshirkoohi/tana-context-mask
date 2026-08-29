import os
import lancedb
import numpy as np
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
from fastembed import TextEmbedding
from ..config import settings

class VectorStore:
    def __init__(self, db_path: Optional[str] = None, model_name: Optional[str] = None):
        self.db_path = db_path or settings.lance_db_path
        self.model_name = model_name or settings.embedding_model
        self.model_cache = settings.model_cache_path
        self.table_name = "tana_nodes"
        
        # Ensure directories
        Path(self.db_path).mkdir(parents=True, exist_ok=True)
        Path(self.model_cache).mkdir(parents=True, exist_ok=True)

        # Initialize FastEmbed model
        self._model = None
        self._db = None
        self._table = None
        self._in_memory_vectors = {} # fallback cache: id -> {vector, id, name, description, hash}

    @property
    def model(self) -> TextEmbedding:
        if self._model is None:
            self._model = TextEmbedding(
                model_name=self.model_name,
                cache_dir=self.model_cache
            )
        return self._model

    @property
    def db(self):
        if self._db is None:
            self._db = lancedb.connect(self.db_path)
        return self._db

    def _get_table(self):
        if self._table is None:
            try:
                self._table = self.db.open_table(self.table_name)
            except Exception:
                self._table = None
        return self._table

    def embed_text(self, text: str) -> List[float]:
        clean_text = text.strip() or "empty"
        vectors = list(self.model.embed([clean_text]))
        return [float(v) for v in vectors[0]]

    def embed_batch(self, texts: List[str], batch_size: int = 128) -> List[List[float]]:
        clean_texts = [t.strip() or "empty" for t in texts]
        if not clean_texts:
            return []
        vectors = list(self.model.embed(clean_texts, batch_size=batch_size))
        return [[float(v) for v in vec] for vec in vectors]

    def upsert_vectors(self, records: List[Dict[str, Any]]):
        """
        records: list of dicts with keys: id, name, description, hash, text (to embed if vector not supplied), vector (optional)
        """
        if not records:
            return

        # Prepare vectors
        needs_embedding = [r for r in records if "vector" not in r or r["vector"] is None]
        if needs_embedding:
            texts = [f"{r.get('name', '')}\n{r.get('description', '')}".strip() or "empty" for r in needs_embedding]
            computed_vectors = self.embed_batch(texts)
            for r, vec in zip(needs_embedding, computed_vectors):
                r["vector"] = vec

        data_rows = []
        for r in records:
            row = {
                "vector": r["vector"],
                "id": str(r["id"]),
                "name": str(r.get("name", "")),
                "description": str(r.get("description", "")),
                "hash": str(r.get("hash", "")),
                "last_updated": datetime.now().isoformat()
            }
            data_rows.append(row)
            self._in_memory_vectors[row["id"]] = row

        # Write to LanceDB
        try:
            tbl = self._get_table()
            if tbl is None:
                self._table = self.db.create_table(self.table_name, data=data_rows, mode="overwrite")
            else:
                # LanceDB upsert: delete existing IDs then add
                ids_to_del = [r["id"] for r in data_rows]
                # Chunk deletions if many
                for chunk in [ids_to_del[i:i + 200] for i in range(0, len(ids_to_del), 200)]:
                    id_filter = " OR ".join(f"id = '{nid}'" for nid in chunk)
                    try:
                        self._table.delete(id_filter)
                    except Exception:
                        pass
                self._table.add(data_rows)
        except Exception:
            pass

    def search(self, query_text_or_vector, limit: int = 25) -> List[Dict[str, Any]]:
        """
        Search for nearest nodes. Accepts query string or embedding vector.
        """
        if isinstance(query_text_or_vector, str):
            query_vector = self.embed_text(query_text_or_vector)
        else:
            query_vector = query_text_or_vector

        # Try LanceDB search first
        try:
            tbl = self._get_table()
            if tbl is not None:
                results_df = tbl.search(query_vector).limit(limit).to_pandas()
                results = []
                for _, row in results_df.iterrows():
                    # LanceDB returns _distance (L2 distance or cosine distance). Convert to similarity score in [0, 1]
                    dist = float(row.get("_distance", 1.0))
                    # Cosine similarity roughly 1 - (dist^2)/2 or 1 - dist
                    sim = max(0.0, min(1.0, 1.0 - (dist / 2.0)))
                    results.append({
                        "id": row["id"],
                        "name": row["name"],
                        "description": row["description"],
                        "hash": row.get("hash", ""),
                        "score": sim,
                        "distance": dist
                    })
                return results
        except Exception:
            pass

        # Fallback to in-memory cosine similarity
        return self._in_memory_search(query_vector, limit)

    def _in_memory_search(self, query_vector: List[float], limit: int = 25) -> List[Dict[str, Any]]:
        if not self._in_memory_vectors:
            return []
        
        q_vec = np.array(query_vector, dtype=np.float32)
        q_norm = np.linalg.norm(q_vec)
        if q_norm == 0:
            return []
        
        scored = []
        for nid, item in self._in_memory_vectors.items():
            vec = np.array(item["vector"], dtype=np.float32)
            v_norm = np.linalg.norm(vec)
            if v_norm == 0:
                continue
            cos_sim = float(np.dot(q_vec, vec) / (q_norm * v_norm))
            scored.append({
                "id": item["id"],
                "name": item["name"],
                "description": item["description"],
                "hash": item.get("hash", ""),
                "score": max(0.0, min(1.0, (cos_sim + 1.0) / 2.0)), # normalise to [0, 1]
                "distance": 1.0 - cos_sim
            })
        
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:limit]

    def count_vectors(self) -> int:
        try:
            tbl = self._get_table()
            if tbl is not None:
                return tbl.count_rows()
        except Exception:
            pass
        return len(self._in_memory_vectors)
