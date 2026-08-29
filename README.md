# Tana Context Mask (Tana Semantic Engine)

> An embedding-backed, graph-aware context acquisition engine for Tana Outliner and ChatGPT.

`tana-context-mask` acts as a private retrieval service that sits between Tana Outliner and AI clients (ChatGPT Actions on iOS, custom MCP apps on ChatGPT web, Claude, Cursor, and Google Antigravity). Rather than forcing users or AI agents to guess exact search keywords or navigate rigid folders, it performs **whole-workspace semantic discovery**, **graph expansion** around candidate seeds, **multi-factor reranking**, and formats a compact, sourced **context packet** for LLMs.

---

## 🚀 Core Architecture

```
User Task / Prompt
        │
        ▼
[ 1. Whole-Workspace Semantic Discovery ]
  • Local BGE Embeddings (BAAI/bge-small-en-v1.5) via LanceDB
  • Full-Text Search (BM25) via SQLite FTS5
        │
        ▼
[ 2. Knowledge Graph Expansion ]
  • Ancestry breadcrumb resolution
  • Sub-item / child task traversal
  • Outgoing references (`tana:<id>`) & incoming backlinks
        │
        ▼
[ 3. Multi-Factor Task Reranking ]
  • Hybrid similarity + Graph clustering bonus + Temporal recency decay
  • Subtree deduplication & noise pruning
        │
        ▼
[ 4. Compact, Sourced Context Packet ]
  • Markdown & JSON payload with deep links (`https://app.tana.inc/?nodeid=<id>`)
  • Trace ID for full retrieval inspectability
        │
        ▼
AI Assistant (ChatGPT / Claude / Antigravity / AGY) executes real task
```

---

## 🛠️ Key Capabilities

- **Dual-Interface Layer**:
  - **OpenAPI 3.1 REST API**: Tailored for ChatGPT Custom GPT Actions (native mobile/iOS compatible) and custom HTTP clients.
  - **Model Context Protocol (MCP) Server**: Exposes stdio/SSE tools for ChatGPT web MCP apps, Claude Desktop, Cursor, and Antigravity.
- **Separate Invalidation Hashing**: Content hashes and structural/hierarchy hashes are tracked separately. Moving a node or renaming a parent container never triggers a cascading re-embedding of entire subtrees.
- **Hybrid Search**: Combines dense vector cosine similarity with sparse BM25 keyword matching via customizable alpha weighting.
- **Inspectable Traceability**: Every context acquisition run generates a persistent `trace_id` recording query inference, seed hits, followed graph edges, excluded nodes, and scoring breakdowns.

---

## 📦 Quick Start

### 1. Installation & Environment

```bash
cd /Users/krshirkoohi/github/lab/tana-context-mask
pip install -e .
```

Ensure your `.env` contains your Tana token:
```env
TANA_TOKEN=your_tana_token
TANA_URL=https://app.tana.inc/mcp
WORKSPACE_ID=--D3QJHnLgSk
```

### 2. Mirror Synchronization

**Incremental Sync (via Tana Remote MCP):**
```bash
python3 -m tana_context_mask.cli sync --lookback 1
```

**Fast Bootstrap (from Tana JSON Export):**
```bash
python3 -m tana_context_mask.cli sync --mode bootstrap --file "/path/to/export.json"
```

### 3. Context Acquisition CLI

```bash
# Acquire background context for an AI task
python3 -m tana_context_mask.cli context "What are my current active projects and upcoming milestones?"

# Perform hybrid search
python3 -m tana_context_mask.cli search "context engine" --limit 5

# View system statistics
python3 -m tana_context_mask.cli stats
```

### 4. Running the REST API Server

```bash
python3 -m tana_context_mask.cli serve --port 8000
```
- Interactive Swagger UI: `http://localhost:8000/docs`
- OpenAPI 3.1 JSON: `http://localhost:8000/openapi.json`

### 5. Running as an MCP Server

Add to your MCP client configuration (`claude_desktop_config.json` or Antigravity config):
```json
{
  "mcpServers": {
    "tana-context-mask": {
      "command": "python3",
      "args": ["-m", "tana_context_mask.cli", "mcp"],
      "cwd": "/Users/krshirkoohi/github/lab/tana-context-mask"
    }
  }
}
```

---

## 🧪 Testing

Run the automated test suite:
```bash
pytest
```
18 unit and integration tests covering database operations, FTS5 queries, graph expansion, hybrid search, context packet assembly, REST endpoints, and MCP tools.
