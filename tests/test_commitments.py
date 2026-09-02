"""Tests for CommitmentsCollector — S081."""

from unittest.mock import MagicMock, patch
from sofe.collectors.aws.commitments import CommitmentsCollector


def _mock_session(sp_util=None, sp_cov=None, ri_util=None, raise_sp=False, raise_cov=False, raise_ri=False):
    ce = MagicMock()
    if raise_sp:
        ce.get_savings_plans_utilization.side_effect = Exception("CE not enabled")
    else:
        ce.get_savings_plans_utilization.return_value = {
            "SavingsPlansUtilizationsByTime": [{"SavingsPlansUtilization": {"UtilizationPercentage": sp_util or 0}}]
        }
    if raise_cov:
        ce.get_savings_plans_coverage.side_effect = Exception("CE not enabled")
    else:
        ce.get_savings_plans_coverage.return_value = {
            "SavingsPlansCoverages": [{"SavingsPlansCoverage": {"CoveragePercentage": sp_cov or 0}}]
        }
    if raise_ri:
        ce.get_reserved_instance_utilization.side_effect = Exception("CE not enabled")
    else:
        ce.get_reserved_instance_utilization.return_value = {
            "UtilizationsByTime": [{"Utilization": {"UtilizationPercentage": ri_util or 0}}]
        }
    session = MagicMock()
    session.client.return_value = ce
    return session


def test_happy_path():
    session = _mock_session(sp_util=85.5, sp_cov=60.0, ri_util=92.0)
    c = CommitmentsCollector(session=session, region="us-east-1", account_id="123")
    result = c.collect()
    assert result == []
    metrics = c.get_metrics()
    assert metrics["sp_utilization_pct"] == 85.5
    assert metrics["sp_coverage_pct"] == 60.0
    assert metrics["ri_utilization_pct"] == 92.0
    print("✓ happy path")


def test_fallback_no_ce():
    session = _mock_session(raise_sp=True, raise_cov=True, raise_ri=True)
    c = CommitmentsCollector(session=session, region="us-east-1", account_id="123")
    c.collect()
    metrics = c.get_metrics()
    assert metrics["sp_utilization_pct"] is None
    assert metrics["sp_coverage_pct"] is None
    assert metrics["ri_utilization_pct"] is None
    print("✓ fallback")


def test_partial():
    session = _mock_session(sp_util=75.0, raise_cov=True, ri_util=80.0)
    c = CommitmentsCollector(session=session, region="us-east-1", account_id="123")
    c.collect()
    metrics = c.get_metrics()
    assert metrics["sp_utilization_pct"] == 75.0
    assert metrics["sp_coverage_pct"] is None
    assert metrics["ri_utilization_pct"] == 80.0
    print("✓ partial")


if __name__ == "__main__":
    test_happy_path()
    test_fallback_no_ce()
    test_partial()
    print("All S081 tests passed.")
