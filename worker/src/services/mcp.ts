import { D1Database, VectorizeIndex, Ai } from '@cloudflare/workers-types';
import { D1GraphStore } from './graph';
import { HybridSearchService } from './search';
import { EdgeReranker } from './reranker';
import { EdgeSyncService } from './sync';

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
                  max_nodes: { type: 'integer', default: 6, description: 'Maximum number of context nodes' }
                },
                required: ['task']
              }
            },
            {
              name: 'search_nodes',
              description: 'Direct hybrid semantic and keyword search across Tana corpus.',
              inputSchema: {
                type: 'object',
                properties: {
                  query: { type: 'string', description: 'Search query' },
                  limit: { type: 'integer', default: 20, description: 'Max results' }
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

        const scoredSeeds = await searchService.searchWithScores(query, 20, 0.50);
        const seedNodes = scoredSeeds.map(s => s.node);
        const seedScoreMap = new Map<string, number>(scoredSeeds.map(s => [s.node.id, s.score]));
        const expandedPairs = await graphStore.expandSubgraph(seedNodes, 15);
        const candidatePool = expandedPairs.map(p => ({
          node: p.node,
          score: seedScoreMap.has(p.node.id) ? seedScoreMap.get(p.node.id)! : 0.45,
          reason: p.reason
        }));
        const rankedCandidates = reranker.rerank(task, candidatePool, maxNodes);

        const mdSections: string[] = [];
        mdSections.push(`### 🌐 Tana Context Mask: Retrieved Knowledge Graph`);
        mdSections.push(`> **Task:** ${task}\n`);

        for (let i = 0; i < rankedCandidates.length; i++) {
          const { node, score, reason } = rankedCandidates[i];
          const tagsBadge = node.supertags.map(t => `\`#${t.tag_name}\``).join(' ');
          const deepLink = node.deep_link || `https://app.tana.inc/?nodeid=${node.id}`;

          const itemLines = [
            `#### ${i + 1}. [${node.name}](${deepLink}) ${tagsBadge}`,
            `- **Tana ID:** \`tana:${node.id}\` | **Relevance:** ${score.toFixed(2)}`,
            `- **Why included:** ${reason}`
          ];

          if (node.description) itemLines.push(`- **Description:** ${node.description}`);
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
        const results = await searchService.search(query, limit);

        return {
          jsonrpc: '2.0',
          id: id ?? null,
          result: {
            content: [
              {
                type: 'text',
                text: JSON.stringify(results.map(n => ({
                  id: n.id,
                  name: n.name,
                  description: n.description,
                  deep_link: n.deep_link,
                  tags: n.supertags.map(t => t.tag_name)
                })), null, 2)
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
