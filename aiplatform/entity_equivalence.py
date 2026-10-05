"""Conservative company equivalence shared by build and search callers.

Read views preserve all triples. Inference is limited to the two public company
snapshots with matching name, region, revenue and employees and unique candidates.
No scholar, publication or institution is merged by this module.
"""
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from rdflib import RDF
from aiplatform.core import ONTO, RES, RES_NS, OWL


def _fingerprint(graph, entity):
    values = []
    for predicate in (ONTO.name, ONTO.region, ONTO.revenue, ONTO.employees):
        raw = set(graph.objects(entity, predicate))
        if len(raw) != 1:
            return None
        text = str(next(iter(raw))).strip()
        if not text:
            return None
        if predicate in (ONTO.revenue, ONTO.employees):
            try:
                text = Decimal(text)
                if not text.is_finite() or text < 0:
                    return None
            except InvalidOperation:
                return None
        values.append(text)
    return tuple(values)


def inferred_company_pairs(graph):
    """Yield only unique, evidenced pairs between the two known public sources."""
    buckets = defaultdict(lambda: defaultdict(list))
    sources = ((RES['ds_ds_companies'], 'c_C'), (RES['ds_ds_companies_v2'], 'c2_V2C'))
    for entity in graph.subjects(RDF.type, ONTO.Company):
        fingerprint = _fingerprint(graph, entity)
        if fingerprint is None:
            continue
        for source, prefix in sources:
            if (entity, ONTO.sourcedFrom, source) in graph and str(entity).startswith(RES_NS + prefix):
                buckets[fingerprint][source].append(entity)
    for groups in buckets.values():
        left, right = (groups[source] for source, _ in sources)
        if len(left) == len(right) == 1:
            yield left[0], right[0]


class CompanyEquivalence:
    """Fresh deterministic read view. Recreate after any graph mutation/reload."""
    def __init__(self, graph):
        self.graph = graph
        companies = set(graph.subjects(RDF.type, ONTO.Company))
        parent = {entity: entity for entity in companies}
        def root(entity):
            while parent[entity] != entity:
                parent[entity] = parent[parent[entity]]
                entity = parent[entity]
            return entity
        def union(left, right):
            if left in companies and right in companies:
                a, b = root(left), root(right)
                if a != b:
                    first, second = sorted((a, b), key=str)
                    parent[second] = first
        for predicate in (ONTO.sameAs, OWL.sameAs):
            for left, right in graph.subject_objects(predicate):
                union(left, right)
        for left, right in inferred_company_pairs(graph):
            union(left, right)
        self.groups = defaultdict(set)
        self.canonical = {}
        for entity in companies:
            canonical = root(entity)
            self.canonical[entity] = canonical
            self.groups[canonical].add(entity)

    def representative(self, entity):
        return self.canonical.get(entity, entity)

    def aliases(self, entity):
        return self.groups.get(self.representative(entity), {entity})

    def predicate_objects(self, entity):
        return sorted({pair for alias in self.aliases(entity)
                       for pair in self.graph.predicate_objects(alias)}, key=lambda pair: (str(pair[0]), str(pair[1])))


def link_company_equivalences(graph):
    """B's build hook: add evidenced ONTO.sameAs edges; never delete triples.

    Returns the number of newly added links. Call after public data restoration.
    C's search callers should use CompanyEquivalence for merged alias traversal.
    """
    added = 0
    for left, right in inferred_company_pairs(graph):
        if (left, ONTO.sameAs, right) not in graph and (right, ONTO.sameAs, left) not in graph:
            graph.add((left, ONTO.sameAs, right))
            added += 1
    return added
