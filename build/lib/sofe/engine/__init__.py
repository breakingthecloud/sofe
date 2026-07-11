from __future__ import annotations
"""Evaluation engine — applies policies against resources."""

import uuid
from datetime import datetime
from ..models import Policy, Resource, Finding, Operator, Severity
from .architecture import ArchitectureContext


def evaluate(policies: list[Policy], resources: list[Resource], context: ArchitectureContext = None) -> list[Finding]:
    """Evaluate all policies against all resources. Returns findings (violations).
    
    If context is provided, architecture-aware policies can access related resources.
    """
    findings: list[Finding] = []

    # Build context if not provided (auto-infer relationships)
    if context is None:
        context = ArchitectureContext.from_resources(resources)

    for policy in policies:
        matching = _filter_by_scope(resources, policy)
        for resource in matching:
            violation = _check_rule(resource, policy, context)
            if violation:
                findings.append(violation)

    # Add architecture insights as findings
    _add_architecture_findings(context, findings)

    return findings


def evaluate_architecture(policies: list[Policy], resources: list[Resource]) -> dict:
    """Evaluate with full architecture analysis. Returns findings + insights."""
    context = ArchitectureContext.from_resources(resources)
    findings = evaluate(policies, resources, context)

    # Compute architecture insights
    insights = {
        "blast_radius": {},
        "single_points_of_failure": context.single_points_of_failure(threshold=3),
        "team_costs": {},
        "relationships_count": len(context.relationships),
    }

    # Blast radius for resources with findings
    resource_ids_with_findings = set(f.resource_id for f in findings)
    for rid in resource_ids_with_findings:
        affected = context.blast_radius(rid)
        if affected:
            insights["blast_radius"][rid] = {
                "affected_count": len(affected),
                "affected_resources": affected[:10],  # Cap at 10
                "cost_impact": context.cost_chain(rid),
            }

    # Team costs
    owners = set()
    for r in resources:
        owner = r.tags.get("owner", r.tags.get("Owner", ""))
        if owner:
            owners.add(owner)
    for owner in owners:
        insights["team_costs"][owner] = context.team_cost(owner)

    return {"findings": findings, "insights": insights, "context": context}


def _filter_by_scope(resources: list[Resource], policy: Policy) -> list[Resource]:
    """Filter resources by policy scope."""
    scope = policy.spec.scope
    result = []

    for r in resources:
        # Check resource type
        if not _matches_list(r.resource_type, scope.resource_types):
            continue
        # Check region
        if not _matches_list(r.region, scope.regions):
            continue
        # Check account
        if not _matches_list(r.account_id, scope.accounts):
            continue
        # Check environment tag
        if scope.environments != ["*"]:
            env = r.tags.get("Environment", r.tags.get("env", ""))
            if not _matches_list(env, scope.environments):
                continue
        # Check exclude
        if scope.exclude:
            exclude_tags = scope.exclude.get("tags", {})
            if any(r.tags.get(k) == v for k, v in exclude_tags.items()):
                continue
            if r.resource_id in scope.exclude.get("resource_ids", []):
                continue

        result.append(r)

    return result


def _check_rule(resource: Resource, policy: Policy, context: ArchitectureContext = None) -> Finding | None:
    """Check if a resource violates the policy rule. Uses context for cross-resource checks."""
    rule = policy.spec.rule
    metric_value = resource.metrics.get(rule.metric)

    if metric_value is None:
        return None  # No data for this metric — skip

    # Apply operator
    violated = _apply_operator(metric_value, rule.operator, rule.threshold)
    if not violated:
        return None

    # Check additional conditions (AND logic)
    for cond in rule.additional_conditions:
        cond_value = resource.metrics.get(cond.field) or resource.properties.get(cond.field)
        if cond_value is None:
            return None  # Missing data — skip
        if not _apply_operator(float(cond_value), cond.operator, float(cond.value)):
            return None  # Condition not met

    # Check cross-resource context conditions (if policy has context spec)
    if hasattr(policy.spec, 'context') and policy.spec.context and context:
        ctx_spec = policy.spec.context
        # Check requires_relationship
        if ctx_spec.get("requires_relationship"):
            rel_type = ctx_spec["requires_relationship"]
            related = context.get_related(resource.resource_id, rel_type=rel_type)
            if not related:
                return None  # Relationship doesn't exist — skip

            # Check related_check (condition on related resource)
            if ctx_spec.get("related_check"):
                check = ctx_spec["related_check"]
                check_met = False
                for rel_resource in related:
                    if check.get("resource_type") and rel_resource.resource_type != check["resource_type"]:
                        continue
                    rel_metric = rel_resource.metrics.get(check.get("metric", ""))
                    if rel_metric is not None:
                        if _apply_operator(rel_metric, Operator(check["operator"]), float(check["value"])):
                            check_met = True
                            break
                if not check_met:
                    return None  # Related check not met

    # Build finding
    recommendation = None
    estimated_savings = None
    for action in policy.spec.actions:
        if action.type == "recommend" and action.suggestion:
            recommendation = action.suggestion
        if action.estimated_savings == "calc":
            estimated_savings = resource.metrics.get("monthly_cost")

    # Enrich with blast radius if context available
    blast_info = ""
    if context:
        affected = context.blast_radius(resource.resource_id)
        if affected:
            blast_info = f" [blast radius: {len(affected)} resources]"

    return Finding(
        id=str(uuid.uuid4())[:8],
        policy_name=policy.metadata.name,
        severity=policy.spec.severity,
        resource_id=resource.resource_id,
        resource_type=resource.resource_type,
        region=resource.region,
        account_id=resource.account_id,
        message=f"{rule.metric} = {metric_value} (threshold: {rule.operator.value}{rule.threshold}){blast_info}",
        metric_name=rule.metric,
        metric_value=metric_value,
        threshold=rule.threshold,
        estimated_savings=estimated_savings,
        recommendation=recommendation,
        remediation_eligible=policy.spec.remediation.auto_eligible if policy.spec.remediation else False,
        timestamp=datetime.utcnow(),
    )


def _add_architecture_findings(context: ArchitectureContext, findings: list[Finding]):
    """Add findings for architectural issues (single points of failure, etc.)."""
    # Single points of failure
    spofs = context.single_points_of_failure(threshold=3)
    for rid in spofs:
        resource = context.resources.get(rid)
        if not resource:
            continue
        fan = context.fan_in(rid)
        findings.append(Finding(
            id=str(uuid.uuid4())[:8],
            policy_name="architecture-single-point-of-failure",
            severity=Severity.medium,
            resource_id=rid,
            resource_type=resource.resource_type,
            region=resource.region,
            account_id=resource.account_id,
            message=f"Single point of failure: {fan} resources depend on this (fan-in >= 3)",
            metric_name="fan_in",
            metric_value=float(fan),
            threshold=3.0,
            estimated_savings=None,
            recommendation="Consider redundancy or multi-AZ for this critical resource",
            remediation_eligible=False,
            timestamp=datetime.utcnow(),
        ))


def _apply_operator(value: float, operator: Operator, threshold: float) -> bool:
    if operator == Operator.lt: return value < threshold
    elif operator == Operator.gt: return value > threshold
    elif operator == Operator.lte: return value <= threshold
    elif operator == Operator.gte: return value >= threshold
    elif operator == Operator.eq: return value == threshold
    elif operator == Operator.ne: return value != threshold
    return False


def _matches_list(value: str, allowed: list[str]) -> bool:
    if "*" in allowed:
        return True
    return value in allowed
