import { D1Database } from '@cloudflare/workers-types';
import { TanaNode, NodeTag, NodeField } from '../types';

export class D1GraphStore {
  constructor(private db: D1Database) {}

  /**
   * Batch hydrate TanaNodes from D1 rows, avoiding N+1 queries.
   */
  async getNodesByIds(ids: string[]): Promise<TanaNode[]> {
    if (!ids || ids.length === 0) return [];
    
    // Filter out empties and duplicates
    const uniqueIds = Array.from(new Set(ids.filter(Boolean)));
    if (uniqueIds.length === 0) return [];

    const placeholders = uniqueIds.map(() => '?').join(',');

    // 1. Fetch base node rows
    const { results: nodeRows } = await this.db
      .prepare(`SELECT * FROM nodes WHERE id IN (${placeholders}) AND in_trash = 0`)
      .bind(...uniqueIds)
      .all<any>();

    if (!nodeRows || nodeRows.length === 0) return [];

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

    // 4. Fetch edges in batch (parent_child + reference)
    const { results: edgeRows } = await this.db
      .prepare(`SELECT source_id, target_id, relation_type FROM edges WHERE source_id IN (${foundPlaceholders}) OR target_id IN (${foundPlaceholders})`)
      .bind(...foundIds, ...foundIds)
      .all<any>();

    const childrenByNode = new Map<string, Set<string>>();
    const refsByNode = new Map<string, Set<string>>();
    const backlinksByNode = new Map<string, Set<string>>();

    const foundIdSet = new Set(foundIds);
    for (const e of edgeRows || []) {
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
      if (relation_type === 'reference' && foundIdSet.has(target_id)) {
        const s = backlinksByNode.get(target_id) || new Set();
        s.add(source_id);
        backlinksByNode.set(target_id, s);
      }
    }

    // Assemble nodes
    return nodeRows.map((r: any) => ({
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
    }));
  }

  /**
   * Expands 1-hop context around semantic seed nodes.
   */
  async expandSubgraph(seedNodes: TanaNode[], maxExpansion: number = 20): Promise<Array<{ node: TanaNode; reason: string }>> {
    const seenIds = new Set<string>(seedNodes.map(s => s.id));
    const result: Array<{ node: TanaNode; reason: string }> = [];

    // Add seeds
    for (const seed of seedNodes) {
      result.push({ node: seed, reason: 'Primary semantic seed match' });
    }

    // Collect 1-hop neighbour candidate IDs
    const parentIds = seedNodes.map(s => s.parent_id).filter((id): id is string => Boolean(id && !seenIds.has(id)));
    const immediateChildIds: string[] = [];
    const refIds: string[] = [];
    const backlinkIds: string[] = [];

    for (const seed of seedNodes) {
      for (const cid of seed.children.slice(0, 3)) {
        if (!seenIds.has(cid)) immediateChildIds.push(cid);
      }
      for (const rid of seed.references.slice(0, 3)) {
        if (!seenIds.has(rid)) refIds.push(rid);
      }
      for (const bid of seed.backlinks.slice(0, 3)) {
        if (!seenIds.has(bid)) backlinkIds.push(bid);
      }
    }

    const allNeighborIds = Array.from(new Set([...parentIds, ...immediateChildIds, ...refIds, ...backlinkIds])).slice(0, maxExpansion);
    const neighborNodes = await this.getNodesByIds(allNeighborIds);
    const neighborMap = new Map<string, TanaNode>(neighborNodes.map(n => [n.id, n]));

    for (const seed of seedNodes) {
      if (result.length >= maxExpansion + seedNodes.length) break;

      if (seed.parent_id && neighborMap.has(seed.parent_id) && !seenIds.has(seed.parent_id)) {
        const p = neighborMap.get(seed.parent_id)!;
        seenIds.add(p.id);
        result.push({ node: p, reason: `Parent container of '${seed.name.slice(0, 30)}'` });
      }

      for (const cid of seed.children) {
        if (neighborMap.has(cid) && !seenIds.has(cid)) {
          const c = neighborMap.get(cid)!;
          seenIds.add(c.id);
          result.push({ node: c, reason: `Child sub-item of '${seed.name.slice(0, 30)}'` });
          if (result.length >= maxExpansion + seedNodes.length) break;
        }
      }

      for (const rid of seed.references) {
        if (neighborMap.has(rid) && !seenIds.has(rid)) {
          const r = neighborMap.get(rid)!;
          seenIds.add(r.id);
          result.push({ node: r, reason: `Explicitly referenced by '${seed.name.slice(0, 30)}'` });
          if (result.length >= maxExpansion + seedNodes.length) break;
        }
      }
    }

    return result;
  }
}
