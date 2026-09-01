ALTER TABLE nodes ADD COLUMN effective_date TEXT;
ALTER TABLE nodes ADD COLUMN date_source TEXT;
ALTER TABLE nodes ADD COLUMN date_source_node_id TEXT;
ALTER TABLE nodes ADD COLUMN calendar_distance INTEGER;
ALTER TABLE nodes ADD COLUMN calendar_path TEXT;
ALTER TABLE nodes ADD COLUMN ancestor_day_node_id TEXT;
ALTER TABLE nodes ADD COLUMN ancestor_week_node_id TEXT;
ALTER TABLE nodes ADD COLUMN ancestor_month_node_id TEXT;
ALTER TABLE nodes ADD COLUMN ancestor_year_node_id TEXT;

CREATE INDEX IF NOT EXISTS idx_nodes_effective_date ON nodes(effective_date);
CREATE INDEX IF NOT EXISTS idx_nodes_ancestor_day ON nodes(ancestor_day_node_id);
CREATE INDEX IF NOT EXISTS idx_nodes_ancestor_year ON nodes(ancestor_year_node_id);
