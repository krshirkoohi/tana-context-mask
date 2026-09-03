# Tana Context Mask (Universal Semantic Engine)

> A provider-agnostic, graph-aware semantic context acquisition engine connecting [Tana Outliner](https://tana.inc) to modern AI assistants, agents, and IDEs.

`tana-context-mask` acts as a unified retrieval gateway between your Tana knowledge graph and modern AI platforms. Instead of rigid keyword queries or manual copy-pasting, it performs dense vector discovery, subgraph expansion around candidate nodes, multi-factor reranking, and serves a structured, sourced context packet to any LLM.

---

## Supported AI Interfaces

Built to interface cleanly with major AI ecosystems:

| Standard | Compatible Clients | Integration Method |
| :--- | :--- | :--- |
| **OpenAPI 3.1 Actions** | ChatGPT (Web & Mobile), LibreChat, OpenWebUI | Import `/openapi.json` |
| **Model Context Protocol (MCP)** | Claude Desktop, Cursor, Windsurf, Cline, Antigravity | Connect to `/sse` |
| **REST API** | LangChain, LlamaIndex, AutoGen, Custom Agents | Standard `POST /api/v1/context/acquire` |

---

## Architecture: Edge Cloud vs Self-Hosted

Choose the deployment model that best matches your workflow:

```
                      ┌──────────────────────────────────────────────┐
                      │    AI Clients (ChatGPT / Claude / Agents)    │
                      └──────────────────────┬───────────────────────┘
                                             │ HTTPS / OpenAPI / MCP SSE
                                             ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                        Tana Context Mask Gateway                                       │
│                                                                                        │
│   Option A: 100% Serverless Edge (Cloudflare)   Option B: Universal Container (Docker) │
│   ───────────────────────────────────────────   ────────────────────────────────────── │
│   • Edge GPUs: Workers AI (BGE-Small 384-dim)   • Local/VPS GPU/CPU: FastEmbed         │
│   • Vector DB: Cloudflare Vectorize             • Vector DB: LanceDB                   │
│   • Metadata: Cloudflare D1 (SQLite FTS5)       • Metadata: SQLite FTS5                │
│   • Continuous Cloud Cron Sync (Every 15 min)   • Background Sync Daemon               │
│   • Zero local computer dependencies (24/7)     • Self-hosted on AWS, GCP, Fly.io, etc.│
└────────────────────────────────────┬───────────────────────────────────────────────────┘
                                     │ Remote MCP Sync
                                     ▼
                     ┌───────────────────────────────┐
                     │     Tana Knowledge Graph      │
                     └───────────────────────────────┘
```

---

## Quickstart

### Option 1: Automated Serverless Edge (Recommended)

Deploy to Cloudflare's global edge network in under 5 minutes. Operates 24/7 online with zero local machine compute required:

```bash
git clone https://github.com/krshirkoohi/tana-context-mask.git
cd tana-context-mask

# Run automated deployment
./deploy.sh
```

**What `./deploy.sh` provisions:**
1. Authenticates your Cloudflare CLI (`wrangler`).
2. Creates your serverless Cloudflare D1 SQLite Database (`tana-db`) and applies `schema.sql`.
3. Provisions your Vectorize Index (`tana-nodes-index`) with 384 dimensions matching `@cf/baai/bge-small-en-v1.5`.
4. Securely stores your encrypted `TANA_API_TOKEN` secret.
5. Deploys the worker to Cloudflare's edge network and outputs your live production URL:
   `https://tana-context-mask.<your-subdomain>.workers.dev`

---

### Option 2: Self-Hosted Container (Docker / Cloud Run / AWS)

For self-hosting on standard container infrastructure:

```bash
# Install as a local Python package
pip install -e .

# Run the API server
python3 -m tana_context_mask.cli serve --port 8000
```

Deployable to Google Cloud Run, AWS ECS, Fly.io, or standard Kubernetes clusters.

---

## Connecting to AI Clients

### 1. ChatGPT (Custom GPT Action)
1. Open ChatGPT → **Explore GPTs** → **Create a GPT** → **Configure**.
2. Under **Actions**, click **Create new action**.
3. Click **Import from URL** and enter:
   ```text
   https://<your-worker-subdomain>.workers.dev/openapi.json
   ```
4. Set **Authentication** to `None` (or `API Key` / Bearer token if configured).
5. In **Instructions**, add:
   ```text
   Always invoke the acquireContext action before answering questions regarding projects, notes, meetings, or background context to ground answers with direct Tana links.
   ```

### 2. Claude Desktop / Cursor / Cline (Remote MCP)
Add the server endpoint to your MCP configuration:
```json
{
  "mcpServers": {
    "tana-context": {
      "command": "npx",
      "args": [
        "-y",
        "mcp-remote",
        "https://<your-worker-subdomain>.workers.dev/sse"
      ]
    }
  }
}
```

---

## Prerequisites
- **Tana Pro Account:** Required for Tana's hosted cloud MCP endpoint (`https://app.tana.inc/mcp`). Generate a Personal Access Token in **Settings → API Tokens**.
- **Hosting Account:** A free Cloudflare account (for serverless edge) or any Docker-compatible hosting environment.

---

## Extensibility: Source-Agnostic Property-Weighted Retrieval

The retrieval engine (dense/sparse hybrid search + multi-hop ancestry + schema density + key-value property weighting) is fundamentally source-agnostic:

- **Universal Entity & Property Schema:** Any structured second brain (Notion databases, filesystem Markdown frontmatter, GitHub DAG issues, Obsidian vaults) maps directly into our graph schema `(id, name, parent_id, fields, edges)`.
- **Configurable Property Weights:** JSON key-value properties can be weighted dynamically so high-signal attributes (`Status`, `Tags`, `Priority`, `Assignees`, `Dates`) automatically drive reranking priority.
- **MCP Portability:** By attaching standard Model Context Protocol (MCP) ingestion adapters, the engine can index and retrieve across any MCP-compliant tool without altering the core Cloudflare Edge Graph-RAG pipeline.


