"""Architecture context — cross-resource relationship graph for policy evaluation.

ByaML-006 Fase A / S053 §7: el motor de análisis (blast_radius / cost_chain / team_cost /
single_points_of_failure) delega en **nan-graph** (motor OSS publicado, PyPI). Este módulo mantiene
la API de `ArchitectureContext` (resources/relationships/get_related) que consumidores SOFE ya usan;
los algoritmos de traversal los resuelve nan-graph (traverse/blast/cost/SPOF). Elimina el código
duplicado de algoritmos.
"""

from __future__ import annotations
from dataclasses import dataclass, field

from nangraph import NanGraph
from nangraph.types import GraphNode

from ..models import Resource


@dataclass
class Relationship:
    """A relationship between two resources."""
    from_id: str
    to_id: str
    rel_type: str  # routes_to, reads_writes, triggers, depends_on, protects


@dataclass
class ArchitectureContext:
    """Graph of resources and relationships for architecture-aware evaluation.

    Almacena resources + relationships (fuente de verdad de SOFE) y construye un `NanGraph`
    interno (lazy) para resolver las métricas de arquitectura (blast radius, cost chain, SPOF).
    """

    resources: dict[str, Resource] = field(default_factory=dict)
    relationships: list[Relationship] = field(default_factory=list)
    _adjacency: dict[str, list[tuple[str, str]]] = field(default_factory=dict)  # id -> [(target_id, rel_type)]
    _reverse: dict[str, list[tuple[str, str]]] = field(default_factory=dict)  # id -> [(source_id, rel_type)]
    _nan: NanGraph = field(default=None, repr=False)

    def add_resource(self, resource: Resource):
        self.resources[resource.resource_id] = resource
        self._nan = None  # invalidar cache

    def add_relationship(self, from_id: str, to_id: str, rel_type: str):
        self.relationships.append(Relationship(from_id, to_id, rel_type))
        if from_id not in self._adjacency:
            self._adjacency[from_id] = []
        self._adjacency[from_id].append((to_id, rel_type))
        if to_id not in self._reverse:
            self._reverse[to_id] = []
        self._reverse[to_id].append((from_id, rel_type))
        self._nan = None  # invalidar cache

    # ── NanGraph interno (lazy) ──────────────────────────────────────────────
    def _nan_graph(self) -> NanGraph:
        """Construye (cacheado) el NanGraph desde resources + relationships."""
        if self._nan is not None:
            return self._nan
        g = NanGraph()
        for r in self.resources.values():
            node = GraphNode(
                id=r.resource_id,
                type=r.resource_type,
                label=getattr(r, "name", None),
                attrs={
                    "monthly_cost": r.metrics.get("monthly_cost", 0) or 0,
                    "owner": r.tags.get("owner") or r.tags.get("Owner"),
                },
            )
            g.add_node(node)
        for rel in self.relationships:
            g.add_edge(from_id=rel.from_id, to_id=rel.to_id, rel_type=rel.rel_type)
        self._nan = g
        return g

    # ── API pública (sin cambios para consumidores) ─────────────────────────
    def get_related(self, resource_id: str, rel_type: str = None, direction: str = "outgoing") -> list[Resource]:
        """Get resources related to the given resource.
        
        direction: 'outgoing' (this -> others), 'incoming' (others -> this), 'both'
        """
        result = []
        if direction in ("outgoing", "both"):
            for target_id, rtype in self._adjacency.get(resource_id, []):
                if rel_type is None or rtype == rel_type:
                    if target_id in self.resources:
                        result.append(self.resources[target_id])
        if direction in ("incoming", "both"):
            for source_id, rtype in self._reverse.get(resource_id, []):
                if rel_type is None or rtype == rel_type:
                    if source_id in self.resources:
                        result.append(self.resources[source_id])
        return result

    # ── Métricas delegadas a nan-graph ───────────────────────────────────────
    def blast_radius(self, resource_id: str) -> list[str]:
        """Calculate blast radius — all resources affected if this one fails (BFS)."""
        from nangraph import blast_radius as nan_blast

        return nan_blast(self._nan_graph(), resource_id)

    def cost_chain(self, resource_id: str) -> float:
        """Sum monthly_cost of all downstream resources (BFS forward)."""
        from nangraph import cost_chain as nan_cost_chain

        return nan_cost_chain(self._nan_graph(), resource_id)

    def team_cost(self, owner: str) -> float:
        """Sum monthly_cost for all resources owned by a team/person."""
        total = 0.0
        for r in self.resources.values():
            r_owner = r.tags.get("owner", r.tags.get("Owner", ""))
            if r_owner == owner:
                total += r.metrics.get("monthly_cost", 0)
        return round(total, 2)

    def fan_in(self, resource_id: str) -> int:
        """Count how many resources depend on this one (incoming edges)."""
        from nangraph import fan_in as nan_fan_in

        return nan_fan_in(self._nan_graph(), resource_id)

    def single_points_of_failure(self, threshold: int = 3) -> list[str]:
        """Resources with high fan-in (many things depend on them)."""
        from nangraph import single_points_of_failure as nan_spof

        return nan_spof(self._nan_graph(), threshold)

    @classmethod
    def from_resources(cls, resources: list[Resource]) -> "ArchitectureContext":
        """Build context from resources, inferring relationships from common patterns."""
        ctx = cls()
        for r in resources:
            ctx.add_resource(r)

        # Infer relationships from resource properties
        _infer_relationships(ctx, resources)
        return ctx


def _infer_relationships(ctx: ArchitectureContext, resources: list[Resource]):
    """Infer relationships between resources based on common AWS patterns."""
    by_type: dict[str, list[Resource]] = {}
    for r in resources:
        by_type.setdefault(r.resource_type, []).append(r)

    # API Gateway → Lambda (routes_to)
    for apigw in by_type.get("aws.apigateway", []):
        for lam in by_type.get("aws.lambda", []):
            if lam.region == apigw.region:
                ctx.add_relationship(apigw.resource_id, lam.resource_id, "routes_to")

    # CloudFront → ALB/S3 (routes_to)
    for cf in by_type.get("aws.cloudfront", []):
        for alb in by_type.get("aws.elb", []):
            ctx.add_relationship(cf.resource_id, alb.resource_id, "routes_to")
        for s3 in by_type.get("aws.s3", []):
            ctx.add_relationship(cf.resource_id, s3.resource_id, "routes_to")

    # Lambda → DynamoDB/RDS/S3 (reads_writes)
    for lam in by_type.get("aws.lambda", []):
        for ddb in by_type.get("aws.dynamodb", []):
            if ddb.region == lam.region:
                ctx.add_relationship(lam.resource_id, ddb.resource_id, "reads_writes")
        for rds in by_type.get("aws.rds", []):
            if rds.region == lam.region:
                ctx.add_relationship(lam.resource_id, rds.resource_id, "reads_writes")
        for s3 in by_type.get("aws.s3", []):
            ctx.add_relationship(lam.resource_id, s3.resource_id, "reads_writes")

    # ECS/EKS → RDS/DynamoDB (reads_writes)
    for comp in by_type.get("aws.ecs", []) + by_type.get("aws.eks", []):
        for db in by_type.get("aws.rds", []) + by_type.get("aws.dynamodb", []):
            if db.region == comp.region:
                ctx.add_relationship(comp.resource_id, db.resource_id, "reads_writes")

    # NAT Gateway ← EC2/Lambda (depends_on)
    for nat in by_type.get("aws.natgateway", []):
        for ec2 in by_type.get("aws.ec2", []):
            if ec2.region == nat.region:
                ctx.add_relationship(ec2.resource_id, nat.resource_id, "depends_on")