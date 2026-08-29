# 🤖 Running Tana Context Mask on ChatGPT (Complete Setup Guide)

This guide walks you through connecting **Tana Context Mask** to **ChatGPT** via **Custom GPT Actions** (works on iOS, iPadOS, macOS, Android, and ChatGPT Web) as specified in the PRD.

---

## 🏗️ Architecture Overview

```
ChatGPT (iOS / Web / Mobile)
        │
        ▼ (HTTPS / Action Request)
Secure Public Tunnel (Pinggy / Cloudflare / Ngrok / VPS)
        │
        ▼ (Port 8000)
Tana Context Mask FastAPI Server (Local Mac)
        │
        ├── LanceDB + FastEmbed (BAAI/bge-small-en-v1.5 Dense Embeddings)
        ├── SQLite FTS5 (BM25 Lexical Search & Graph Store)
        └── Tana Remote MCP / Local Workspace Mirror
```

---

## ⚡ Step 1: Start the Local API Server

In your terminal:
```bash
cd /Users/krshirkoohi/github/lab/tana-context-mask
python3 -m tana_context_mask.cli serve --port 8000
```

Verify it is running locally:
```bash
curl http://localhost:8000/api/v1/health
# {"status":"healthy","sqlite_nodes":502,"lancedb_vectors":467}
```

---

## 🌐 Step 2: Expose via Secure HTTPS Tunnel

ChatGPT's servers require an internet-accessible HTTPS URL to call Actions. You can create a free HTTPS tunnel in seconds using any of these methods:

### Option A: Zero-Install Instant Tunnel (Pinggy / SSH)
Run in a separate terminal:
```bash
ssh -p 443 -R0:localhost:8000 a.pinggy.io
```
*Pinggy will give you an HTTPS URL like `https://xxxx.a.pinggy.link`.*

### Option B: Cloudflare Tunnel (Recommended for permanent setups)
```bash
brew install cloudflared
cloudflared tunnel --url http://localhost:8000
```
*Cloudflare will give you an HTTPS URL like `https://xxxx.trycloudflare.com`.*

---

## 🛠️ Step 3: Create the Custom GPT in ChatGPT

1. Open [chatgpt.com/gpts/editor](https://chatgpt.com/gpts/editor) (or click **Explore GPTs** -> **Create**).
2. Go to the **Configure** tab.

### 📝 Profile Configuration
- **Name**: `Tana Knowledge Assistant`
- **Description**: `Semantic and graph-aware companion connected directly to your Tana Outliner workspace.`

### 🧠 Instructions (System Prompt)
Paste the following into the **Instructions** box:

```markdown
You are an intelligent knowledge assistant connected to the user's private Tana Outliner workspace via the Tana Context Mask API.

### Core Operating Rules:
1. PROACTIVE CONTEXT ACQUISITION:
   - Whenever the user asks a question about their projects, active tasks, meetings, people, notes, reading list, personal ideas, or schedule, you MUST ALWAYS call the `acquireContext` action first before generating your final answer.
   - The user should not have to manually formulate search keywords or remind you to look at their notes.

2. SEARCH FORMULATION:
   - In `task`, pass the user's full prompt or question.
   - In `query`, pass refined search keywords if appropriate.
   - If the user explicitly mentions a tag/domain (e.g., 'Project', 'Recipe', 'Fitness'), pass it in `scope`.

3. CITATIONS & DEEP LINKS:
   - When citing notes, tasks, or information retrieved from Tana, format them with their deep links: `[Node Name](https://app.tana.inc/?nodeid=<nodeId>)`.
   - Mention the path / breadcrumbs if it clarifies context (e.g. `Area › Project › Task`).

4. TRANSPARENT BEHAVIOUR:
   - Synthesise the retrieved graph nodes concisely into your answer.
   - If no relevant notes exist in Tana, state that no existing workspace notes were found and answer using general reasoning.
```

---

## 🔌 Step 4: Add Action & Paste OpenAPI Schema

1. At the bottom of the Configure tab, click **Create new action**.
2. Set **Authentication** to `None` (or `API Key` / Custom Header `X-API-Key` if configured in `.env`).
3. In the **Schema** box, paste the JSON schema from [`chatgpt_openapi.json`](file:///Users/krshirkoohi/github/lab/tana-context-mask/chatgpt_openapi.json), replacing `YOUR_HTTPS_TUNNEL_URL` in the `servers` block with your tunnel URL (e.g. `https://xxxx.trycloudflare.com` or `https://xxxx.a.pinggy.link`).

---

## 🧪 Step 5: Test on Web & iOS

1. In the ChatGPT Preview pane (or on the ChatGPT iOS app), send a test prompt:
   > *"What are my active projects and notes on enterprise AI pricing?"*
2. ChatGPT will call `acquireContext`, fetch the contextual subgraphs and breadcrumbs from your local Tana database, and deliver a grounded, deep-linked response.
