# Getting Started: Tana Semantic Engine & ChatGPT Guide

This guide is designed for anyone who has **Tana** and **ChatGPT** and wants to connect them intelligently—without having to run complex developer infrastructure or manage multiple plugins.

The Tana Semantic Engine acts as a layer of intelligence above your Tana workspace. Combined with Tana's native outliner graph, it creates a lightweight GraphRAG system: **semantic vector search** finds the right neighbourhood of information by meaning, while **Tana Outliner** provides structural context (hierarchy, supertags, fields, and references).

---

## 💡 The Core Problem: Why Do We Need This?

If you try to connect Tana directly to ChatGPT today, you run into an immediate technical barrier:
* **ChatGPT Custom GPTs only speak OpenAPI REST Actions.** They cannot connect directly to MCP (Model Context Protocol) endpoints.
* **Tana's hosted cloud endpoint (`https://app.tana.inc/mcp`) is pure MCP (JSON-RPC).** Pasting it directly into ChatGPT causes an error: *"Could not find a valid schema at that URL"*.

**Tana Context Mask solves this in one place.** It connects to your Tana workspace, indexes your notes into private vector embeddings at the edge, and provides a single, unified OpenAPI interface. **You only ever need to configure ONE Action in ChatGPT.**

---

## 📋 What You Need Before Starting

1. **Tana Plus Account (or Pro):**
   - **You do NOT need Tana Pro.** You only need **Tana Plus** ($14/mo) to access Tana's remote API.
   - In Tana, go to **Settings → API Tokens** and copy your **Personal Access Token (PAT)**.
2. **Free Cloudflare Account:**
   - Sign up at [cloudflare.com](https://cloudflare.com) (free, no credit card required).
   - Your edge backend runs entirely within Cloudflare's generous free tier ($0/month).
3. **ChatGPT Plus / Team / Enterprise Account:**
   - Required by OpenAI to create and use Custom GPTs with Actions on Web, macOS, iOS, and Android.

---

## ⚡ The 3-Step Painless Setup

### Step 1: Clone and Run the Setup Script (5 Minutes)

Open your terminal (on Mac, press `Cmd + Space`, type `Terminal`, and press Enter):

```bash
git clone https://github.com/krshirkoohi/tana-context-mask.git
cd tana-context-mask
./deploy.sh
```

**What the script does automatically:**
1. Opens a browser window for you to log in to Cloudflare.
2. Creates your serverless SQLite database (`tana-db`) on Cloudflare D1 with the required graph schema.
3. Creates a vector search index (`tana-nodes-index`) configured for dense embeddings (`bge-small-en-v1.5`).
4. Prompts you to paste your **Tana API Token** and stores it securely in Cloudflare's encrypted secrets vault.
5. Deploys your 24/7 serverless worker and prints your live URL:
   ```text
   https://tana-context-mask.<your-subdomain>.workers.dev
   ```

*Save this URL. You will use it in Step 3.*

---

### Step 2: Workspace Data Ingestion

Once deployed, your Cloudflare Worker synchronises your Tana notes automatically in the background:
* **Continuous Edge Sync:** A background cron trigger runs automatically every 15 minutes (`*/15 * * * *`). It pulls newly created or edited nodes from Tana, computes vector embeddings, and stores them in your database.
* **Check Sync Health:** Visit `https://<your-subdomain>.workers.dev/api/v1/sync/status` in any browser to see your live node count and indexing status.

*You can now close your terminal and shut down your computer. Your engine runs 24/7 in the cloud.*

---

### Step 3: Configure Your Custom GPT in ChatGPT (3 Minutes)

1. Open [chatgpt.com/gpts/editor](https://chatgpt.com/gpts/editor) (or click **Explore GPTs → Create**).
2. Go to the **Configure** tab:
   * **Name:** `Tana Knowledge Assistant`
   * **Description:** `Cognitive GraphRAG copilot connected to my private Tana workspace.`
   * **Instructions:** Copy and paste the comprehensive system prompt from [`SYSTEM_PROMPT.md`](SYSTEM_PROMPT.md) (replace `{{USER_NAME}}` with your name).
3. Scroll down to **Actions** and click **Create new action**.
4. Click **Import from URL** and enter:
   ```text
   https://<your-subdomain>.workers.dev/openapi.json
   ```
5. Leave **Authentication** set to **None** (your worker authenticates directly with Tana using your encrypted secret).
6. Click **Save** in the top right.

**Done!** Your Custom GPT is now fully connected to your Tana workspace on Web, iOS, iPadOS, and Android.

---

## ⚠️ Honest Limitations: The Free User Reality Check

Most people setting this up will be running on free or lower-tier accounts. Here is exactly what to expect and where you will hit hard platform limits:

### 1. Cloudflare Free Tier Limitations ($0/Month)
Cloudflare's free tier requires no credit card and never expires, but has daily rate limits:
* **Initial Embedding Cap (Workers AI):** 10,000 neuron executions per day.
  - If your Tana workspace has **under 10,000 nodes**, your entire workspace will be indexed on day one.
  - If you have **over 10,000 nodes**, the background worker will index the first 10,000 nodes, pause when it hits the daily limit, and automatically resume the following day until the backfill is complete.
* **Monthly Vector Search Cap (Vectorize):** 5,000,000 queried vector dimensions per month.
  - Because we use compact 384-dimensional embeddings (`bge-small-en-v1.5`), this allows roughly **13,000 search queries per month** (~430 queries every day). This is more than enough for individual use.
* **Storage Cap (D1 Database):** 5 GB of SQLite storage. A typical 20,000-node graph takes less than 150 MB.

### 2. ChatGPT Free Tier Limitation
* **Custom GPT Actions require ChatGPT Plus or Team:** OpenAI restricts the ability to create Custom GPTs and attach private OpenAPI Actions to paid accounts ($20/mo). If you only have a free ChatGPT account, you cannot access the GPT Editor to add the action.
* **The 100% Free Alternative:** If you do not have ChatGPT Plus, connect the Cloudflare Worker to **Claude Desktop** (which supports Model Context Protocol 100% free with no subscription) or open-source frontends like **Cursor**, **LibreChat**, or **OpenWebUI**.

### 3. Tana Free (Core Plan) Limitation
* **API Access requires Tana Plus ($14/mo):** Tana does not provide API tokens on the free "Core" tier. To generate a Personal Access Token in **Settings → API Tokens**, an active Tana Plus subscription is required. Tana Pro is *not* required.

### 4. Initial Cold-Start Indexing Lag
* Tana's cloud API enforces per-minute rate limits to maintain system stability.
* When first deployed, syncing 3,000–8,000 nodes takes approximately **15–30 minutes** in the background. Queries made immediately after deployment will only search the nodes indexed so far.
* Check `https://<your-subdomain>.workers.dev/api/v1/sync/status` to monitor indexing progress.

---

## 🛠️ Alternative AI Clients: Claude Desktop & Cursor (MCP)

If you use **Claude Desktop**, **Cursor**, or an IDE that natively supports Model Context Protocol (MCP):

Add the remote server to your `claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "tana-context": {
      "command": "npx",
      "args": [
        "-y",
        "mcp-remote",
        "https://<your-subdomain>.workers.dev/sse"
      ]
    }
  }
}
```

---

## 📐 Workspace Usage Tip: Keep It Flat by Default

For the Semantic Engine to work cleanly with Tana's graph model, avoid creating deep outline hierarchies unless the parent-child relationship carries real structural meaning.

**Default to flat. Nest only when the parent-child relationship represents genuine containment or dependency.**

Use indentation mainly for:
- Tasks that genuinely belong to a project
- Sections inside a long document
- Components that are meaningful children of a specific object
- Information whose local sequence or context would become unclear if flattened

For looser associations between notes, ideas, people, resources, observations, or concepts, prefer Tana's semantic structure instead: **supertags, fields, references, backlinks, and search**.

In short: **use hierarchy for containment, not association**. This keeps the workspace easier to navigate and gives the Semantic Engine cleaner graph structure to retrieve and expand around.

Source note in Tana: [Using Tana](https://app.tana.inc?nodeid=GkIX9ji8s9X2)

---

## ❓ Frequently Asked Questions

### Do I need Tana Pro?
**No.** You only need **Tana Plus** ($14/mo) to generate an API token in **Settings → API Tokens**. Tana Pro is primarily for teams and shared workspaces.

### Do I need to connect two separate plugins to ChatGPT?
**No.** Tana Context Mask is the single bridge. It indexes your workspace vectors and proxies outliner queries so you only need one Action in ChatGPT.

### Do I need to keep my computer turned on?
**No.** Everything runs 24/7 on Cloudflare's serverless edge.

### Is my data private?
**Yes.** All data is stored in your private Cloudflare D1 database and Vectorize index. No intermediate third-party servers are involved.
