"""Tests for S083 savings policies (evaluated against the synthetic account resource)."""

from sofe.loader import load_policies
from sofe.engine import evaluate
from sofe.models import Resource


def _account(metrics: dict[str, float]) -> Resource:
    return Resource(
        resource_id="account-123",
        resource_type="aws.account",
        region="us-east-1",
        account_id="123",
        tags={},
        properties={},
        metrics=metrics,
    )


def _savings_policies():
    return [p for p in load_policies("policies/") if p.metadata.name in
            ("savings-plan-coverage-low", "ri-utilization-low", "sp-utilization-low")]


def test_low_coverage_fires():
    policies = _savings_policies()
    assert len(policies) == 3
    findings = evaluate(policies, [_account({"sp_coverage_pct": 60.0, "ri_utilization_pct": 95.0, "sp_utilization_pct": 80.0})])
    names = {f.policy_name for f in findings}
    assert "savings-plan-coverage-low" in names
    assert "ri-utilization-low" not in names  # ri=95 ok
    assert "sp-utilization-low" not in names  # sp=80 ok
    print("✓ low coverage fires")


def test_low_ri_and_sp_utilization_fire():
    policies = _savings_policies()
    findings = evaluate(policies, [_account({"sp_coverage_pct": 95.0, "ri_utilization_pct": 50.0, "sp_utilization_pct": 40.0})])
    names = {f.policy_name for f in findings}
    assert "ri-utilization-low" in names
    assert "sp-utilization-low" in names
    assert "savings-plan-coverage-low" not in names
    print("✓ low ri/sp utilization fire")


def test_healthy_account_no_findings():
    policies = _savings_policies()
    findings = evaluate(policies, [_account({"sp_coverage_pct": 95.0, "ri_utilization_pct": 90.0, "sp_utilization_pct": 85.0})])
    assert findings == []
    print("✓ healthy account no findings")


def test_missing_metrics_skip():
    policies = _savings_policies()
    findings = evaluate(policies, [_account({})])  # no sp/ri metrics -> skip
    assert findings == []
    print("✓ missing metrics skip")


if __name__ == "__main__":
    test_low_coverage_fires()
    test_low_ri_and_sp_utilization_fire()
    test_healthy_account_no_findings()
    test_missing_metrics_skip()
    print("All S083 policy tests passed.")