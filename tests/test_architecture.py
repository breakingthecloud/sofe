"""Tests del refactor ByaML-006 / S053: ArchitectureContext delega en nan-graph.

Cubre: blast_radius, cost_chain, team_cost, fan_in, single_points_of_failure delegados al
motor nan-graph (PyPI), manteniendo la API de SOFE.
"""

from __future__ import annotations

from sofe.engine.architecture import ArchitectureContext
from sofe.models import Resource


def _r(rid: str, rtype: str, region: str = "us-east-1", cost: float = 0.0, owner: str = "") -> Resource:
    return Resource(
        resource_id=rid,
        resource_type=rtype,
        region=region,
        account_id="acct-1",
        tags={"owner": owner} if owner else {},
        metrics={"monthly_cost": cost},
    )


def _ctx():
    resources = [
        _r("api-gw", "aws.apigateway", cost=20, owner="web"),
        _r("lambda-orders", "aws.lambda", cost=5, owner="web"),
        _r("ddb-orders", "aws.dynamodb", cost=30, owner="web"),
        _r("nat", "aws.natgateway", cost=8),
        _r("ec2-a", "aws.ec2", cost=12, owner="infra"),
    ]
    return ArchitectureContext.from_resources(resources)


def test_infers_relationships():
    ctx = _ctx()
    rels = {(x.from_id, x.to_id, x.rel_type) for x in ctx.relationships}
    assert ("api-gw", "lambda-orders", "routes_to") in rels
    assert ("lambda-orders", "ddb-orders", "reads_writes") in rels


def test_get_related_preserved():
    ctx = _ctx()
    related = [x.resource_id for x in ctx.get_related("api-gw")]
    assert related == ["lambda-orders"]


def test_blast_radius_nan_graph():
    ctx = _ctx()
    assert sorted(ctx.blast_radius("api-gw")) == ["ddb-orders", "lambda-orders"]
    assert ctx.blast_radius("ddb-orders") == []  # hoja


def test_cost_chain_nan_graph():
    ctx = _ctx()
    assert ctx.cost_chain("api-gw") == 55.0  # 20 + 5 + 30
    assert ctx.cost_chain("ddb-orders") == 30.0  # hoja


def test_team_cost():
    ctx = _ctx()
    assert ctx.team_cost("web") == 55.0  # 20 + 5 + 30
    assert ctx.team_cost("nobody") == 0.0


def test_fan_in_and_spof():
    ctx = _ctx()
    assert ctx.fan_in("ddb-orders") == 1
    spof = ctx.single_points_of_failure(1)
    assert "lambda-orders" in spof
    assert "ddb-orders" in spof


def test_mutation_invalidates_nan_cache():
    ctx = _ctx()
    # blast antes de la mutación
    assert sorted(ctx.blast_radius("api-gw")) == ["ddb-orders", "lambda-orders"]
    # nueva relación conecta ddb-orders → ec2-a — el cache debe reconstruirse y reflejar el cambio
    ctx.add_relationship("ddb-orders", "ec2-a", "depends_on")
    affected = ctx.blast_radius("api-gw")
    assert "ec2-a" in affected  # ahora el camino api→lambda→ddb→ec2-a existe