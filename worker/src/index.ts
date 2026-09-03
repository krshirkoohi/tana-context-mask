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
app.get('/.well-known/oauth-authorization-server', (c) => {
  const url = new URL(c.req.url);
  const baseUrl = `${url.protocol}//${url.host}`;
  return c.json({
    issuer: baseUrl,
    authorization_endpoint: `${baseUrl}/oauth/authorize`,
    token_endpoint: `${baseUrl}/oauth/token`,
    userinfo_endpoint: `${baseUrl}/oauth/userinfo`,
    response_types_supported: ['code', 'token'],
    grant_types_supported: ['authorization_code', 'client_credentials'],
    token_endpoint_auth_methods_supported: ['client_secret_basic', 'client_secret_post', 'none'],
    scopes_supported: ['read', 'write', 'mcp']
  });
});
app.get('/.well-known/openid-configuration', (c) => {
  const url = new URL(c.req.url);
  const baseUrl = `${url.protocol}//${url.host}`;
  return c.json({
    issuer: baseUrl,
    authorization_endpoint: `${baseUrl}/oauth/authorize`,
    token_endpoint: `${baseUrl}/oauth/token`,
    userinfo_endpoint: `${baseUrl}/oauth/userinfo`,
    response_types_supported: ['code', 'token'],
    grant_types_supported: ['authorization_code', 'client_credentials'],
    token_endpoint_auth_methods_supported: ['client_secret_basic', 'client_secret_post', 'none'],
    scopes_supported: ['read', 'write', 'mcp']
  });
});
app.get('/.well-known/oauth-authorization-server/mc', (c) => c.redirect('/.well-known/oauth-authorization-server'));
app.get('/.well-known/oauth-authorization-server/mcp', (c) => c.redirect('/.well-known/oauth-authorization-server'));
app.get('/.well-known/oauth-authorization-server/sse', (c) => c.redirect('/.well-known/oauth-authorization-server'));

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
    sub: 'user',
    name: 'Tana User',
    email: 'user@tana.inc'
  });
});

// OpenAI AI Plugin Manifest
app.get('/.well-known/ai-plugin.json', (c) => {
  const url = new URL(c.req.url);
  const baseUrl = `${url.protocol}//${url.host}`;
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
      url: `${baseUrl}/openapi.json`
    },
    logo_url: `${baseUrl}/logo.png`,
    contact_email: 'support@tana.inc',
    legal_info_url: baseUrl
  });
});

// OpenAPI Spec Endpoint
app.get('/openapi.json', (c) => {
  const url = new URL(c.req.url);
  const baseUrl = `${url.protocol}//${url.host}`;
  return c.json({
    ...openApiSpec,
    servers: [
      {
        url: baseUrl,
        description: 'Cloudflare Serverless Production'
      }
    ]
  });
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

// Helper function for Context Acquisition
async function handleAcquireContext(
  c: any,
  rawTask: string,
  rawQuery?: string,
  rawMaxNodes?: number,
  rawTargetDate?: string,
  rawTemporalMode?: string
) {
  const startTime = Date.now();
  try {
    const task = (rawTask || '').trim();
    const query = (rawQuery || task).trim();
    const maxNodes = rawMaxNodes || 6;
    const targetDate = rawTargetDate;
    const temporalMode = rawTemporalMode || 'none';
    const traceId = crypto.randomUUID();

    if (!task && !query) {
      return c.json({ error: 'Task or query parameter must be provided' }, 400);
    }

    const graphStore = new D1GraphStore(c.env.DB);
    const searchService = new HybridSearchService(c.env.DB, c.env.VECTORIZE, c.env.AI, graphStore);
    const reranker = new EdgeReranker();

    // 1. Semantic + Temporal Search
    const searchResp = await searchService.searchWithTemporal({
      query,
      limit: 20,
      target_date: targetDate,
      temporal_mode: temporalMode as any
    });

    const seedNodes = searchResp.results.map(r => ({
      id: r.id,
      name: r.name,
      description: r.description,
      is_done: false,
      in_trash: false,
      supertags: r.tags.map(t => ({ tag_id: t, tag_name: t })),
      fields: [],
      children: [],
      references: [],
      backlinks: [],
      breadcrumbs: r.breadcrumbs,
      effective_date: r.effective_date,
      date_source: r.date_source,
      date_source_node_id: r.date_source_node_id,
      calendar_path: r.calendar_path,
      ancestor_day_node_id: r.ancestor_day_node_id,
      ancestor_month_node_id: r.ancestor_month_node_id,
      ancestor_year_node_id: r.ancestor_year_node_id
    }));

    const seedScoreMap = new Map<string, number>(searchResp.results.map(r => [r.id, r.score]));

    // 2. 1-Hop Graph Expansion
    const expandedPairs = await graphStore.expandSubgraph(seedNodes, 15);

    // 3. Score & Rerank Candidates
    const candidatePool = expandedPairs.map(p => ({
      node: p.node,
      score: seedScoreMap.has(p.node.id) ? seedScoreMap.get(p.node.id)! : 0.45,
      reason: p.reason
    }));

    const rankedCandidates = reranker.rerank(task, candidatePool, maxNodes, targetDate, undefined, undefined, temporalMode);
    const selectedNodes = rankedCandidates.map(r => r.node);

    // 4. Resolve Breadcrumbs and Child Snippets in parallel
    const [_, childrenSnippetsMap] = await Promise.all([
      graphStore.resolveAncestry(selectedNodes, 6),
      graphStore.resolveChildSnippets(selectedNodes, 8)
    ]);

    // 5. Format Markdown Context Packet
    const contextNodes: ContextNode[] = [];
    const mdSections: string[] = [];

    mdSections.push(`### 🌐 Tana Context Mask: Retrieved Knowledge Graph`);
    mdSections.push(`> **Task:** ${task}`);
    if (targetDate) {
      mdSections.push(`> **Target Date:** \`${targetDate}\` | **Temporal Mode:** \`${temporalMode}\``);
    }
    mdSections.push(`> **Trace ID:** \`${traceId}\` | **Nodes Selected:** ${rankedCandidates.length} of ${candidatePool.length} evaluated\n`);

    if (searchResp.insufficient_evidence) {
      mdSections.push(`> ⚠️ **Notice:** Insufficient contemporaneous evidence found in Tana for the requested historical period.\n`);
    }

    for (let i = 0; i < rankedCandidates.length; i++) {
      const { node, score, reason } = rankedCandidates[i];
      const tagsBadge = (node.supertags || []).map(t => `\`#${t.tag_name}\``).join(' ');
      const deepLink = node.deep_link || `https://app.tana.inc/?nodeid=${node.id}`;
      const snippets = childrenSnippetsMap.get(node.id) || [];

      const fieldsDict: Record<string, string> = {};
      for (const f of node.fields || []) {
        if (f.value_text || f.value_node_id) {
          fieldsDict[f.field_name] = f.value_text || f.value_node_id || '';
        }
      }

      contextNodes.push({
        id: node.id,
        name: node.name,
        description: node.description || '',
        supertags: (node.supertags || []).map(t => t.tag_name),
        fields: fieldsDict,
        breadcrumbs: node.breadcrumbs || [],
        children_snippets: snippets,
        references: node.references || [],
        backlinks: node.backlinks || [],
        inclusion_reason: reason,
        relevance_score: score,
        deep_link: deepLink,
        effective_date: node.effective_date,
        date_source: node.date_source
      });

      const itemLines = [
        `#### ${i + 1}. [${node.name}](${deepLink}) ${tagsBadge}`,
        `- **Tana ID:** \`tana:${node.id}\` | **Relevance:** ${score.toFixed(2)}`,
        `- **Why included:** ${reason}`
      ];

      if (node.effective_date) {
        itemLines.push(`- **Calendar Provenance:** \`${node.effective_date}\` (source: \`${node.date_source || 'day_node'}\`)`);
      }

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

      if (snippets.length > 0) {
        itemLines.push(`- **Sub-Items:**`);
        for (const s of snippets) {
          itemLines.push(`  - ${s}`);
        }
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
      timestamp: new Date().toISOString(),
      target_date: targetDate,
      temporal_mode: temporalMode,
      insufficient_evidence: searchResp.insufficient_evidence
    };

    return c.json(packet);
  } catch (err: any) {
    console.error('Context acquire error:', err);
    return c.json({ error: err.message, stack: err.stack }, 500);
  }
}

// Primary Context Acquisition for ChatGPT Actions (POST & GET)
app.post('/api/v1/context/acquire', async (c) => {
  let body: any = {};
  try {
    body = await c.req.json();
  } catch {
    // Non-JSON or empty body
  }
  const task = (body.task || body.query || body.prompt || body.q || body.input || body.message || c.req.query('task') || c.req.query('query') || c.req.query('q') || '').trim();
  const query = (body.query || body.q || task).trim();
  const maxNodes = body.max_nodes || parseInt(c.req.query('max_nodes') || '6', 10);
  const targetDate = body.target_date || c.req.query('target_date');
  const temporalMode = body.temporal_mode || c.req.query('temporal_mode');
  return handleAcquireContext(c, task, query, maxNodes, targetDate, temporalMode);
});

app.get('/api/v1/context/acquire', async (c) => {
  const task = (c.req.query('task') || c.req.query('query') || c.req.query('q') || c.req.query('prompt') || c.req.query('input') || '').trim();
  const query = (c.req.query('query') || c.req.query('q') || task).trim();
  const maxNodes = parseInt(c.req.query('max_nodes') || '6', 10);
  const targetDate = c.req.query('target_date');
  const temporalMode = c.req.query('temporal_mode');
  return handleAcquireContext(c, task, query, maxNodes, targetDate, temporalMode);
});

// Hybrid Search Endpoint (OpenAPI searchNodes - POST & GET)
app.post('/api/v1/search', async (c) => {
  try {
    let body: any = {};
    try {
      body = await c.req.json();
    } catch {}

    const query = (body.query || body.q || body.task || c.req.query('query') || c.req.query('q') || '').trim();
    const limit = body.limit || parseInt(c.req.query('limit') || '20', 10);
    const tagFilter = body.tag_filter || c.req.query('tag_filter');
    const targetDate = body.target_date || c.req.query('target_date');
    const dateFrom = body.date_from || c.req.query('date_from');
    const dateTo = body.date_to || c.req.query('date_to');
    const temporalMode = body.temporal_mode || c.req.query('temporal_mode') || 'none';

    if (!query) {
      return c.json({ error: 'Query parameter required' }, 400);
    }

    const graphStore = new D1GraphStore(c.env.DB);
    const searchService = new HybridSearchService(c.env.DB, c.env.VECTORIZE, c.env.AI, graphStore);
    const resp = await searchService.searchWithTemporal({
      query,
      limit,
      tag_filter: tagFilter,
      target_date: targetDate,
      date_from: dateFrom,
      date_to: dateTo,
      temporal_mode: temporalMode
    });

    return c.json(resp);
  } catch (err: any) {
    return c.json({ error: err.message }, 500);
  }
});

app.get('/api/v1/search', async (c) => {
  const query = (c.req.query('query') || c.req.query('q') || c.req.query('task') || '').trim();
  const limit = parseInt(c.req.query('limit') || '20', 10);
  const tagFilter = c.req.query('tag_filter');
  const targetDate = c.req.query('target_date');
  const dateFrom = c.req.query('date_from');
  const dateTo = c.req.query('date_to');
  const temporalMode = (c.req.query('temporal_mode') || 'none') as any;

  if (!query) {
    return c.json({ error: 'Query parameter required' }, 400);
  }
  const graphStore = new D1GraphStore(c.env.DB);
  const searchService = new HybridSearchService(c.env.DB, c.env.VECTORIZE, c.env.AI, graphStore);
  const resp = await searchService.searchWithTemporal({
    query,
    limit,
    tag_filter: tagFilter,
    target_date: targetDate,
    date_from: dateFrom,
    date_to: dateTo,
    temporal_mode: temporalMode
  });
  return c.json(resp);
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

// Cloud Sync Endpoint (Incremental Sync & Backfill)
app.post('/api/v1/sync', async (c) => {
  const lookback = parseInt(c.req.query('lookback_days') || '1', 10);
  const forceBackfill = c.req.query('force_backfill') === 'true' || c.req.query('backfill') === 'true';
  const syncService = new EdgeSyncService(c.env.DB, c.env.VECTORIZE, c.env.AI, c.env.TANA_API_TOKEN);
  const res = await syncService.syncRecentNodes(lookback, forceBackfill);
  return c.json(res);
});

// Explicit Backfill Endpoint
app.post('/api/v1/sync/backfill', async (c) => {
  const lookback = parseInt(c.req.query('lookback_days') || '7', 10);
  const syncService = new EdgeSyncService(c.env.DB, c.env.VECTORIZE, c.env.AI, c.env.TANA_API_TOKEN);
  const res = await syncService.syncRecentNodes(lookback, true);
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
