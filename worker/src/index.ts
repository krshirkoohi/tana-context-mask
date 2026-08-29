import { Hono } from 'hono';
import { cors } from 'hono/cors';
import { Env, ContextAcquisitionRequest, ContextPacket, ContextNode } from './types';
import { D1GraphStore } from './services/graph';
import { HybridSearchService } from './services/search';
import { EdgeReranker } from './services/reranker';
import { EdgeSyncService } from './services/sync';

const app = new Hono<{ Bindings: Env }>();

app.use('*', cors());

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

// Root Health & Overview
app.get('/', (c) => {
  return c.json({
    name: 'Tana Context Mask (Cloudflare Serverless Edge)',
    status: 'online',
    version: '1.0.0',
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

// Manual Sync Endpoint
app.post('/api/v1/sync', async (c) => {
  const syncService = new EdgeSyncService(c.env.DB, c.env.VECTORIZE, c.env.AI, c.env.TANA_API_TOKEN);
  const res = await syncService.syncRecentNodes(1);
  return c.json(res);
});

// Export default worker with Scheduled Cron handler
export default {
  fetch: app.fetch,
  async scheduled(event: ScheduledEvent, env: Env, ctx: ExecutionContext) {
    ctx.waitUntil((async () => {
      console.log('Running automated 15-minute Tana incremental sync...');
      const syncService = new EdgeSyncService(env.DB, env.VECTORIZE, env.AI, env.TANA_API_TOKEN);
      const res = await syncService.syncRecentNodes(1);
      console.log('Automated sync result:', res);
    })());
  }
};
