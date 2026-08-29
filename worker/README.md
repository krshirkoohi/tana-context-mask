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

## 🚀 One-Time Deployment Guide

### 1. Install Dependencies
```bash
cd worker
npm install
```

### 2. Create Cloudflare D1 Database & Vectorize Index
```bash
# Create Serverless SQLite Database
npx wrangler d1 create tana-db

# Create Serverless Vector Index (384 dimensions for BGE-small-en-v1.5)
npx wrangler vectorize create tana-nodes-index --dimensions=384 --metric=cosine
```
*Note: Copy the `database_id` printed by the D1 command and paste it into `wrangler.jsonc`.*

### 3. Initialize Remote D1 Database Schema
```bash
npm run db:init:remote
```

### 4. Configure Your Tana API Token
```bash
npx wrangler secret put TANA_API_TOKEN
# Paste your Tana API Token when prompted
```

*(Optional) Set an API Key for Custom GPT authentication:*
```bash
npx wrangler secret put API_KEY
```

### 5. Deploy to Cloudflare Edge
```bash
npm run deploy
```
*Wrangler will output your live URL: `https://tana-context-mask.<your-subdomain>.workers.dev`.*

---

## 📦 Seeding Historical Tana Export

To seed your Cloudflare D1 database with an offline Tana JSON export:

```bash
# 1. Generate the SQL seed script
python3 scripts/export_to_cf.py "/path/to/tana-export.json" .

# 2. Execute the seed script on remote D1
npx wrangler d1 execute tana-db --remote --file=d1_seed.sql
```

---

## 🤖 ChatGPT Custom Action Setup

1. Open your Custom GPT at [chatgpt.com/gpts/editor](https://chatgpt.com/gpts/editor).
2. Under **Actions**, update the server URL in your OpenAPI JSON to your Cloudflare Worker URL:
   ```json
   "servers": [
     {
       "url": "https://tana-context-mask.<your-subdomain>.workers.dev",
       "description": "Cloudflare Serverless Production"
     }
   ]
   ```
3. Test your Custom GPT on Web, iOS, or Android without needing your Mac turned on!
