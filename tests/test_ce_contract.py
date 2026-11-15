"""Cost Explorer contract + recommendations behavior — S113.

Why this exists: the recommendations collector sent parameters the API does not accept
("COMPUTE", "1", "30", "SHARED", no RecommendationTarget) and swallowed the error, so
`/savings` was empty for months. `MagicMock` clients accept anything and botocore does
NOT check enum values client-side, so nothing noticed.

The ValidatingCE below checks every request the engine makes against the real botocore
service model: required members, unknown members, types AND enum values.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import boto3
import pytest
from botocore.exceptions import ClientError

from sofe.collectors.aws import recommendations as rec_mod
from sofe.collectors.aws.commitments import CommitmentsCollector
from sofe.collectors.aws.cost import CostCollector
from sofe.collectors.aws.recommendations import RecommendationsCollector

ACCT = "111122223333"
_MODEL_CLIENT = boto3.client("ce", region_name="us-east-1", aws_access_key_id="x", aws_secret_access_key="x")


# --------------------------------------------------------------------------- model validation
def violations(shape, value, path="") -> list[str]:
    """Everything wrong with `value` as an instance of the botocore `shape`."""
    t = shape.type_name
    if t == "structure":
        if not isinstance(value, dict):
            return [f"{path}: expected a structure, got {type(value).__name__}"]
        out = [f"{path}.{k}: unknown member" for k in value if k not in shape.members]
        out += [f"{path}.{k}: required member missing" for k in shape.required_members if k not in value]
        for k, v in value.items():
            if k in shape.members:
                out += violations(shape.members[k], v, f"{path}.{k}")
        return out
    if t == "list":
        if not isinstance(value, list):
            return [f"{path}: expected a list"]
        return [x for i, v in enumerate(value) for x in violations(shape.member, v, f"{path}[{i}]")]
    if t == "string":
        if not isinstance(value, str):
            return [f"{path}: expected a string, got {type(value).__name__}"]
        enum = getattr(shape, "enum", None)
        if enum and value not in enum:
            return [f"{path}: {value!r} is not one of {enum}"]
        return []
    if t == "boolean":
        return [] if isinstance(value, bool) else [f"{path}: expected a boolean"]
    return []  # integers, timestamps, maps: nothing to add for these calls


class ValidatingCE:
    """A Cost Explorer client that records each call and any way it violates the real API model."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.problems: list[str] = []

    def __getattr__(self, method):
        if method.startswith("_"):
            raise AttributeError(method)
        operation = _MODEL_CLIENT.meta.method_to_api_mapping.get(method)
        if operation is None:
            raise AttributeError(f"Cost Explorer has no operation {method!r}")

        def call(**kwargs):
            self.calls.append((operation, kwargs))
            shape = _MODEL_CLIENT.meta.service_model.operation_model(operation).input_shape
            self.problems += [f"{operation}{p}" for p in violations(shape, kwargs)]
            return {"ResultsByTime": []}  # a valid, empty answer for every operation used here

        return call


def session_with(ce) -> MagicMock:
    s = MagicMock(name="session")
    s.client.return_value = ce
    return s


def run_all_meta_collectors(ce) -> None:
    s = session_with(ce)
    CostCollector(session=s, region="us-east-1", account_id=ACCT).collect()
    CommitmentsCollector(session=s, region="us-east-1", account_id=ACCT).collect()
    RecommendationsCollector(session=s, region="us-east-1", account_id=ACCT, target_regions=["us-east-1", "us-west-2"]).collect()


# --------------------------------------------------------------------------- the contract
class TestEveryRequestIsValidForTheRealApi:
    def test_the_validator_catches_the_bugs_that_shipped(self):
        """If this stops failing on the old parameters, the contract test below proves nothing."""
        ce = ValidatingCE()
        ce.get_savings_plans_purchase_recommendation(
            SavingsPlansType="COMPUTE", LookbackPeriodInDays="30", TermInYears="1", PaymentOption="NO_UPFRONT", AccountScope="SHARED",
        )
        ce.get_rightsizing_recommendation(Service="AmazonEC2", Configuration={"BenefitsConsidered": True})

        text = " | ".join(ce.problems)
        for expected in ("'COMPUTE' is not one of", "'30' is not one of", "'1' is not one of", "'SHARED' is not one of", "RecommendationTarget: required member missing"):
            assert expected in text, expected

    def test_cost_commitments_and_recommendations_send_only_valid_requests(self):
        ce = ValidatingCE()

        run_all_meta_collectors(ce)

        assert ce.problems == []
        operations = {op for op, _ in ce.calls}
        assert operations >= {
            "GetCostAndUsageWithResources", "GetSavingsPlansUtilization", "GetSavingsPlansCoverage",
            "GetReservationUtilization", "GetSavingsPlansPurchaseRecommendation", "GetRightsizingRecommendation",
        }

    def test_both_savings_plan_types_and_every_region_are_requested(self):
        ce = ValidatingCE()
        run_all_meta_collectors(ce)

        sp = [kw for op, kw in ce.calls if op == "GetSavingsPlansPurchaseRecommendation"]
        assert sorted(kw["SavingsPlansType"] for kw in sp) == ["COMPUTE_SP", "EC2_INSTANCE_SP"]
        assert {kw["AccountScope"] for kw in sp} == {"LINKED"}  # one account, never the whole organization
        rs = [kw for op, kw in ce.calls if op == "GetRightsizingRecommendation"]
        assert sorted(kw["Filter"]["Dimensions"]["Values"][0] for kw in rs) == ["us-east-1", "us-west-2"]
        assert {kw["Configuration"]["RecommendationTarget"] for kw in rs} == {"SAME_INSTANCE_FAMILY"}


# --------------------------------------------------------------------------- behavior
def client_error(code, message="boom") -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": message}}, "op")


class FlakyCE:
    """Fails the operations listed in `fail` (operation name -> exception); answers the others."""

    def __init__(self, fail=None, purchase_details=None, rightsizing=None):
        self.fail = fail or {}
        self.purchase_details = purchase_details or []
        self.rightsizing = rightsizing or []
        self.calls = []

    def get_savings_plans_purchase_recommendation(self, **kw):
        self.calls.append(("sp", kw["SavingsPlansType"]))
        if "sp" in self.fail:
            raise self.fail["sp"]
        details = self.purchase_details
        if isinstance(details, dict):  # per Savings Plan type: real answers differ, and identical ones are de-duplicated
            details = details.get(kw["SavingsPlansType"], [])
        return {"SavingsPlansPurchaseRecommendation": {"SavingsPlansPurchaseRecommendationDetails": details}}

    def get_rightsizing_recommendation(self, **kw):
        self.calls.append(("rs", kw["Filter"]["Dimensions"]["Values"][0]))
        if "rs" in self.fail:
            raise self.fail["rs"]
        return {"RightsizingRecommendations": self.rightsizing}


def collector_for(ce, regions=("us-east-1",)) -> RecommendationsCollector:
    return RecommendationsCollector(session=session_with(ce), region="us-east-1", account_id=ACCT, target_regions=list(regions))


PURCHASE = {"EstimatedMonthlySavingsAmount": "150.0", "UpfrontCost": "0", "SavingsPlansDetails": {"InstanceFamily": "t3"}}


class TestFailuresAreVisible:
    def test_a_failure_is_recorded_with_its_code_and_logged(self, capsys):
        c = collector_for(FlakyCE(fail={"sp": client_error("ValidationException", "4 validation errors detected: role arn:aws:iam::999999999999:role/x")}))

        c.collect()

        assert ("savings-plans:COMPUTE_SP", "ValidationException") in c.get_errors()
        err = capsys.readouterr().err
        assert "ValidationException" in err
        assert "arn:" not in err and "999999999999" not in err  # AWS messages can embed ARNs

    def test_a_non_aws_exception_is_recorded_by_class(self):
        c = collector_for(FlakyCE(fail={"rs": RuntimeError("socket closed")}))

        c.collect()

        assert ("rightsizing:us-east-1", "RuntimeError") in c.get_errors()

    def test_not_enabled_is_logged_as_information_not_as_a_warning(self, capsys):
        collector_for(FlakyCE(fail={"sp": client_error("DataUnavailableException")})).collect()

        err = capsys.readouterr().err
        assert "ℹ️" in err and "⚠️" not in err.split("rightsizing")[0]

    def test_errors_are_reset_on_every_collect(self):
        ce = FlakyCE(fail={"sp": client_error("ValidationException")})
        c = collector_for(ce)
        c.collect()
        assert c.get_errors()

        ce.fail = {}
        c.collect()

        assert c.get_errors() == []


class TestSourcesAreIndependent:
    def test_a_failing_savings_plan_type_does_not_skip_the_other_nor_rightsizing(self):
        ce = FlakyCE(fail={"sp": client_error("ValidationException")})

        collector_for(ce).collect()

        assert [c for c in ce.calls if c[0] == "sp"] == [("sp", "COMPUTE_SP"), ("sp", "EC2_INSTANCE_SP")]  # was: stopped after the first
        assert ("rs", "us-east-1") in ce.calls

    def test_a_failing_region_does_not_skip_the_next_one(self):
        ce = FlakyCE(fail={"rs": client_error("DataUnavailableException")})

        collector_for(ce, regions=("us-east-1", "us-west-2")).collect()

        assert [c for c in ce.calls if c[0] == "rs"] == [("rs", "us-east-1"), ("rs", "us-west-2")]


class TestFindings:
    def test_purchase_findings_use_a_human_label_not_the_api_enum(self):
        c = collector_for(FlakyCE(purchase_details={
            "COMPUTE_SP": [PURCHASE],
            "EC2_INSTANCE_SP": [{**PURCHASE, "EstimatedMonthlySavingsAmount": "90.0"}],
        }))

        c.collect()

        messages = [f.message for f in c.get_findings()]
        assert any("Savings Plan Compute" in m for m in messages)
        assert any("Savings Plan EC2 Instance" in m for m in messages)
        assert not any("_SP" in m for m in messages)

    def test_policy_names_downstream_readers_rely_on_are_unchanged(self):
        rightsize = {
            "RightsizingType": "Modify", "CurrentInstance": {"InstanceName": "i-1", "ResourceId": "i-1"},
            "ModifyRecommendationDetail": {"TargetInstances": [{"EstimatedMonthlySavings": "40", "ResourceDetails": {"EC2ResourceDetails": {"InstanceType": "t3.small"}}}]},
        }
        c = collector_for(FlakyCE(purchase_details=[PURCHASE], rightsizing=[rightsize]))

        c.collect()

        assert {f.policy_name for f in c.get_findings()} == {"savings-plan-purchase", "rightsize-recommendation"}
