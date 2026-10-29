import os
import json
import subprocess
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional
from dotenv import load_dotenv

# Base project directory
BASE_DIR = Path(__file__).resolve().parent.parent

# Load .env file from project root or current working dir
load_dotenv(BASE_DIR / ".env")
load_dotenv()


def resolve_secret_from_bitwarden(
    secret_name: str = "TANA_TOKEN",
    env_Fallback_file: Optional[Path] = None,
    timeout: int = 5,
) -> str:
    """
    Zero-trust credential resolver for Tana API tokens (#9):
    1. Checks ~/.gemini/secrets.env (synced by bitwarden-sync) if present.
    2. Queries Bitwarden Secrets Manager CLI (`bws`) when BWS_ACCESS_TOKEN is present.
    3. Queries unlocked Bitwarden Vault CLI (`bw`) when BW_SESSION is present.
    Never writes plaintext credentials back to the repository.
    """
    # 1. Check ~/.gemini/secrets.env if available
    secrets_env = env_Fallback_file if env_Fallback_file is not None else (Path.home() / ".gemini" / "secrets.env")
    if secrets_env.exists():
        try:
            for line in secrets_env.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                if stripped.startswith("export "):
                    stripped = stripped[len("export "):].strip()
                if "=" in stripped:
                    k, v = stripped.split("=", 1)
                    if k.strip() == secret_name:
                        val = v.strip().strip("'").strip('"')
                        if val:
                            return val
        except Exception:
            pass

    # 2. Check Bitwarden Secrets Manager CLI (`bws`)
    bws_token = os.getenv("BWS_ACCESS_TOKEN", "").strip()
    if bws_token:
        try:
            res = subprocess.run(
                ["bws", "secret", "list", "--access-token", bws_token, "--output", "json"],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            if res.returncode == 0 and res.stdout.strip():
                items = json.loads(res.stdout)
                if isinstance(items, list):
                    for item in items:
                        if item.get("key") == secret_name and item.get("value"):
                            return str(item["value"]).strip()
        except Exception:
            pass

    # 3. Check unlocked Bitwarden Vault CLI (`bw`) when BW_SESSION is active
    bw_session = os.getenv("BW_SESSION", "").strip()
    if bw_session:
        try:
            res = subprocess.run(
                ["bw", "get", "password", secret_name, "--session", bw_session],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout.strip()

            res_items = subprocess.run(
                ["bw", "list", "items", "--search", secret_name, "--session", bw_session],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            if res_items.returncode == 0 and res_items.stdout.strip():
                items = json.loads(res_items.stdout)
                if isinstance(items, list):
                    for item in items:
                        if item.get("name") == secret_name:
                            login = item.get("login") or {}
                            if login.get("password"):
                                return str(login["password"]).strip()
                            if item.get("notes"):
                                return str(item["notes"]).strip()
        except Exception:
            pass

    return ""


@dataclass
class Settings:
    tana_token: str = field(default_factory=lambda: os.getenv("TANA_TOKEN", ""))
    tana_url: str = field(default_factory=lambda: os.getenv("TANA_URL", "https://app.tana.inc/mcp"))
    workspace_id: str = field(default_factory=lambda: os.getenv("WORKSPACE_ID", "--D3QJHnLgSk"))
    
    # Storage Paths
    data_dir: Path = BASE_DIR / "data"
    sqlite_db_path: str = field(default_factory=lambda: os.getenv("SQLITE_DB_PATH", str(BASE_DIR / "data" / "tana_mask.db")))
    lance_db_path: str = field(default_factory=lambda: os.getenv("LANCE_DB_PATH", str(BASE_DIR / "data" / "vector_store")))
    model_cache_path: str = field(default_factory=lambda: os.getenv("MODEL_CACHE_PATH", str(BASE_DIR / "data" / "model_cache")))
    
    # Server & API
    host: str = field(default_factory=lambda: os.getenv("HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: int(os.getenv("PORT", "8000")))
    api_key: str = field(default_factory=lambda: os.getenv("API_KEY") or os.getenv("CONTEXT_MASK_API_KEY", ""))
    embedding_model: str = field(default_factory=lambda: os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5"))
    
    # Search & Retrieval Defaults
    default_top_k: int = field(default_factory=lambda: int(os.getenv("DEFAULT_TOP_K", "25")))
    default_max_context_nodes: int = field(default_factory=lambda: int(os.getenv("DEFAULT_MAX_CONTEXT_NODES", "8")))
    max_expansion_hops: int = field(default_factory=lambda: int(os.getenv("MAX_EXPANSION_HOPS", "1")))
    hybrid_alpha: float = field(default_factory=lambda: float(os.getenv("HYBRID_ALPHA", "0.7")))  # 0.7 semantic, 0.3 lexical

    def __post_init__(self):
        if not self.tana_token:
            self.tana_token = resolve_secret_from_bitwarden("TANA_TOKEN")

    def ensure_directories(self):
        """Ensure all required data directories exist."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        Path(self.sqlite_db_path).parent.mkdir(parents=True, exist_ok=True)
        Path(self.lance_db_path).mkdir(parents=True, exist_ok=True)
        Path(self.model_cache_path).mkdir(parents=True, exist_ok=True)

settings = Settings()
settings.ensure_directories()
