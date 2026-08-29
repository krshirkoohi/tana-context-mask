export const openApiSpec = {
  "openapi": "3.1.0",
  "info": {
    "title": "Tana Context Mask Plugin",
    "description": "An embedding-backed, graph-aware semantic context acquisition plugin for Tana Outliner.",
    "version": "1.0.0"
  },
  "servers": [
    {
      "url": "https://tana-context-mask.krshirkoohi.workers.dev",
      "description": "Cloudflare Serverless Production"
    }
  ],
  "paths": {
    "/": {
      "get": {
        "summary": "Root Health & Overview",
        "operationId": "root",
        "responses": {
          "200": { "description": "OK" }
        }
      }
    },
    "/api/v1/health": {
      "get": {
        "summary": "Health Check",
        "operationId": "healthCheck",
        "responses": {
          "200": { "description": "OK" }
        }
      }
    },
    "/api/v1/context/acquire": {
      "post": {
        "summary": "Acquire Context for AI Task",
        "description": "Proactively discovers, expands, and reranks relevant background context from Tana knowledge graph before answering a user request.",
        "operationId": "acquireContext",
        "requestBody": {
          "required": true,
          "content": {
            "application/json": {
              "schema": {
                "$ref": "#/components/schemas/ContextAcquisitionRequest"
              }
            }
          }
        },
        "responses": {
          "200": {
            "description": "Context Packet with grounded graph nodes",
            "content": {
              "application/json": {
                "schema": {
                  "$ref": "#/components/schemas/ContextPacket"
                }
              }
            }
          }
        }
      }
    },
    "/api/v1/search": {
      "post": {
        "summary": "Hybrid Search",
        "description": "Direct hybrid semantic and keyword search across Tana corpus.",
        "operationId": "searchNodes",
        "requestBody": {
          "required": true,
          "content": {
            "application/json": {
              "schema": {
                "type": "object",
                "properties": {
                  "query": { "type": "string" },
                  "limit": { "type": "integer", "default": 20 }
                },
                "required": ["query"]
              }
            }
          }
        },
        "responses": {
          "200": { "description": "Search Results" }
        }
      }
    },
    "/api/v1/nodes/{id}": {
      "get": {
        "summary": "Get Node Details",
        "description": "Inspect single node details with graph lineage.",
        "operationId": "getNode",
        "parameters": [
          {
            "name": "id",
            "in": "path",
            "required": true,
            "schema": { "type": "string" }
          }
        ],
        "responses": {
          "200": { "description": "Node Details" }
        }
      }
    }
  },
  "components": {
    "schemas": {
      "ContextAcquisitionRequest": {
        "type": "object",
        "properties": {
          "task": { "type": "string", "description": "The user request or prompt" },
          "query": { "type": "string", "description": "Optional refined search query" },
          "scope": { "type": "string", "description": "Optional supertag scope filter" },
          "max_nodes": { "type": "integer", "default": 6, "description": "Maximum number of context nodes" },
          "include_children": { "type": "boolean", "default": true, "description": "Whether to resolve child bullets" }
        },
        "required": ["task"]
      },
      "ContextNode": {
        "type": "object",
        "properties": {
          "id": { "type": "string" },
          "name": { "type": "string" },
          "description": { "type": "string" },
          "supertags": { "type": "array", "items": { "type": "string" } },
          "fields": { "type": "object", "additionalProperties": { "type": "string" } },
          "breadcrumbs": { "type": "array", "items": { "type": "string" } },
          "children_snippets": { "type": "array", "items": { "type": "string" } },
          "inclusion_reason": { "type": "string" },
          "relevance_score": { "type": "number" },
          "deep_link": { "type": "string" }
        },
        "required": ["id", "name", "deep_link"]
      },
      "ContextPacket": {
        "type": "object",
        "properties": {
          "trace_id": { "type": "string" },
          "task": { "type": "string" },
          "summary": { "type": "string" },
          "formatted_context_markdown": { "type": "string" },
          "nodes": { "type": "array", "items": { "$ref": "#/components/schemas/ContextNode" } },
          "total_candidates_examined": { "type": "integer" },
          "graph_expansion_count": { "type": "integer" },
          "latency_ms": { "type": "number" },
          "timestamp": { "type": "string" }
        },
        "required": ["trace_id", "task", "summary", "formatted_context_markdown", "nodes"]
      }
    }
  }
};
