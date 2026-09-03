#!/usr/bin/env bash
# ==============================================================================
# Tana Context Mask (Tana Semantic Engine) - Automated One-Click Cloud Deployer
# Provisions Cloudflare D1 + Vectorize + Edge Worker with zero local dependencies.
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKER_DIR="${SCRIPT_DIR}/worker"

echo "========================================================"
echo "   ⚡ Tana Semantic Engine: Edge Cloud Deployer          "
echo "========================================================"
echo "Stack: Cloudflare Workers + Workers AI + Vectorize + D1"
echo "Runs 24/7 online. Your computer does NOT need to stay on."
echo "========================================================"

cd "${WORKER_DIR}"

if ! npx wrangler whoami > /dev/null 2>&1; then
    echo "🔑 Authenticating with Cloudflare..."
    npx wrangler login
fi

echo "--> 1. Installing Worker Dependencies..."
npm install --silent

echo "--> 2. Ensuring Cloudflare D1 Database ('tana-db')..."
D1_OUTPUT=$(npx wrangler d1 create tana-db 2>&1 || true)
echo "${D1_OUTPUT}"

D1_ID=$(echo "${D1_OUTPUT}" | grep -E 'database_id = "([^"]+)"' | sed -E 's/.*database_id = "([^"]+)".*/\1/' || true)

if [ -n "${D1_ID}" ]; then
    echo "--> Updating wrangler.jsonc with database_id: ${D1_ID}"
    sed -i '' "s/\"database_id\": \"[^\"]*\"/\"database_id\": \"${D1_ID}\"/g" wrangler.jsonc || sed -i "s/\"database_id\": \"[^\"]*\"/\"database_id\": \"${D1_ID}\"/g" wrangler.jsonc
fi

echo "--> 3. Applying Database Schema to Remote D1..."
npx wrangler d1 execute tana-db --file=schema.sql --remote -y

echo "--> 4. Ensuring Cloudflare Vectorize Index ('tana-nodes-index')..."
npx wrangler vectorize create tana-nodes-index --dimensions=384 --metric=cosine 2>&1 || true

echo "--> 5. Checking Tana API Secret Token..."
if [ -z "${TANA_API_TOKEN:-}" ]; then
    echo "Please enter your Tana Personal Access Token (from Tana Settings > API Tokens):"
    read -r -s TANA_API_TOKEN
fi
echo "${TANA_API_TOKEN}" | npx wrangler secret put TANA_API_TOKEN

echo "--> 6. Deploying to Cloudflare Global Edge..."
DEPLOY_OUTPUT=$(npx wrangler deploy)
echo "${DEPLOY_OUTPUT}"

WORKER_URL=$(echo "${DEPLOY_OUTPUT}" | grep -Eo 'https://[a-zA-Z0-9.-]+\.workers\.dev' | head -n 1 || true)

echo ""
echo "========================================================"
echo "🎉 DEPLOYMENT COMPLETE! YOUR ENGINE IS LIVE 24/7 ONLINE"
echo "========================================================"
echo "Live Worker URL:     ${WORKER_URL}"
echo "OpenAPI Action Spec: ${WORKER_URL}/openapi.json"
echo "AI Plugin Manifest:  ${WORKER_URL}/.well-known/ai-plugin.json"
echo "Remote MCP Stream:   ${WORKER_URL}/sse"
echo "Health & Status:     ${WORKER_URL}/api/v1/health"
echo "========================================================"
