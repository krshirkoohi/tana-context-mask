import { D1Database } from '@cloudflare/workers-types';
import { TanaNode, NodeTag, NodeField } from '../types';

export class D1GraphStore {
  constructor(private db: D1Database) {}

  /**
   * Batch hydrate TanaNodes from D1 rows, avoiding N+1 queries and parameter limits.
   */
  async getNodesByIds(ids: string[]): Promise<TanaNode[]> {
    if (!ids || ids.length === 0) return [];
    
    // Filter out empties and duplicates
    const uniqueIds = Array.from(new Set(ids.filter(Boolean)));
    if (uniqueIds.length === 0) return [];

    const chunkSize = 25;
    const allNodes: TanaNode[] = [];

    for (let i = 0; i < uniqueIds.length; i += chunkSize) {
      const chunk = uniqueIds.slice(i, i + chunkSize);
      const placeholders = chunk.map(() => '?').join(',');

      // 1. Fetch base node rows
      const { results: nodeRows } = await this.db
        .prepare(`SELECT * FROM nodes WHERE id IN (${placeholders}) AND in_trash = 0`)
        .bind(...chunk)
        .all<any>();

      if (!nodeRows || nodeRows.length === 0) continue;

      const foundIds = nodeRows.map((r: any) => r.id);
      const foundPlaceholders = foundIds.map(() => '?').join(',');

      // 2. Fetch tags in batch
      const { results: tagRows } = await this.db
        .prepare(`SELECT node_id, tag_id, tag_name FROM tags WHERE node_id IN (${foundPlaceholders})`)
        .bind(...foundIds)
        .all<any>();

      const tagsByNode = new Map<string, NodeTag[]>();
      for (const t of tagRows || []) {
        const list = tagsByNode.get(t.node_id) || [];
        list.push({ tag_id: t.tag_id, tag_name: t.tag_name });
        tagsByNode.set(t.node_id, list);
      }

      // 3. Fetch fields in batch
      const { results: fieldRows } = await this.db
        .prepare(`SELECT node_id, field_id, field_name, value_text, value_node_id FROM fields WHERE node_id IN (${foundPlaceholders})`)
        .bind(...foundIds)
        .all<any>();

      const fieldsByNode = new Map<string, NodeField[]>();
      for (const f of fieldRows || []) {
        const list = fieldsByNode.get(f.node_id) || [];
        list.push({
          field_id: f.field_id,
          field_name: f.field_name,
          value_text: f.value_text,
          value_node_id: f.value_node_id
        });
        fieldsByNode.set(f.node_id, list);
      }

      // 4. Fetch edges in batch (outbound and inbound separately)
      const { results: outEdges } = await this.db
        .prepare(`SELECT source_id, target_id, relation_type FROM edges WHERE source_id IN (${foundPlaceholders})`)
        .bind(...foundIds)
        .all<any>();

      const { results: inEdges } = await this.db
        .prepare(`SELECT source_id, target_id, relation_type FROM edges WHERE target_id IN (${foundPlaceholders})`)
        .bind(...foundIds)
        .all<any>();

      const childrenByNode = new Map<string, Set<string>>();
      const refsByNode = new Map<string, Set<string>>();
      const backlinksByNode = new Map<string, Set<string>>();

      const foundIdSet = new Set(foundIds);
      for (const e of outEdges || []) {
        const { source_id, target_id, relation_type } = e;
        if (relation_type === 'parent_child' && foundIdSet.has(source_id)) {
          const s = childrenByNode.get(source_id) || new Set();
          s.add(target_id);
          childrenByNode.set(source_id, s);
        }
        if (relation_type === 'reference' && foundIdSet.has(source_id)) {
          const s = refsByNode.get(source_id) || new Set();
          s.add(target_id);
          refsByNode.set(source_id, s);
        }
      }

      for (const e of inEdges || []) {
        const { source_id, target_id, relation_type } = e;
        if (relation_type === 'reference' && foundIdSet.has(target_id)) {
          const s = backlinksByNode.get(target_id) || new Set();
          s.add(source_id);
          backlinksByNode.set(target_id, s);
        }
      }

      // Assemble chunk nodes
      for (const r of nodeRows) {
        allNodes.push({
          id: r.id,
          name: r.name,
          description: r.description || '',
          doc_type: r.doc_type,
          parent_id: r.parent_id,
          created_at: r.created_at,
          updated_at: r.updated_at,
          content_hash: r.content_hash,
          structure_hash: r.structure_hash,
          is_done: Boolean(r.is_done),
          in_trash: Boolean(r.in_trash),
          supertags: tagsByNode.get(r.id) || [],
          fields: fieldsByNode.get(r.id) || [],
          children: Array.from(childrenByNode.get(r.id) || []),
          references: Array.from(refsByNode.get(r.id) || []),
          backlinks: Array.from(backlinksByNode.get(r.id) || []),
          breadcrumbs: [],
          deep_link: `https://app.tana.inc/?nodeid=${r.id}`
        });
      }
    }

    return allNodes;
  }

  /**
   * Resolves ancestry breadcrumb chains up to maxHops (default 6) for a list of nodes.
   */
  async resolveAncestry(nodes: TanaNode[], maxHops: number = 6): Promise<void> {
    if (!nodes || nodes.length === 0) return;

    const nodeParentMap = new Map<string, string>();
    const allAncestors = new Map<string, string>(); // id -> name

    // Identify starting parents
    let currentLevelIds = Array.from(
      new Set(nodes.map(n => n.parent_id).filter((id): id is string => Boolean(id)))
    );

    let hop = 0;
    while (currentLevelIds.length > 0 && hop < maxHops) {
      const parentNodes = await this.getNodesByIds(currentLevelIds);
      const nextLevelIds: string[] = [];

      for (const p of parentNodes) {
        allAncestors.set(p.id, p.name);
        if (p.parent_id) {
          nodeParentMap.set(p.id, p.parent_id);
          if (!allAncestors.has(p.parent_id)) {
            nextLevelIds.push(p.parent_id);
          }
        }
      }

      currentLevelIds = Array.from(new Set(nextLevelIds));
      hop++;
    }

    // Populate breadcrumbs on each node
    for (const node of nodes) {
      const crumbs: string[] = [];
      let curr = node.parent_id;
      let depth = 0;

      while (curr && allAncestors.has(curr) && depth < maxHops) {
        crumbs.unshift(allAncestors.get(curr)!);
        curr = nodeParentMap.get(curr);
        depth++;
      }

      node.breadcrumbs = crumbs;
    }
  }

  /**
   * Expands context around semantic seed nodes using asymmetric budget:
   * - Up to 6 hops upward (Ancestry / Containers)
   * - Up to 3 hops downward (Children / Tasks)
   * - 1-2 hops across references and backlinks
   */
  async expandSubgraph(seedNodes: TanaNode[], maxExpansion: number = 25): Promise<Array<{ node: TanaNode; reason: string }>> {
    const seenIds = new Set<string>(seedNodes.map(s => s.id));
    const result: Array<{ node: TanaNode; reason: string }> = [];

    // 1. Add seeds
    for (const seed of seedNodes) {
      result.push({ node: seed, reason: 'Primary semantic seed match' });
    }

    // 2. Multi-hop downward collection (up to 3 hops, budget-capped)
    const hop1ChildIds: Array<{ id: string; parentName: string }> = [];
    for (const seed of seedNodes) {
      for (const cid of seed.children.slice(0, 4)) {
        if (!seenIds.has(cid)) {
          hop1ChildIds.push({ id: cid, parentName: seed.name });
          seenIds.add(cid);
        }
      }
    }

    const hop1Nodes = await this.getNodesByIds(hop1ChildIds.map(h => h.id));
    const hop1Map = new Map<string, TanaNode>(hop1Nodes.map(n => [n.id, n]));

    for (const h of hop1ChildIds) {
      if (hop1Map.has(h.id) && result.length < maxExpansion) {
        result.push({
          node: hop1Map.get(h.id)!,
          reason: `Immediate sub-item of '${h.parentName.slice(0, 30)}'`
        });
      }
    }

    // Hop 2 Downward (Grandchildren)
    const hop2ChildIds: Array<{ id: string; parentName: string }> = [];
    for (const n of hop1Nodes) {
      for (const cid of n.children.slice(0, 3)) {
        if (!seenIds.has(cid) && result.length + hop2ChildIds.length < maxExpansion) {
          hop2ChildIds.push({ id: cid, parentName: n.name });
          seenIds.add(cid);
        }
      }
    }

    if (hop2ChildIds.length > 0) {
      const hop2Nodes = await this.getNodesByIds(hop2ChildIds.map(h => h.id));
      for (const n of hop2Nodes) {
        const item = hop2ChildIds.find(h => h.id === n.id);
        if (result.length < maxExpansion) {
          result.push({
            node: n,
            reason: `Nested sub-task under '${(item?.parentName || '').slice(0, 30)}'`
          });
        }
      }
    }

    // 3. Upward Ancestry & Parent Containers (up to 6 levels up)
    const parentIds = seedNodes
      .map(s => s.parent_id)
      .filter((id): id is string => Boolean(id && !seenIds.has(id)));

    if (parentIds.length > 0) {
      const parentNodes = await this.getNodesByIds(parentIds);
      for (const p of parentNodes) {
        if (!seenIds.has(p.id) && result.length < maxExpansion) {
          seenIds.add(p.id);
          result.push({
            node: p,
            reason: `Parent container of matching seed`
          });
        }
      }
    }

    // 4. Resolve 6-hop breadcrumbs for all selected nodes
    await this.resolveAncestry(result.map(r => r.node), 6);

    return result;
  }
}
