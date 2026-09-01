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
  effective_date?: string;
  date_source?: string;
  date_source_node_id?: string;
  calendar_distance?: number;
  calendar_path?: string;
  ancestor_day_node_id?: string;
  ancestor_week_node_id?: string;
  ancestor_month_node_id?: string;
  ancestor_year_node_id?: string;
}

export interface ContextAcquisitionRequest {
  task: string;
  query?: string;
  scope?: string;
  max_nodes?: number;
  include_children?: boolean;
  target_date?: string;
  date_from?: string;
  date_to?: string;
  temporal_mode?: 'none' | 'boost' | 'filter' | 'strict';
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
  effective_date?: string;
  date_source?: string;
  temporal_relationship?: string;
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
  target_date?: string;
  temporal_mode?: string;
  insufficient_evidence?: boolean;
}

export interface ScoredCandidate {
  node: TanaNode;
  score: number;
  reason: string;
}

export interface SemanticSearchRequest {
  query: string;
  limit?: number;
  tag_filter?: string;
  target_date?: string;
  date_from?: string;
  date_to?: string;
  temporal_mode?: 'none' | 'boost' | 'filter' | 'strict';
  temporal_weight?: number;
}

export interface SearchResultItem {
  id: string;
  name: string;
  description: string;
  score: number;
  semantic_score?: number;
  lexical_score?: number;
  breadcrumbs: string[];
  tags: string[];
  parent_id?: string;
  last_updated?: string;
  effective_date?: string;
  date_source?: string;
  date_source_node_id?: string;
  calendar_path?: string;
  ancestor_day_node_id?: string;
  ancestor_month_node_id?: string;
  ancestor_year_node_id?: string;
  temporal_relationship?: string;
  temporal_score?: number;
}

export interface SearchResponse {
  query: string;
  total_hits: number;
  results: SearchResultItem[];
  latency_ms: number;
  target_date?: string;
  temporal_mode?: string;
  insufficient_evidence?: boolean;
}
