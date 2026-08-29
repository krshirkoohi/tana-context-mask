import os
from pathlib import Path
from dataclasses import dataclass
from dotenv import load_dotenv

# Base project directory
BASE_DIR = Path(__file__).resolve().parent.parent

# Load .env file from project root or current working dir
load_dotenv(BASE_DIR / ".env")
load_dotenv()

@dataclass
class Settings:
    tana_token: str = os.getenv("TANA_TOKEN", "")
    tana_url: str = os.getenv("TANA_URL", "https://app.tana.inc/mcp")
    workspace_id: str = os.getenv("WORKSPACE_ID", "--D3QJHnLgSk")
    
    # Storage Paths
    data_dir: Path = BASE_DIR / "data"
    sqlite_db_path: str = os.getenv("SQLITE_DB_PATH", str(BASE_DIR / "data" / "tana_mask.db"))
    lance_db_path: str = os.getenv("LANCE_DB_PATH", str(BASE_DIR / "data" / "vector_store"))
    model_cache_path: str = os.getenv("MODEL_CACHE_PATH", str(BASE_DIR / "data" / "model_cache"))
    
    # Server & API
    host: str = os.getenv("HOST", "0.0.0.0")
    port: int = int(os.getenv("PORT", "8000"))
    api_key: str = os.getenv("API_KEY", "")
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
    
    # Search & Retrieval Defaults
    default_top_k: int = int(os.getenv("DEFAULT_TOP_K", "25"))
    default_max_context_nodes: int = int(os.getenv("DEFAULT_MAX_CONTEXT_NODES", "8"))
    max_expansion_hops: int = int(os.getenv("MAX_EXPANSION_HOPS", "1"))
    hybrid_alpha: float = float(os.getenv("HYBRID_ALPHA", "0.7"))  # 0.7 semantic, 0.3 lexical

    def ensure_directories(self):
        """Ensure all required data directories exist."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        Path(self.sqlite_db_path).parent.mkdir(parents=True, exist_ok=True)
        Path(self.lance_db_path).mkdir(parents=True, exist_ok=True)
        Path(self.model_cache_path).mkdir(parents=True, exist_ok=True)

settings = Settings()
settings.ensure_directories()
