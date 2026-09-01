-- Cloudflare D1 SQL Schema for Tana Context Mask

CREATE TABLE IF NOT EXISTS nodes (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    doc_type TEXT,
    parent_id TEXT,
    created_at TEXT,
    updated_at TEXT,
    content_hash TEXT,
    structure_hash TEXT,
    is_done INTEGER DEFAULT 0,
    in_trash INTEGER DEFAULT 0,
    effective_date TEXT,
    date_source TEXT,
    date_source_node_id TEXT,
    calendar_distance INTEGER,
    calendar_path TEXT,
    ancestor_day_node_id TEXT,
    ancestor_week_node_id TEXT,
    ancestor_month_node_id TEXT,
    ancestor_year_node_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_nodes_parent ON nodes(parent_id);
CREATE INDEX IF NOT EXISTS idx_nodes_trash ON nodes(in_trash);
CREATE INDEX IF NOT EXISTS idx_nodes_updated ON nodes(updated_at);
CREATE INDEX IF NOT EXISTS idx_nodes_effective_date ON nodes(effective_date);
CREATE INDEX IF NOT EXISTS idx_nodes_ancestor_day ON nodes(ancestor_day_node_id);
CREATE INDEX IF NOT EXISTS idx_nodes_ancestor_year ON nodes(ancestor_year_node_id);

-- Full Text Search Index using SQLite FTS5
CREATE VIRTUAL TABLE IF NOT EXISTS node_fts USING fts5(
    id UNINDEXED,
    name,
    description,
    tokenize = 'porter unicode61'
);

-- Graph Edges (Parent-Child, Reference, Tag)
CREATE TABLE IF NOT EXISTS edges (
    source_id TEXT,
    target_id TEXT,
    relation_type TEXT,
    attribute_id TEXT DEFAULT '',
    PRIMARY KEY (source_id, target_id, relation_type, attribute_id)
);

CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_id);
CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_id);
CREATE INDEX IF NOT EXISTS idx_edges_rel ON edges(relation_type);

-- Node Tags
CREATE TABLE IF NOT EXISTS tags (
    node_id TEXT,
    tag_id TEXT,
    tag_name TEXT,
    PRIMARY KEY (node_id, tag_id)
);

CREATE INDEX IF NOT EXISTS idx_tags_node ON tags(node_id);
CREATE INDEX IF NOT EXISTS idx_tags_name ON tags(tag_name);

-- Node Fields
CREATE TABLE IF NOT EXISTS fields (
    node_id TEXT,
    field_id TEXT,
    field_name TEXT,
    value_text TEXT,
    value_node_id TEXT,
    PRIMARY KEY (node_id, field_id, value_text, value_node_id)
);

CREATE INDEX IF NOT EXISTS idx_fields_node ON fields(node_id);

-- Traces for Retrieval Inspection
CREATE TABLE IF NOT EXISTS retrieval_traces (
    trace_id TEXT PRIMARY KEY,
    task TEXT,
    inferred_query TEXT,
    data_json TEXT,
    latency_ms REAL,
    created_at TEXT
);

-- System Sync Metadata
CREATE TABLE IF NOT EXISTS sync_metadata (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT
);
