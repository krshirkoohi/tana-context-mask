# ⚡ Tana Context Mask (Cloudflare Serverless Worker)

An always-on, zero-cost (£0/mo) serverless implementation of **Tana Context Mask** running directly on Cloudflare's global edge network.

---

## 🏗️ Serverless Architecture

```
ChatGPT Actions (iOS / Web)
        │
        ▼ (HTTPS)
Cloudflare Worker (Hono Router)
        │
        ├── Cloudflare Workers AI  ──▶ Generates 384-dim BGE embeddings on edge GPUs
        ├── Cloudflare D1          ──▶ Serverless SQLite (FTS5 + Graph Nodes + Edges)
        ├── Cloudflare Vectorize   ──▶ Serverless Vector DB (Cosine similarity)
        └── Scheduled Cron         ──▶ Automated 15-minute background sync with Tana API
```

---

## 🚀 100% Cloud-Native Deployment (No Mac / Computer Needed)

### 1. Create Cloudflare D1 Database & Vectorize Index
```bash
cd worker
npm install

# Create Serverless SQLite Database
npx wrangler d1 create tana-db

# Create Serverless Vector Index (384 dimensions for BGE-small-en-v1.5)
npx wrangler vectorize create tana-nodes-index --dimensions=384 --metric=cosine
```
*Note: Copy the `database_id` from the D1 output and paste it into `wrangler.jsonc`.*

### 2. Initialize Remote D1 Database Schema
```bash
npm run db:init:remote
```

### 3. Add Your Tana API Token
```bash
npx wrangler secret put TANA_API_TOKEN
# Paste your Tana API Token when prompted
```

### 4. Deploy to Cloudflare Edge
```bash
npm run deploy
```
*Wrangler will output your live URL: `https://tana-context-mask.<your-subdomain>.workers.dev`.*

---

## ⚡ Autonomous 100% Cloud Ingestion & Sync

Once deployed, you never have to touch a computer again:
1. **Autonomous Full Workspace Ingestion:** The Cloudflare Worker will automatically connect directly to Tana Cloud over HTTPS, paginate through your entire workspace in chunks of 250 nodes, generate embeddings using Cloudflare's edge GPUs (Workers AI), and store them into D1 + Vectorize.
2. **Continuous 15-Minute Sync:** The Cloudflare Cron trigger automatically runs every 15 minutes in the cloud to pull newly created or modified notes from Tana.
3. **Check Cloud Status Anytime:** Visit `https://tana-context-mask.<your-subdomain>.workers.dev/api/v1/sync/status` in any mobile browser to see live ingestion progress and node counts.

---

## 🤖 ChatGPT Custom Action Setup

1. Open your Custom GPT at [chatgpt.com/gpts/editor](https://chatgpt.com/gpts/editor).
2. Under **Actions**, set the server URL in your OpenAPI schema to your Cloudflare Worker URL:
   ```json
   "servers": [
     {
       "url": "https://tana-context-mask.<your-subdomain>.workers.dev",
       "description": "Cloudflare Serverless Production"
     }
   ]
   ```
3. Talk to ChatGPT on your iPhone, iPad, or browser — all context retrieval runs 100% cloud-to-cloud with zero local dependencies.
