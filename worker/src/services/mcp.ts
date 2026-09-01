import { D1Database, VectorizeIndex, Ai } from '@cloudflare/workers-types';
import { D1GraphStore } from './graph';
import { HybridSearchService } from './search';
import { EdgeReranker } from './reranker';
import { EdgeSyncService } from './sync';
import { classifyTemporalRelationship } from './temporal';

export interface JSONRPCRequest {
  jsonrpc: string;
  id?: string | number | null;
  method: string;
  params?: any;
}

export class RemoteMCPHandler {
  constructor(
    private db: D1Database,
    private vectorize: VectorizeIndex,
    private ai: Ai,
    private tanaToken?: string
  ) {}

  async handleMessage(req: JSONRPCRequest): Promise<any> {
    const { method, params, id } = req;

    if (method === 'initialize') {
      return {
        jsonrpc: '2.0',
        id: id ?? null,
        result: {
          protocolVersion: '2024-11-05',
          capabilities: {
            tools: {}
          },
          serverInfo: {
            name: 'tana-context-mask',
            version: '1.0.0'
          }
        }
      };
    }

    if (method === 'notifications/initialized') {
      return null;
    }

    if (method === 'ping') {
      return { jsonrpc: '2.0', id: id ?? null, result: {} };
    }

    if (method === 'tools/list') {
      return {
        jsonrpc: '2.0',
        id: id ?? null,
        result: {
          tools: [
            {
              name: 'acquire_context',
              description: 'Proactively discovers, expands, and reranks relevant background context from Tana Outliner knowledge graph before answering a user request.',
              inputSchema: {
                type: 'object',
                properties: {
                  task: { type: 'string', description: 'The user prompt, task, or question' },
                  query: { type: 'string', description: 'Optional refined search query' },
                  scope: { type: 'string', description: 'Optional supertag scope filter' },
                  max_nodes: { type: 'integer', default: 6, description: 'Maximum number of context nodes' },
                  target_date: { type: 'string', description: 'Optional target date (YYYY-MM-DD) for historical context' },
                  temporal_mode: { type: 'string', enum: ['none', 'boost', 'filter', 'strict'], description: 'Temporal gating mode' }
                },
                required: ['task']
              }
            },
            {
              name: 'search_nodes',
              description: 'Direct hybrid semantic and keyword search across Tana corpus with calendar provenance and temporal gating.',
              inputSchema: {
                type: 'object',
                properties: {
                  query: { type: 'string', description: 'Search query' },
                  limit: { type: 'integer', default: 20, description: 'Max results' },
                  tag_filter: { type: 'string', description: 'Optional tag filter' },
                  target_date: { type: 'string', description: 'Optional target date (YYYY-MM-DD)' },
                  date_from: { type: 'string', description: 'Optional start date' },
                  date_to: { type: 'string', description: 'Optional end date' },
                  temporal_mode: { type: 'string', enum: ['none', 'boost', 'filter', 'strict'], description: 'Temporal gating mode' }
                },
                required: ['query']
              }
            },
            {
              name: 'get_node_context',
              description: 'Inspect single node details with full graph lineage, parents, children, and references.',
              inputSchema: {
                type: 'object',
                properties: {
                  node_id: { type: 'string', description: 'Tana Node ID' }
                },
                required: ['node_id']
              }
            },
            {
              name: 'get_sync_status',
              description: 'Get live sync and node count health statistics for the Tana mirror.',
              inputSchema: {
                type: 'object',
                properties: {}
              }
            }
          ]
        }
      };
    }

    if (method === 'tools/call') {
      const toolName = params?.name;
      const toolArgs = params?.arguments || {};

      const graphStore = new D1GraphStore(this.db);
      const searchService = new HybridSearchService(this.db, this.vectorize, this.ai, graphStore);
      const reranker = new EdgeReranker();

      if (toolName === 'acquire_context') {
        const task = (toolArgs.task || '').trim();
        const query = (toolArgs.query || task).trim();
        const maxNodes = toolArgs.max_nodes || 6;
        const targetDate = toolArgs.target_date;
        const temporalMode = toolArgs.temporal_mode || 'none';

        const searchResp = await searchService.searchWithTemporal({
          query,
          limit: 20,
          target_date: targetDate,
          temporal_mode: temporalMode
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
        const expandedPairs = await graphStore.expandSubgraph(seedNodes, 15);
        const candidatePool = expandedPairs.map(p => ({
          node: p.node,
          score: seedScoreMap.has(p.node.id) ? seedScoreMap.get(p.node.id)! : 0.45,
          reason: p.reason
        }));
        const rankedCandidates = reranker.rerank(task, candidatePool, maxNodes, targetDate, undefined, undefined, temporalMode);
        const selectedNodes = rankedCandidates.map(r => r.node);

        const [_, childrenSnippetsMap] = await Promise.all([
          graphStore.resolveAncestry(selectedNodes, 6),
          graphStore.resolveChildSnippets(selectedNodes, 8)
        ]);

        const mdSections: string[] = [];
        mdSections.push(`### 🌐 Tana Context Mask: Retrieved Knowledge Graph`);
        mdSections.push(`> **Task:** ${task}`);
        if (targetDate) {
          mdSections.push(`> **Target Date:** \`${targetDate}\` | **Temporal Mode:** \`${temporalMode}\``);
        }
        mdSections.push('');

        if (searchResp.insufficient_evidence) {
          mdSections.push(`> ⚠️ **Notice:** Insufficient contemporaneous evidence found in Tana for the requested period.\n`);
        }

        for (let i = 0; i < rankedCandidates.length; i++) {
          const { node, score, reason } = rankedCandidates[i];
          const tagsBadge = (node.supertags || []).map(t => `\`#${t.tag_name}\``).join(' ');
          const deepLink = node.deep_link || `https://app.tana.inc/?nodeid=${node.id}`;
          const snippets = childrenSnippetsMap.get(node.id) || [];

          const relClass = classifyTemporalRelationship(node, targetDate);
          let relBadge = '';
          if (relClass === 'contemporaneous') {
            relBadge = ` \`[Contemporaneous · ${node.effective_date || 'verified'}]\``;
          } else if (relClass === 'retrospective') {
            relBadge = ` \`[Retrospective · recorded ${node.effective_date || 'later'}]\``;
          }

          const itemLines = [
            `#### ${i + 1}. [${node.name}](${deepLink}) ${tagsBadge}${relBadge}`,
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

          if (snippets.length > 0) {
            itemLines.push(`- **Sub-Items:**`);
            for (const s of snippets) {
              itemLines.push(`  - ${s}`);
            }
          }

          mdSections.push(itemLines.join('\n'));
        }

        return {
          jsonrpc: '2.0',
          id: id ?? null,
          result: {
            content: [
              {
                type: 'text',
                text: mdSections.join('\n\n')
              }
            ]
          }
        };
      }

      if (toolName === 'search_nodes') {
        const query = (toolArgs.query || '').trim();
        const limit = toolArgs.limit || 20;
        const targetDate = toolArgs.target_date;
        const dateFrom = toolArgs.date_from;
        const dateTo = toolArgs.date_to;
        const temporalMode = toolArgs.temporal_mode || 'none';

        const resp = await searchService.searchWithTemporal({
          query,
          limit,
          tag_filter: toolArgs.tag_filter,
          target_date: targetDate,
          date_from: dateFrom,
          date_to: dateTo,
          temporal_mode: temporalMode
        });

        return {
          jsonrpc: '2.0',
          id: id ?? null,
          result: {
            content: [
              {
                type: 'text',
                text: JSON.stringify(resp, null, 2)
              }
            ]
          }
        };
      }

      if (toolName === 'get_node_context') {
        const nodeId = toolArgs.node_id;
        const nodes = await graphStore.getNodesByIds([nodeId]);
        if (!nodes || nodes.length === 0) {
          return {
            jsonrpc: '2.0',
            id: id ?? null,
            isError: true,
            result: { content: [{ type: 'text', text: `Node ${nodeId} not found` }] }
          };
        }
        return {
          jsonrpc: '2.0',
          id: id ?? null,
          result: { content: [{ type: 'text', text: JSON.stringify(nodes[0], null, 2) }] }
        };
      }

      if (toolName === 'get_sync_status') {
        const syncService = new EdgeSyncService(this.db, this.vectorize, this.ai, this.tanaToken);
        const stats = await syncService.getSyncStats();
        return {
          jsonrpc: '2.0',
          id: id ?? null,
          result: { content: [{ type: 'text', text: JSON.stringify(stats, null, 2) }] }
        };
      }

      return {
        jsonrpc: '2.0',
        id: id ?? null,
        error: { code: -32601, message: `Tool not found: ${toolName}` }
      };
    }

    return {
      jsonrpc: '2.0',
      id: id ?? null,
      error: { code: -32601, message: `Method not supported: ${method}` }
    };
  }
}
