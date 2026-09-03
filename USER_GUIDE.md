# Getting Started: The Complete Beginner's Guide

This guide walks you through connecting your Tana workspace to ChatGPT, Claude, or any AI assistant. 

Once deployed, the system runs 24/7 in Cloudflare's serverless edge cloud. You do not need to keep your computer running.

---

## What You Need Before You Start

1. **Tana Account (Core, Plus, or Pro) with API Access:**
   - **Tana Plus or Pro** (or early Core with API access enabled) provides access to Tana's remote API.
   - **Personal Access Token (PAT):** In Tana, go to **Settings → API Tokens** and generate a token.
   - *Note on the Outliner MCP:* The Cloudflare Worker connects directly to Tana's cloud endpoint (`https://app.tana.inc/mcp`) using your token. You do **not** need to install or run the Tana Outliner desktop MCP locally for the Cloudflare sync to work—the cloud worker talks directly to Tana's hosted MCP.
2. **Free Cloudflare Account:**
   - Sign up at [cloudflare.com](https://cloudflare.com).
   - Workers, Vectorize, and D1 all run within the free tier for typical personal knowledge graphs.
3. **AI Client Account:**
   - **ChatGPT Plus / Team / Enterprise:** For Custom GPT Actions on mobile and web.
   - **Claude Desktop / Cursor / Cline:** For Model Context Protocol (MCP) tool calling.

---

## Step 1: Deploy Your Cloud Backend (5 Minutes)

You need `git` and `node` (version 18+) installed on your machine for the initial setup.

1. **Clone the repository:**
   ```bash
   git clone https://github.com/krshirkoohi/tana-context-mask.git
   cd tana-context-mask
   ```

2. **Run the automated setup script:**
   ```bash
   ./deploy.sh
   ```

3. **What happens during setup:**
   - The script opens a browser window for you to log in to Cloudflare.
   - It automatically provisions a serverless SQLite database (`tana-db`) on Cloudflare D1 and applies the required graph schema.
   - It creates a vector search index (`tana-nodes-index`) configured for dense semantic embeddings.
   - It prompts you for your Tana API Token and securely saves it in Cloudflare's encrypted secrets manager.
   - It deploys your edge worker and prints your live URL:
     ```text
     https://tana-context-mask.<your-subdomain>.workers.dev
     ```

*Keep this URL handy. You will use it to connect your AI assistants.*

---

## Step 2: Ingest Your Workspace Data

Once deployed, the Cloudflare Worker synchronizes your Tana graph automatically:

1. **Automated Continuous Sync:**
   - Cloudflare runs a background cron trigger every 15 minutes (`*/15 * * * *`).
   - It connects directly to Tana Cloud over HTTPS, pulls modified nodes, generates embeddings at the edge, and updates your vector database.

2. **Check Your Sync Status:**
   - Open your browser and visit:
     ```text
     https://<your-worker-subdomain>.workers.dev/api/v1/sync/status
     ```
   - You will see the live node count, last sync timestamp, and ingestion health.

*Your local machine is no longer needed. You can close your terminal and turn off your computer.*

---

## Step 3: Connect Your AI Assistant

### Option A: Connect to ChatGPT (Custom GPT)

This lets you query your Tana graph from ChatGPT on your phone or browser.

1. Open [chatgpt.com/gpts/editor](https://chatgpt.com/gpts/editor) to create or edit a Custom GPT.
2. In the **Configure** tab:
   - **Name:** Tana Knowledge Assistant
   - **Instructions:** Paste the prompt below:
     ```text
     You are a personal knowledge assistant connected to the user's private Tana workspace.
     
     Operating Rules:
     1. Always call the acquireContext action before answering questions about the user's notes, projects, meetings, tasks, or ideas.
     2. When referencing retrieved information, cite the exact source note using its deep link: [Node Name](url).
     3. Summarise information concisely and clearly distinguish between retrieved facts and your own synthesis.
     ```
3. Scroll down to **Actions** → click **Create new action**.
4. Click **Import from URL** and enter:
   ```text
   https://<your-worker-subdomain>.workers.dev/openapi.json
   ```
5. Set **Authentication** to **None** (or API Key if you configured one).
6. Click **Save** in the top right.

### Option B: Connect to Claude Desktop or Cursor (MCP)

If you use Claude Desktop, Cursor, or an MCP-compatible IDE:

1. Open your Claude Desktop configuration file (`claude_desktop_config.json`):
   - **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
   - **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`
2. Add the remote server:
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
3. Restart Claude Desktop. The hammer icon will show your new Tana search and context tools.

---

## Frequently Asked Questions

### Does this work with Tana Plus?
Yes. Any Tana tier (Core, Plus, or Pro) that allows you to generate a Personal Access Token in **Settings → API Tokens** works with this engine.

### Do I need to keep my computer turned on?
No. The backend worker, vector database, and periodic synchronization all run on Cloudflare's serverless infrastructure.

### How much does this cost?
Nothing for personal use. Cloudflare's free tier provides 100,000 Worker requests per day, 50,000 Vectorize vector queries per month, and free SQLite storage on D1, which easily covers typical personal knowledge graphs.

### Do I need to run the Tana Outliner desktop MCP server?
No. The Cloudflare Worker connects directly to Tana's cloud-hosted MCP (`https://app.tana.inc/mcp`) using your API token. You do not need to run any local desktop bridge or Python process.

### What is the difference between the Tana Outliner MCP and this Semantic Embedding MCP?
- **Tana Outliner MCP:** Tana's native interface for reading and writing raw nodes by ID or exact keyword match.
- **Tana Context Mask MCP:** This semantic layer. It indexes your notes into dense vector embeddings, performs conceptual search (finding notes even when keywords don't match), expands the graph around candidate seeds, and packages a compact, cited context packet for your AI assistant.

### Is my data private?
Yes. Your data stays between your Tana account and your own Cloudflare account. It never touches any third-party intermediate servers.
