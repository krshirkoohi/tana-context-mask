export interface Env {
  DB: D1Database;
  VECTORIZE: VectorizeIndex;
  AI: Ai;
  ENVIRONMENT: string;
  TANA_API_TOKEN?: string;
  API_KEY?: string;
}

export interface NodeTag {
  tag_id: string;
  tag_name: string;
}

export interface NodeField {
  field_id: string;
  field_name: string;
  value_text?: string;
  value_node_id?: string;
}

export interface TanaNode {
  id: string;
  name: string;
  description?: string;
  doc_type?: string;
  parent_id?: string;
  created_at?: string;
  updated_at?: string;
  content_hash?: string;
  structure_hash?: string;
  is_done: boolean;
  in_trash: boolean;
  supertags: NodeTag[];
  fields: NodeField[];
  children: string[];
  references: string[];
  backlinks: string[];
  breadcrumbs: string[];
  deep_link?: string;
}

export interface ContextAcquisitionRequest {
  task: string;
  query?: string;
  scope?: string;
  max_nodes?: number;
  include_children?: boolean;
}

export interface ContextNode {
  id: string;
  name: string;
  description: string;
  supertags: string[];
  fields: Record<string, string>;
  breadcrumbs: string[];
  children_snippets: string[];
  references: string[];
  backlinks: string[];
  inclusion_reason: string;
  relevance_score: number;
  deep_link: string;
}

export interface ContextPacket {
  trace_id: string;
  task: string;
  summary: string;
  formatted_context_markdown: string;
  nodes: ContextNode[];
  total_candidates_examined: number;
  graph_expansion_count: number;
  latency_ms: number;
  timestamp: string;
}

export interface ScoredCandidate {
  node: TanaNode;
  score: number;
  reason: string;
}
