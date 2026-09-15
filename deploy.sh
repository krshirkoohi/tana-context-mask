#!/usr/bin/env bash
# ==============================================================================
# Tana Context Mask (Tana Semantic Engine) - Hardened Cloud Deployer
# Provisions Cloudflare D1 + Vectorize + Edge Worker with authentication enabled.
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKER_DIR="${SCRIPT_DIR}/worker"

echo "========================================================"
echo "   Tana Semantic Engine: Hardened Cloud Deployer"
echo "========================================================"
echo "Stack: Cloudflare Workers + Workers AI + Vectorize + D1"
echo "Public workers.dev and preview URLs remain disabled."
echo "========================================================"

cd "${WORKER_DIR}"

if ! npx wrangler whoami > /dev/null 2>&1; then
    echo "Authenticating with Cloudflare..."
    npx wrangler login
fi

echo "--> 1. Installing Worker dependencies..."
npm install --silent

echo "--> 2. Ensuring Cloudflare D1 database ('tana-db')..."
D1_OUTPUT=$(npx wrangler d1 create tana-db 2>&1 || true)
echo "${D1_OUTPUT}"

D1_ID=$(echo "${D1_OUTPUT}" | grep -E 'database_id = "([^"]+)"' | sed -E 's/.*database_id = "([^"]+)".*/\1/' || true)

if [ -n "${D1_ID}" ]; then
    echo "--> Updating wrangler.jsonc with database_id: ${D1_ID}"
    sed -i '' "s/\"database_id\": \"[^\"]*\"/\"database_id\": \"${D1_ID}\"/g" wrangler.jsonc || sed -i "s/\"database_id\": \"[^\"]*\"/\"database_id\": \"${D1_ID}\"/g" wrangler.jsonc
fi

echo "--> 3. Applying database schema to remote D1..."
npx wrangler d1 execute tana-db --file=schema.sql --remote -y

echo "--> 4. Ensuring Cloudflare Vectorize index ('tana-nodes-index')..."
npx wrangler vectorize create tana-nodes-index --dimensions=384 --metric=cosine 2>&1 || true

echo "--> 5. Setting Tana API token..."
if [ -z "${TANA_API_TOKEN:-}" ]; then
    echo "Enter your Tana Personal Access Token:"
    read -r -s TANA_API_TOKEN
    echo
fi
echo "${TANA_API_TOKEN}" | npx wrangler secret put TANA_API_TOKEN

echo "--> 6. Setting required API key..."
if [ -z "${API_KEY:-}" ]; then
    echo "Enter a long random API key for clients to use:"
    read -r -s API_KEY
    echo
fi
if [ -z "${API_KEY}" ]; then
    echo "ERROR: API_KEY cannot be empty. Deployment aborted."
    exit 1
fi
echo "${API_KEY}" | npx wrangler secret put API_KEY

echo "--> 7. Deploying hardened Worker..."
npx wrangler deploy

echo ""
echo "========================================================"
echo "DEPLOYMENT COMPLETE"
echo "========================================================"
echo "workers.dev: disabled by wrangler.jsonc"
echo "preview URLs: disabled by wrangler.jsonc"
echo "all HTTP routes: require API_KEY"
echo "legacy fake OAuth endpoints: disabled"
echo "========================================================"
echo "Add a protected custom domain or Cloudflare Access route before connecting clients."
