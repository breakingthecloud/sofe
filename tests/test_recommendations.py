"""Tests for RecommendationsCollector — S082."""

from unittest.mock import MagicMock

from sofe.collectors.aws.recommendations import RecommendationsCollector


def _mock_session(purchase=None, rightsize=None, raise_purchase=False, raise_rightsize=False):
    ce = MagicMock()
    if raise_purchase:
        ce.get_savings_plans_purchase_recommendation.side_effect = Exception("CE not enabled")
    else:
        ce.get_savings_plans_purchase_recommendation.return_value = {
            "SavingsPlansPurchaseRecommendation": {
                "SavingsPlansPurchaseRecommendationDetails": purchase or [],
            }
        }
    if raise_rightsize:
        ce.get_rightsizing_recommendation.side_effect = Exception("CE not enabled")
    else:
        ce.get_rightsizing_recommendation.return_value = {
            "RightsizingRecommendations": rightsize or [],
        }
    session = MagicMock()
    session.client.return_value = ce
    return session


def _purchase_detail(monthly=150.0, upfront=500.0):
    return {
        "EstimatedMonthlySavingsAmount": str(monthly),
        "UpfrontCost": str(upfront),
        "SavingsPlansDetails": {"InstanceFamily": "t3"},
    }


def _rightsize_rec(name="i-abc123", savings=80.0):
    return {
        "RightsizingType": "Modify",
        "CurrentInstance": {"InstanceName": name, "ResourceId": name},
        "ModifyRecommendationDetail": {
            "TargetInstances": [
                {
                    "EstimatedMonthlySavings": str(savings),
                    "ResourceDetails": {"EC2ResourceDetails": {"InstanceType": "t3.small"}},
                }
            ]
        },
    }


def test_purchase_happy_path():
    session = _mock_session(purchase=[_purchase_detail(monthly=150.0, upfront=500.0)])
    c = RecommendationsCollector(session=session, region="us-east-1", account_id="123", target_regions=["us-east-1"])
    c.collect()
    findings = c.get_findings()
    assert len(findings) == 1
    f = findings[0]
    assert f.policy_name == "savings-plan-purchase"
    assert f.severity.value == "high"  # >= 100
    assert f.estimated_savings == 150.0
    assert f.metric_name == "potential_savings"
    print("✓ purchase happy path")


def test_purchase_low_severity():
    session = _mock_session(purchase=[_purchase_detail(monthly=40.0, upfront=200.0)])
    c = RecommendationsCollector(session=session, region="us-east-1", account_id="123", target_regions=["us-east-1"])
    c.collect()
    assert c.get_findings()[0].severity.value == "medium"
    print("✓ purchase low severity")


def test_rightsize():
    session = _mock_session(rightsize=[_rightsize_rec(name="i-abc123", savings=120.0)])
    c = RecommendationsCollector(session=session, region="us-east-1", account_id="123", target_regions=["us-east-1"])
    c.collect()
    findings = c.get_findings()
    assert len(findings) == 1
    f = findings[0]
    assert f.policy_name == "rightsize-recommendation"
    assert f.resource_id == "i-abc123"
    assert f.severity.value == "high"  # 120 >= 100
    assert f.estimated_savings == 120.0
    print("✓ rightsize")


def test_rightsize_zero_savings_skipped():
    session = _mock_session(rightsize=[_rightsize_rec(name="i-xyz", savings=0.0)])
    c = RecommendationsCollector(session=session, region="us-east-1", account_id="123", target_regions=["us-east-1"])
    c.collect()
    assert c.get_findings() == []
    print("✓ rightsize zero skipped")


def test_dedupe():
    session = _mock_session(
        purchase=[_purchase_detail(monthly=150.0), _purchase_detail(monthly=150.0)],  # same → dedupe
        rightsize=[_rightsize_rec(name="i-a", savings=50.0)],
    )
    c = RecommendationsCollector(session=session, region="us-east-1", account_id="123", target_regions=["us-east-1"])
    c.collect()
    # 2 purchase (1 deduped) + 1 rightsize
    assert len(c.get_findings()) == 2
    print("✓ dedupe")


def test_fallback_no_ce():
    session = _mock_session(raise_purchase=True, raise_rightsize=True)
    c = RecommendationsCollector(session=session, region="us-east-1", account_id="123", target_regions=["us-east-1"])
    c.collect()
    assert c.get_findings() == []
    print("✓ fallback")


if __name__ == "__main__":
    test_purchase_happy_path()
    test_purchase_low_severity()
    test_rightsize()
    test_rightsize_zero_savings_skipped()
    test_dedupe()
    test_fallback_no_ce()
    print("All S082 tests passed.")