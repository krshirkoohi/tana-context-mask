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

## 📋 Requirements & Prerequisites

To deploy and use `tana-context-mask`, you need the following accounts and access:

### 1. Account Requirements
* **Tana Pro Account:** 
  * A paid **Tana Pro** account is required to use Tana's hosted cloud MCP endpoint (`https://app.tana.inc/mcp`).
  * In Tana, go to **Settings → API Tokens** and generate a **Personal Access Token (PAT)**.
* **Cloudflare Account (Free or Paid):**
  * Required to host the serverless edge worker, **Cloudflare D1 (SQLite)**, **Vectorize**, and **Workers AI (BGE embeddings)**.
* **ChatGPT Plus / Team / Enterprise (Optional for Custom GPTs):**
  * Required if you wish to attach the OpenAPI action to a Custom GPT in ChatGPT (iOS or Web).
* **Python Runtime (For CLI / Local Package):**
  * Python `3.10` or higher.

---

## 📦 Installation & Setup Guide

### Option 1: Install as a Python Package / CLI

```bash
# Install directly from GitHub
pip install git+https://github.com/krshirkoohi/tana-context-mask.git

# Or install editable from a cloned repo
git clone https://github.com/krshirkoohi/tana-context-mask.git
cd tana-context-mask
pip install -e .
```

#### Configure Environment:
Create a `.env` file in your working directory:
```env
TANA_TOKEN=eyJ0eXAiOiJKV1QiLC...    # Your Tana Pro Personal Access Token
TANA_URL=https://app.tana.inc/mcp     # Tana Hosted Remote MCP Endpoint
```

#### Querying from the CLI:
```bash
# Test semantic context retrieval
tana-context-mask context "What are my active goals and projects?"
```

---

### Option 2: Deploy Your Own 24/7 Cloudflare Worker

```bash
cd worker
npm install

# 1. Create your remote Cloudflare D1 Database and Vectorize Index
npx wrangler d1 create tana-db
npx wrangler vectorize create tana-nodes-index --dimensions=384 --metric=cosine

# 2. Update wrangler.jsonc with your database_id and index_name

# 3. Store your encrypted Tana Pro PAT secret in Cloudflare
npx wrangler secret put TANA_API_TOKEN

# 4. Deploy to Cloudflare Serverless Edge
npx wrangler deploy
```

---

### Option 3: Connect to ChatGPT (Custom GPT Action)

1. Open ChatGPT → **Explore GPTs** → **Create a GPT** → **Configure**.
2. Scroll to **Actions** → Click **Create new action**.
3. Click **Import from URL** and paste your worker's OpenAPI schema:
   ```
   https://<your-worker-subdomain>.workers.dev/openapi.json
   ```
4. Set **Authentication** to `None` (or `API Key` if `API_KEY` environment secret is set).
5. In your GPT Instructions, add:
   ```
   Always call the acquireContext tool before answering questions about the user's projects, notes, meetings, or life status to ground answers in verified Tana notes with deep links.
   ```

---

## 🏛️ Future Architecture: Source-Agnostic Property-Weighted Retrieval

The retrieval engine (dense/sparse hybrid search + multi-hop ancestry + schema density + key-value property weighting) is fundamentally **source-agnostic**:

* **Universal Entity & Property Schema:** Any structured second brain (Notion databases, filesystem Markdown frontmatter, GitHub DAG issues, Obsidian vaults) maps directly into our graph schema `(id, name, parent_id, fields, edges)`.
* **Configurable Property Weights:** JSON key-value properties can be weighted dynamically so high-signal attributes (`Status`, `Tags`, `Priority`, `Assignees`, `Dates`) automatically drive reranking priority.
* **MCP Portability:** By attaching standard Model Context Protocol (MCP) ingestion adapters, the engine can index and retrieve across any MCP-compliant tool without altering the core Cloudflare Edge Graph-RAG pipeline.


