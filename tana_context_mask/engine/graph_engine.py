import re
from typing import List, Dict, Set, Optional, Tuple, Any
from ..storage.db import SQLiteStore
from ..models.node import TanaNode

class GraphEngine:
    def __init__(self, db: Optional[SQLiteStore] = None):
        self.db = db or SQLiteStore()

    def expand_subgraph(
        self,
        seed_nodes: List[TanaNode],
        max_hops: int = 1,
        max_expansion_nodes: int = 20
    ) -> List[Tuple[TanaNode, str]]:
        """
        Expands around promising seed nodes through Tana's knowledge graph.
        Traverses hierarchy (parents/children), references, and backlinks.
        Returns a list of (TanaNode, inclusion_reason).
        """
        seen_ids: Set[str] = set()
        expanded_list: List[Tuple[TanaNode, str]] = []

        # 1. Add all seed nodes first
        for node in seed_nodes:
            if node.id not in seen_ids:
                seen_ids.add(node.id)
                reason = "Primary semantic seed match"
                expanded_list.append((node, reason))

        # 2. Expand 1-hop neighbours for seeds
        if max_hops >= 1:
            # Pre-fetch all parents for seed nodes in batch
            parent_ids = list(set([s.parent_id for s in seed_nodes if s.parent_id and s.parent_id not in seen_ids]))
            parent_nodes_map = {n.id: n for n in self.db.get_nodes(parent_ids)} if parent_ids else {}

            for seed in seed_nodes:
                if len(expanded_list) >= max_expansion_nodes + len(seed_nodes):
                    break

                # A. Parent context (crucial for short or ambiguous nodes)
                if seed.parent_id and seed.parent_id not in seen_ids:
                    parent_node = parent_nodes_map.get(seed.parent_id)
                    if parent_node and not parent_node.in_trash and parent_node.name:
                        seen_ids.add(parent_node.id)
                        clean_name = re.sub(r'<[^>]+>', '', parent_node.name).strip()
                        reason = f"Parent container of '{seed.name[:30]}'"
                        expanded_list.append((parent_node, reason))

                # B. Immediate Children (sub-bullets/tasks)
                children = self.db.get_children(seed.id, limit=5)
                for child in children:
                    if child.id not in seen_ids and child.name and not child.in_trash:
                        seen_ids.add(child.id)
                        reason = f"Child sub-item of '{seed.name[:30]}'"
                        expanded_list.append((child, reason))
                        if len(expanded_list) >= max_expansion_nodes + len(seed_nodes):
                            break

                # C. References & Backlinks
                references = self.db.get_references(seed.id)
                for ref in references[:3]:
                    if ref.id not in seen_ids and ref.name and not ref.in_trash:
                        seen_ids.add(ref.id)
                        reason = f"Explicitly referenced by '{seed.name[:30]}'"
                        expanded_list.append((ref, reason))
                        if len(expanded_list) >= max_expansion_nodes + len(seed_nodes):
                            break

                backlinks = self.db.get_backlinks(seed.id)
                for bl in backlinks[:3]:
                    if bl.id not in seen_ids and bl.name and not bl.in_trash:
                        seen_ids.add(bl.id)
                        reason = f"Mentions '{seed.name[:30]}'"
                        expanded_list.append((bl, reason))
                        if len(expanded_list) >= max_expansion_nodes + len(seed_nodes):
                            break

                # D. Temporal Co-occurrence (Implicit Sibling Edge on Day/Week Pages)
                if seed.parent_id:
                    siblings = self.db.get_children(seed.parent_id, limit=3)
                    for sib in siblings:
                        if sib.id not in seen_ids and sib.id != seed.id and sib.name and not sib.in_trash:
                            seen_ids.add(sib.id)
                            reason = f"Candidate contributing evidence (temporal co-occurrence with '{seed.name[:25]}')"
                            expanded_list.append((sib, reason))
                            if len(expanded_list) >= max_expansion_nodes + len(seed_nodes):
                                break

        return expanded_list

    def get_full_node_context(self, node_id: str) -> Optional[Dict[str, Any]]:
        """
        Returns rich contextual information for a single node including its
        lineage, children, references, backlinks, tags, and fields.
        """
        node = self.db.get_node(node_id)
        if not node:
            return None

        parents = self.db.get_parents_chain(node_id, max_depth=6)
        children = self.db.get_children(node_id, limit=30)
        references = self.db.get_references(node_id)
        backlinks = self.db.get_backlinks(node_id)

        return {
            "node": node,
            "breadcrumbs": [p.name for p in reversed(parents)],
            "children": children,
            "references": references,
            "backlinks": backlinks,
            "supertags": [t.tag_name for t in node.supertags],
            "fields": {f.field_name: (f.value_text or f.value_node_id) for f in node.fields}
        }
