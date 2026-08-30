# Tana Context Mask (Tana Semantic Engine)

> An embedding-backed, graph-aware context acquisition engine for Tana Outliner and ChatGPT.

`tana-context-mask` acts as a private retrieval service that sits between Tana Outliner and AI clients (ChatGPT Actions on iOS, custom MCP apps on ChatGPT web, Claude, Cursor, and Google Antigravity). Rather than forcing users or AI agents to guess exact search keywords or navigate rigid folders, it performs **whole-workspace semantic discovery**, **graph expansion** around candidate seeds, **multi-factor reranking**, and formats a compact, sourced **context packet** for LLMs.

---

## 🌐 Live Cloud Infrastructure

The service is fully deployed and active on **Cloudflare Serverless Edge**:

* **Production Base URL:** `https://tana-context-mask.krshirkoohi.workers.dev`
* **Health Check:** [`/api/v1/health`](https://tana-context-mask.krshirkoohi.workers.dev/api/v1/health) — Live D1 node count & system status
* **Sync & Mirror Status:** [`/api/v1/sync/status`](https://tana-context-mask.krshirkoohi.workers.dev/api/v1/sync/status) — Last sync timestamp, cursor & health
* **OpenAPI 3.1 Spec:** [`/openapi.json`](https://tana-context-mask.krshirkoohi.workers.dev/openapi.json) — Custom GPT Action schema
* **MCP SSE Endpoint:** [`/sse`](https://tana-context-mask.krshirkoohi.workers.dev/sse) — Model Context Protocol stream
* **AI Plugin Manifest:** [`/.well-known/ai-plugin.json`](https://tana-context-mask.krshirkoohi.workers.dev/.well-known/ai-plugin.json)
* **Cloudflare Dashboard:** [Cloudflare Dash](https://dash.cloudflare.com/) — Workers logs, D1 `tana-db`, Vectorize `tana-nodes-index`

### Cloud Architecture & Cadence
* **Cloud Storage:** **97,254 nodes** mirrored in **Cloudflare D1 (SQLite)** & **Cloudflare Vectorize** (dense BGE embeddings).
* **Automated Sync Cadence:** Runs every **15 minutes** (`*/15 * * * *`) via Cloudflare Cron Triggers.
* **Delta Sync Cursor:** Leverages millisecond timestamp queries (`edited.since`) with a 5-minute safety overlap window directly against Tana Remote MCP (`https://app.tana.inc/mcp`).
* **Zero Local Dependencies:** Operates 24/7 in the cloud without requiring a desktop app or running Mac.

---

## 🚀 Core Pipeline

```
User Task / Prompt in ChatGPT (iOS / Web)
        │
        ▼
[ 1. Semantic Discovery on Edge GPUs ]
  • Cloudflare Workers AI (@cf/baai/bge-small-en-v1.5)
  • Cosine Similarity via Cloudflare Vectorize + SQLite FTS5 (BM25)
        │
        ▼
[ 2. Knowledge Graph Expansion ]
  • 1-hop ancestry breadcrumbs, sub-items & backlinks in D1
        │
        ▼
[ 3. Multi-Factor Task Reranking ]
  • Hybrid similarity + Graph clustering bonus + Temporal recency
        │
        ▼
[ 4. Compact Context Packet (~800ms) ]
  • Markdown payload with deep links (https://app.tana.inc/?nodeid=<id>)
        │
        ▼
AI Assistant answers with exact, grounded personal context
```

---

## 📖 Usage Guide for ChatGPT Custom GPT

### Setting up Custom GPT Action:
1. In ChatGPT, open your Custom GPT editor → **Configure** → **Actions** → **Create new action**.
2. Click **Import from URL** and enter:
   ```
   https://tana-context-mask.krshirkoohi.workers.dev/openapi.json
   ```
3. Set **Authentication** to `None` (or API Key if enabled).

### Daily Usage:
* Simply ask your Custom GPT natural questions:
  * *"What are my active goals and projects right now?"*
  * *"Summarize my notes from yesterday's meeting about [Topic]."*
  * *"Find all references and context related to [Concept]."*
* Your GPT automatically calls `acquireContext`, inspects the graph, and responds with exact source node links.

---

## 🧪 Daily Test Cases (Trial Protocol)

Use these 3 test cases over the next few days to verify real-world behavior:

### Test Case 1: Fresh Note Sync (Ingestion & Retrieval)
1. In Tana, create a new note under Today's page (e.g. `Project Horizon: Target launch date is October 15 with 3 milestones`).
2. Wait 15 minutes (or trigger instant sync via `/api/v1/sync`).
3. In ChatGPT on iOS/Web, ask: *"What is the launch date and milestone count for Project Horizon?"*
4. **Expected:** ChatGPT cites the exact node with a clickable Tana deep link.

### Test Case 2: Semantic Concept Search (No Exact Keywords)
1. In ChatGPT, ask a conceptual query that does not share verbatim keywords with your notes (e.g., *"What were my thoughts on coping with loud noise or sensory overload?"*).
2. **Expected:** Semantic vector search surfaces the relevant historical notes via BGE cosine similarity.

### Test Case 3: Deletion & Stale Cache Check
1. Trash or delete a test note in Tana.
2. After the next sync cycle, ask ChatGPT about that deleted note.
3. **Expected:** The stale node is removed from the active context packet and not hallucinated.

---

## 📦 Local CLI / Dev Setup

```bash
pip install -e .
```

Ensure `.env` contains your Pro Personal Access Token:
```env
TANA_TOKEN=eyJ0eXAiOiJKV1QiLC...
TANA_URL=https://app.tana.inc/mcp
```

### Local Testing:
```bash
# Run test suite
pytest

# Context acquisition CLI
python3 -m tana_context_mask.cli context "What are my current active projects?"
```
