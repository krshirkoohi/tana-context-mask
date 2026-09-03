# Tana Context Mask (Cloudflare Serverless Worker)

Serverless edge context acquisition and retrieval layer running directly on Cloudflare's global edge network.

---

## Serverless Architecture

```
AI Clients (ChatGPT / Claude / MCP / Agents)
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

## Deployment (Zero Local Compute Needed)

### 1. Create Cloudflare D1 Database & Vectorize Index
```bash
cd worker
npm install

# Create Serverless SQLite Database
npx wrangler d1 create tana-db

# Create Serverless Vector Index (384 dimensions for BGE-small-en-v1.5)
npx wrangler vectorize create tana-nodes-index --dimensions=384 --metric=cosine
```
*Copy the `database_id` from the D1 output and set it in `wrangler.jsonc`.*

### 2. Initialize Remote D1 Database Schema
```bash
npm run db:init:remote
```

### 3. Add Your Tana API Token
```bash
npx wrangler secret put TANA_API_TOKEN
# Paste your Tana Personal Access Token when prompted
```

### 4. Deploy to Cloudflare Edge
```bash
npm run deploy
```
*Wrangler will output your live URL: `https://tana-context-mask.<your-subdomain>.workers.dev`.*

---

## Autonomous Cloud Ingestion & Sync

1. **Full Workspace Ingestion:** The Cloudflare Worker connects directly to Tana Cloud over HTTPS, paginates through your workspace in chunks of 250 nodes, generates embeddings using Cloudflare's edge GPUs (Workers AI), and stores them into D1 + Vectorize.
2. **Continuous 15-Minute Sync:** The Cloudflare Cron trigger automatically runs every 15 minutes in the cloud to pull newly created or modified notes from Tana.
3. **Telemetry & Telemetry Endpoint:** Visit `https://<your-worker-subdomain>.workers.dev/api/v1/sync/status` to inspect live ingestion progress and node counts.

---

## Client Integration

1. In your AI client (e.g., ChatGPT Custom GPT, LibreChat, or Claude MCP), point the configuration to your worker:
   - **OpenAPI Schema:** `https://<your-worker-subdomain>.workers.dev/openapi.json`
   - **MCP Stream:** `https://<your-worker-subdomain>.workers.dev/sse`
2. All context retrieval runs cloud-to-cloud with zero local dependencies.
