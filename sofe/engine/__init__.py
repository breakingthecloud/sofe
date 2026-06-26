"""Evaluation engine — applies policies against resources."""

import uuid
from datetime import datetime
from ..models import Policy, Resource, Finding, Operator, Severity


def evaluate(policies: list[Policy], resources: list[Resource]) -> list[Finding]:
    """Evaluate all policies against all resources. Returns findings (violations)."""
    findings: list[Finding] = []

    for policy in policies:
        matching = _filter_by_scope(resources, policy)
        for resource in matching:
            violation = _check_rule(resource, policy)
            if violation:
                findings.append(violation)

    return findings


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


def _check_rule(resource: Resource, policy: Policy) -> Finding | None:
    """Check if a resource violates the policy rule."""
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

    # Build finding
    recommendation = None
    estimated_savings = None
    for action in policy.spec.actions:
        if action.type == "recommend" and action.suggestion:
            recommendation = action.suggestion
        if action.estimated_savings == "calc":
            estimated_savings = resource.metrics.get("monthly_cost")

    return Finding(
        id=str(uuid.uuid4())[:8],
        policy_name=policy.metadata.name,
        severity=policy.spec.severity,
        resource_id=resource.resource_id,
        resource_type=resource.resource_type,
        region=resource.region,
        account_id=resource.account_id,
        message=f"{rule.metric} = {metric_value} (threshold: {rule.operator.value}{rule.threshold})",
        metric_name=rule.metric,
        metric_value=metric_value,
        threshold=rule.threshold,
        estimated_savings=estimated_savings,
        recommendation=recommendation,
        remediation_eligible=policy.spec.remediation.auto_eligible if policy.spec.remediation else False,
        timestamp=datetime.utcnow(),
    )


def _apply_operator(value: float, operator: Operator, threshold: float) -> bool:
    match operator:
        case Operator.lt: return value < threshold
        case Operator.gt: return value > threshold
        case Operator.lte: return value <= threshold
        case Operator.gte: return value >= threshold
        case Operator.eq: return value == threshold
        case Operator.ne: return value != threshold
    return False


def _matches_list(value: str, allowed: list[str]) -> bool:
    if "*" in allowed:
        return True
    return value in allowed
