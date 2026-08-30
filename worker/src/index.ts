import { Hono } from 'hono';
import { cors } from 'hono/cors';
import { Env, ContextAcquisitionRequest, ContextPacket, ContextNode } from './types';
import { D1GraphStore } from './services/graph';
import { HybridSearchService } from './services/search';
import { EdgeReranker } from './services/reranker';
import { EdgeSyncService } from './services/sync';

import { openApiSpec } from './openapi';

import { RemoteMCPHandler } from './services/mcp';

const app = new Hono<{ Bindings: Env }>();

app.use('*', cors());

import { streamSSE } from 'hono/streaming';

// Model Context Protocol (MCP) SSE Transport Endpoint
app.get('/sse', async (c) => {
  const sessionId = crypto.randomUUID();
  return streamSSE(c, async (stream) => {
    await stream.writeSSE({
      event: 'endpoint',
      data: `/messages?sessionId=${sessionId}`
    });
    while (true) {
      await stream.sleep(15000);
      await stream.writeSSE({ event: 'ping', data: 'heartbeat' });
    }
  });
});

app.get('/mcp', async (c) => {
  const sessionId = crypto.randomUUID();
  return streamSSE(c, async (stream) => {
    await stream.writeSSE({
      event: 'endpoint',
      data: `/messages?sessionId=${sessionId}`
    });
    while (true) {
      await stream.sleep(15000);
      await stream.writeSSE({ event: 'ping', data: 'heartbeat' });
    }
  });
});

// MCP JSON-RPC Message Handling
app.post('/messages', async (c) => {
  const body = await c.req.json();
  const mcpHandler = new RemoteMCPHandler(c.env.DB, c.env.VECTORIZE, c.env.AI, c.env.TANA_API_TOKEN);
  const response = await mcpHandler.handleMessage(body);
  if (!response) {
    return new Response(null, { status: 202 });
  }
  return c.json(response);
});

app.post('/mcp', async (c) => {
  const body = await c.req.json();
  const mcpHandler = new RemoteMCPHandler(c.env.DB, c.env.VECTORIZE, c.env.AI, c.env.TANA_API_TOKEN);
  const response = await mcpHandler.handleMessage(body);
  return c.json(response);
});

// Alias /mc to /mcp for convenience
app.post('/mc', async (c) => {
  const body = await c.req.json();
  const mcpHandler = new RemoteMCPHandler(c.env.DB, c.env.VECTORIZE, c.env.AI, c.env.TANA_API_TOKEN);
  const response = await mcpHandler.handleMessage(body);
  return c.json(response);
});

// OAuth 2.0 Discovery Endpoints (RFC 8414 & OpenID Connect)
const oauthMetadata = {
  issuer: 'https://tana-context-mask.krshirkoohi.workers.dev',
  authorization_endpoint: 'https://tana-context-mask.krshirkoohi.workers.dev/oauth/authorize',
  token_endpoint: 'https://tana-context-mask.krshirkoohi.workers.dev/oauth/token',
  userinfo_endpoint: 'https://tana-context-mask.krshirkoohi.workers.dev/oauth/userinfo',
  response_types_supported: ['code', 'token'],
  grant_types_supported: ['authorization_code', 'client_credentials'],
  token_endpoint_auth_methods_supported: ['client_secret_basic', 'client_secret_post', 'none'],
  scopes_supported: ['read', 'write', 'mcp']
};

app.get('/.well-known/oauth-authorization-server', (c) => c.json(oauthMetadata));
app.get('/.well-known/openid-configuration', (c) => c.json(oauthMetadata));
app.get('/.well-known/oauth-authorization-server/mc', (c) => c.json(oauthMetadata));
app.get('/.well-known/oauth-authorization-server/mcp', (c) => c.json(oauthMetadata));
app.get('/.well-known/oauth-authorization-server/sse', (c) => c.json(oauthMetadata));

// Instant Auto-Approve OAuth Authorisation Endpoint
app.get('/oauth/authorize', (c) => {
  const redirectUri = c.req.query('redirect_uri');
  const state = c.req.query('state') || '';
  if (redirectUri) {
    const separator = redirectUri.includes('?') ? '&' : '?';
    return c.redirect(`${redirectUri}${separator}code=tana_auth_success&state=${encodeURIComponent(state)}`);
  }
  return c.text('OAuth Authorisation Successful. You can close this window.');
});

// OAuth Token Exchange Endpoint
app.post('/oauth/token', async (c) => {
  return c.json({
    access_token: 'tana_mcp_session_token_' + crypto.randomUUID(),
    token_type: 'Bearer',
    expires_in: 315360000,
    scope: 'mcp'
  });
});

app.get('/oauth/userinfo', (c) => {
  return c.json({
    sub: 'kavia',
    name: 'Kavia',
    email: 'krshirkoohi@gmail.com'
  });
});

// OpenAI AI Plugin Manifest
app.get('/.well-known/ai-plugin.json', (c) => {
  return c.json({
    schema_version: 'v1',
    name_for_human: 'Tana Context Mask',
    name_for_model: 'tana_context_mask',
    description_for_human: 'Semantic and graph-aware context acquisition plugin for Tana Outliner.',
    description_for_model: 'Proactively discovers, expands, and reranks relevant background context from Tana Outliner knowledge graph before answering user questions about notes, tasks, meetings, people, and projects.',
    auth: {
      type: 'none'
    },
    api: {
      type: 'openapi',
      url: 'https://tana-context-mask.krshirkoohi.workers.dev/openapi.json'
    },
    logo_url: 'https://tana-context-mask.krshirkoohi.workers.dev/logo.png',
    contact_email: 'krshirkoohi@gmail.com',
    legal_info_url: 'https://tana-context-mask.krshirkoohi.workers.dev'
  });
});

// OpenAPI Spec Endpoint
app.get('/openapi.json', (c) => {
  return c.json(openApiSpec);
});

// Plugin Logo Endpoint
app.get('/logo.png', (c) => {
  c.header('Content-Type', 'image/svg+xml');
  return c.body(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="100" height="100">
    <circle cx="50" cy="50" r="45" fill="#5E5CE6" />
    <path d="M30 50 L45 65 L70 35" stroke="#FFFFFF" stroke-width="8" stroke-linecap="round" stroke-linejoin="round" fill="none"/>
  </svg>`);
});

// Authentication Middleware
app.use('/api/*', async (c, next) => {
  const apiKey = c.env.API_KEY;
  if (apiKey) {
    const headerKey = c.req.header('x-api-key') || c.req.header('Authorization')?.replace('Bearer ', '');
    if (headerKey !== apiKey) {
      return c.json({ error: 'Unauthorized' }, 401);
    }
  }
  await next();
});

// Root: Supports both GET overview and POST MCP JSON-RPC
app.post('/', async (c) => {
  try {
    const body = await c.req.json();
    if (body && body.jsonrpc === '2.0') {
      const mcpHandler = new RemoteMCPHandler(c.env.DB, c.env.VECTORIZE, c.env.AI, c.env.TANA_API_TOKEN);
      const res = await mcpHandler.handleMessage(body);
      return c.json(res);
    }
  } catch {
    // Ignore and return root
  }
  return c.json({ error: 'Invalid JSON-RPC payload' }, 400);
});

// Root Health & Overview
app.get('/', (c) => {
  return c.json({
    name: 'Tana Context Mask (Cloudflare Serverless Plugin)',
    status: 'online',
    version: '1.0.0',
    plugin_manifest: '/.well-known/ai-plugin.json',
    openapi_spec: '/openapi.json',
    endpoints: {
      acquireContext: 'POST /api/v1/context/acquire',
      search: 'POST /api/v1/search',
      node: 'GET /api/v1/nodes/:id',
      sync: 'POST /api/v1/sync',
      health: 'GET /api/v1/health'
    }
  });
});

// Health check
app.get('/api/v1/health', async (c) => {
  const { results } = await c.env.DB.prepare('SELECT count(*) as count FROM nodes').all<any>();
  const count = results?.[0]?.count || 0;
  return c.json({
    status: 'healthy',
    total_nodes_d1: count,
    environment: c.env.ENVIRONMENT || 'production'
  });
});

// Primary Context Acquisition for ChatGPT Actions
app.post('/api/v1/context/acquire', async (c) => {
  const startTime = Date.now();
  try {
    const body = await c.req.json<ContextAcquisitionRequest>();
    const task = (body.task || '').trim();
    const query = (body.query || task).trim();
    const maxNodes = body.max_nodes || 6;
    const traceId = crypto.randomUUID();

    if (!task && !query) {
      return c.json({ error: 'Task or query must be provided' }, 400);
    }

    const graphStore = new D1GraphStore(c.env.DB);
    const searchService = new HybridSearchService(c.env.DB, c.env.VECTORIZE, c.env.AI, graphStore);
    const reranker = new EdgeReranker();

    // 1. Semantic + Keyword Search
    const seedNodes = await searchService.search(query, 15, 0.65);

    // 2. 1-Hop Graph Expansion
    const expandedPairs = await graphStore.expandSubgraph(seedNodes, 12);

    // 3. Score & Rerank Candidates
    const candidatePool = expandedPairs.map(p => ({
      node: p.node,
      score: seedNodes.some(s => s.id === p.node.id) ? 0.85 : 0.45,
      reason: p.reason
    }));

    const rankedCandidates = reranker.rerank(task, candidatePool, maxNodes);

    // 4. Format Markdown Context Packet
    const contextNodes: ContextNode[] = [];
    const mdSections: string[] = [];

    mdSections.push(`### 🌐 Tana Context Mask: Retrieved Knowledge Graph`);
    mdSections.push(`> **Task:** ${task}`);
    mdSections.push(`> **Trace ID:** \`${traceId}\` | **Nodes Selected:** ${rankedCandidates.length} of ${candidatePool.length} evaluated\n`);

    for (let i = 0; i < rankedCandidates.length; i++) {
      const { node, score, reason } = rankedCandidates[i];
      const tagsBadge = node.supertags.map(t => `\`#${t.tag_name}\``).join(' ');
      const deepLink = node.deep_link || `https://app.tana.inc/?nodeid=${node.id}`;

      const fieldsDict: Record<string, string> = {};
      for (const f of node.fields) {
        if (f.value_text || f.value_node_id) {
          fieldsDict[f.field_name] = f.value_text || f.value_node_id || '';
        }
      }

      contextNodes.push({
        id: node.id,
        name: node.name,
        description: node.description || '',
        supertags: node.supertags.map(t => t.tag_name),
        fields: fieldsDict,
        breadcrumbs: node.breadcrumbs,
        children_snippets: [],
        references: node.references,
        backlinks: node.backlinks,
        inclusion_reason: reason,
        relevance_score: score,
        deep_link: deepLink
      });

      const itemLines = [
        `#### ${i + 1}. [${node.name}](${deepLink}) ${tagsBadge}`,
        `- **Tana ID:** \`tana:${node.id}\` | **Relevance:** ${score.toFixed(2)}`,
        `- **Why included:** ${reason}`
      ];

      if (node.breadcrumbs && node.breadcrumbs.length > 0) {
        itemLines.push(`- **Hierarchy:** ${node.breadcrumbs.join(' > ')}`);
      }

      if (node.description) {
        itemLines.push(`- **Description:** ${node.description}`);
      }

      const fieldsList = Object.entries(fieldsDict).map(([k, v]) => `\`${k}\`: ${v}`).join(', ');
      if (fieldsList) {
        itemLines.push(`- **Fields:** ${fieldsList}`);
      }

      mdSections.push(itemLines.join('\n'));
    }

    const latencyMs = Date.now() - startTime;
    const formattedMarkdown = mdSections.join('\n\n');

    const packet: ContextPacket = {
      trace_id: traceId,
      task: task,
      summary: `Acquired ${contextNodes.length} contextual nodes from Tana edge mirror in ${latencyMs}ms.`,
      formatted_context_markdown: formattedMarkdown,
      nodes: contextNodes,
      total_candidates_examined: candidatePool.length,
      graph_expansion_count: expandedPairs.length - seedNodes.length,
      latency_ms: latencyMs,
      timestamp: new Date().toISOString()
    };

    return c.json(packet);
  } catch (err: any) {
    console.error('Context acquire error:', err);
    return c.json({ error: err.message, stack: err.stack }, 500);
  }
});

// Single node inspection
app.get('/api/v1/nodes/:id', async (c) => {
  const id = c.req.param('id');
  const graphStore = new D1GraphStore(c.env.DB);
  const nodes = await graphStore.getNodesByIds([id]);
  if (!nodes || nodes.length === 0) {
    return c.json({ error: 'Node not found' }, 404);
  }
  return c.json(nodes[0]);
});

// Cloud Sync Endpoint (Incremental Sync)
app.post('/api/v1/sync', async (c) => {
  const lookback = parseInt(c.req.query('lookback_days') || '1', 10);
  const syncService = new EdgeSyncService(c.env.DB, c.env.VECTORIZE, c.env.AI, c.env.TANA_API_TOKEN);
  const res = await syncService.syncRecentNodes(lookback);
  return c.json(res);
});

// Cloud Sync & Mirror Status
app.get('/api/v1/sync/status', async (c) => {
  const syncService = new EdgeSyncService(c.env.DB, c.env.VECTORIZE, c.env.AI, c.env.TANA_API_TOKEN);
  const stats = await syncService.getSyncStats();
  return c.json(stats);
});

// Export default worker with Autonomous Scheduled Cron handler
export default {
  fetch: app.fetch,
  async scheduled(event: ScheduledEvent, env: Env, ctx: ExecutionContext) {
    ctx.waitUntil((async () => {
      const syncService = new EdgeSyncService(env.DB, env.VECTORIZE, env.AI, env.TANA_API_TOKEN);
      console.log('[Cloud Cron] Running automated 15-minute Tana incremental diff sync...');
      const res = await syncService.syncRecentNodes(1);
      console.log('[Cloud Cron] Incremental sync result:', res);
    })());
  }
};
