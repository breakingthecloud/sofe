"""S111 Phase B — cost integrity.

What these tests protect:
  * a resource only gets ``monthly_cost`` when Cost Explorer matched it (no equal split);
  * the evaluation says WHY it has no cost data (access_denied / not_enabled / ...), without
    leaking AWS error messages;
  * the Cost Explorer query is restricted to the evaluated account;
  * every evaluation makes exactly the documented number of Cost Explorer requests;
  * policies skip an unknown cost instead of evaluating an invented one.
"""

from __future__ import annotations

import collections
from datetime import datetime
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

import sofe.collectors as collectors
from sofe.collectors.aws import cost as cost_mod
from sofe.collectors.aws.cost import CostCollector, classify_ce_error
from sofe.engine import evaluate
from sofe.loader import load_policies
from sofe.models import Resource

ACCT = "111122223333"


# --------------------------------------------------------------------------- helpers
def ce_page(rows: dict[str, float], token: str | None = None) -> dict:
    """A get_cost_and_usage_with_resources response: {resource_id: 7-day cost}."""
    page = {
        "ResultsByTime": [
            {"Groups": [{"Keys": [rid], "Metrics": {"UnblendedCost": {"Amount": str(amount)}}} for rid, amount in rows.items()]}
        ]
    }
    if token:
        page["NextPageToken"] = token
    return page


def client_error(code: str, message: str = "boom") -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": message}}, "GetCostAndUsageWithResources")


class FakeCE:
    """Cost Explorer stand-in that records every request.

    Unknown API methods answer ``{}`` (so the commitments / recommendations collectors run
    through their normal code) and are still counted.
    """

    def __init__(self, pages=None, error: Exception | None = None):
        self.pages = list(pages or [ce_page({})])
        self.error = error
        self.calls: collections.Counter = collections.Counter()
        self.cost_kwargs: list[dict] = []

    def get_cost_and_usage_with_resources(self, **kwargs):
        self.calls["get_cost_and_usage_with_resources"] += 1
        self.cost_kwargs.append(kwargs)
        if self.error:
            raise self.error
        return self.pages.pop(0) if len(self.pages) > 1 else self.pages[0]

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        def call(**kwargs):
            self.calls[name] += 1
            return {}

        return call

    @property
    def total(self) -> int:
        return sum(self.calls.values())


def session_with(ce: FakeCE, account_id: str = ACCT, region: str = "us-east-1"):
    s = MagicMock(name="session")
    s.region_name = region

    def client(name, **kwargs):
        if name == "ce":
            return ce
        c = MagicMock(name=name)
        if name == "sts":
            c.get_caller_identity.return_value = {"Account": account_id}
        return c

    s.client.side_effect = client
    return s


def res(rid: str, rtype: str = "aws.lambda") -> Resource:
    return Resource(resource_id=rid, resource_type=rtype, region="us-east-1", account_id=ACCT)


def arn_secret(name: str) -> str:
    return f"arn:aws:secretsmanager:us-east-1:{ACCT}:secret:{name}-AbCdEf"


# 7-day amounts chosen so that monthly = amount * 30/7 gives round numbers
def week(monthly: float) -> float:
    return monthly * 7 / 30


# --------------------------------------------------------------------------- CostCollector
class TestQuery:
    def test_is_restricted_to_the_evaluated_account(self):
        ce = FakeCE()
        CostCollector(session=session_with(ce), region="us-east-1", account_id=ACCT).collect()

        flt = ce.cost_kwargs[0]["Filter"]
        assert {"Dimensions": {"Key": "LINKED_ACCOUNT", "Values": [ACCT]}} in flt["And"]

    def test_still_excludes_credits_refunds_and_tax(self):
        ce = FakeCE()
        CostCollector(session=session_with(ce), region="us-east-1", account_id=ACCT).collect()

        flt = ce.cost_kwargs[0]["Filter"]
        excluded = [x for x in flt["And"] if "Not" in x][0]["Not"]["Dimensions"]
        assert excluded == {"Key": "RECORD_TYPE", "Values": ["Credit", "Refund", "Tax"]}

    def test_pages_are_followed_and_summed(self):
        ce = FakeCE(pages=[ce_page({"fn-a": 1.0}, token="t1"), ce_page({"fn-a": 1.0, "fn-b": 2.0})])
        c = CostCollector(session=session_with(ce), region="us-east-1", account_id=ACCT)
        c.collect()

        assert ce.calls["get_cost_and_usage_with_resources"] == 2
        assert ce.cost_kwargs[1]["NextPageToken"] == "t1"
        assert c.request_count() == 2
        assert c.get_cost_map() == {"fn-a": round(2.0 * 30 / 7, 2), "fn-b": round(2.0 * 30 / 7, 2)}

    def test_a_pagination_loop_stops_with_an_error_instead_of_spinning(self):
        ce = FakeCE(pages=[ce_page({"fn-a": 1.0}, token="again")])  # always says "there is more"
        c = CostCollector(session=session_with(ce), region="us-east-1", account_id=ACCT)

        c.collect()

        assert c.get_status() == "error"
        assert ce.calls["get_cost_and_usage_with_resources"] == cost_mod.MAX_PAGES

    def test_seven_days_are_extrapolated_to_a_month_and_dust_is_dropped(self):
        ce = FakeCE(pages=[ce_page({"real": 7.0, "dust": 0.0001})])
        c = CostCollector(session=session_with(ce), region="us-east-1", account_id=ACCT)
        c.collect()

        assert c.get_cost_map() == {"real": 30.0}


class TestStatus:
    def test_data_received_is_ok(self):
        c = CostCollector(session=session_with(FakeCE(pages=[ce_page({"x": 7.0})])), region="us-east-1", account_id=ACCT)
        c.collect()
        assert c.get_status() == "ok" and c.get_detail() == ""

    def test_a_working_call_without_spend_is_no_data_not_an_error(self):
        c = CostCollector(session=session_with(FakeCE(pages=[ce_page({})])), region="us-east-1", account_id=ACCT)
        c.collect()
        assert c.get_status() == "no_data"
        assert c.get_cost_map() == {}

    def test_credits_only_account_is_no_data(self):
        c = CostCollector(session=session_with(FakeCE(pages=[ce_page({"x": -3.0})])), region="us-east-1", account_id=ACCT)
        c.collect()
        assert c.get_status() == "no_data"

    @pytest.mark.parametrize(
        "exc, status",
        [
            (client_error("AccessDeniedException", "User: arn:aws:sts::999999999999:assumed-role/r/s is not authorized to perform: ce:GetCostAndUsageWithResources on resource: arn:aws:ce:us-east-1:111122223333:/GetCostAndUsageWithResources"), "access_denied"),
            (client_error("AccessDeniedException", "User not enabled for cost explorer access"), "not_enabled"),
            (client_error("DataUnavailableException", "Data is not available"), "not_enabled"),
            (client_error("OptInRequired", "x"), "not_enabled"),
            (client_error("AccessDeniedException", "Rightsizing EC2 recommendation is an opt-in only feature. You can enable this feature from the PAYER account's Cost Explorer Preferences page."), "not_enabled"),
            (client_error("AccessDeniedException", "Resource-level data is not enabled for this account"), "not_enabled"),
            (client_error("ThrottlingException", "Rate exceeded"), "error"),
            (client_error("LimitExceededException", "x"), "error"),
            (RuntimeError("socket closed"), "error"),
        ],
    )
    def test_failures_are_classified(self, exc, status):
        c = CostCollector(session=session_with(FakeCE(error=exc)), region="us-east-1", account_id=ACCT)
        c.collect()
        assert c.get_status() == status
        assert c.get_cost_map() == {}

    def test_access_denied_names_the_missing_action_but_never_an_arn(self):
        exc = client_error(
            "AccessDeniedException",
            "User: arn:aws:sts::999999999999:assumed-role/r/s is not authorized to perform: ce:GetCostAndUsageWithResources on resource: arn:aws:ce:us-east-1:111122223333:/x",
        )
        status, detail = classify_ce_error(exc)

        assert status == "access_denied"
        assert detail == "AccessDeniedException: ce:GetCostAndUsageWithResources"
        assert "arn:" not in detail and "999999999999" not in detail

    @pytest.mark.parametrize("exc", [client_error("ThrottlingException", "arn:aws:iam::222233334444:role/secret-role"), RuntimeError("arn:aws:iam::222233334444:role/x")])
    def test_other_errors_never_leak_the_message(self, exc):
        _, detail = classify_ce_error(exc)
        assert "arn:" not in detail and "2222" not in detail


# --------------------------------------------------------------------------- enrichment
class TestEnrichCosts:
    def test_only_matched_resources_get_a_cost_and_values_differ_per_resource(self):
        ce = FakeCE(pages=[ce_page({arn_secret("db-pass"): week(10.0), arn_secret("api-key"): week(20.0)})])
        inventory = [res("db-pass", "aws.secretsmanager"), res("api-key", "aws.secretsmanager"), res("idle-fn")]

        summary = collectors._enrich_costs(session_with(ce), inventory, ACCT)

        assert inventory[0].metrics["monthly_cost"] == 10.0
        assert inventory[1].metrics["monthly_cost"] == 20.0
        assert "monthly_cost" not in inventory[2].metrics  # unknown is NOT zero and NOT a share of the total
        assert summary["cost_status"] == "ok"
        assert summary["cost_coverage"] == round(2 / 3, 4)
        assert summary["cost_coverage_usd"] == 1.0
        assert summary["cost_account_monthly"] == 30.0
        assert summary["cost_matched_monthly"] == 30.0

    def test_spend_on_things_sofe_does_not_inventory_makes_it_partial_and_is_reported(self):
        # the real case of account 916...: $0.80 matched, $26.12 on a WAF web ACL and an Amazon Q seat
        ce = FakeCE(pages=[ce_page({
            arn_secret("jwt"): week(0.4), arn_secret("mongo"): week(0.4),
            f"arn:aws:wafv2:us-east-1:{ACCT}:global/webacl/Front/abc": week(7.0),
            "arn:aws:identitystore:::user/xyz": week(19.0),
        })])
        inventory = [res("jwt", "aws.secretsmanager"), res("mongo", "aws.secretsmanager")] + [res(f"fn-{i}") for i in range(20)]

        summary = collectors._enrich_costs(session_with(ce), inventory, ACCT)

        assert summary["cost_status"] == "partial"
        assert summary["cost_account_monthly"] == 26.8
        assert summary["cost_matched_monthly"] == 0.8
        assert summary["cost_coverage_usd"] == round(0.8 / 26.8, 4)  # ~3%
        assert summary["cost_coverage"] == round(2 / 22, 4)
        assert summary["cost_detail"]  # says why
        assert sum(1 for r in inventory if "monthly_cost" in r.metrics) == 2

    def test_ok_needs_almost_all_the_spend_to_be_matched(self):
        ce = FakeCE(pages=[ce_page({arn_secret("a"): week(96.0), "arn:aws:identitystore:::user/x": week(4.0)})])  # 96% matched
        assert collectors._enrich_costs(session_with(ce), [res("a", "aws.secretsmanager")], ACCT)["cost_status"] == "ok"

        ce = FakeCE(pages=[ce_page({arn_secret("a"): week(90.0), "arn:aws:identitystore:::user/x": week(10.0)})])  # 90% matched
        assert collectors._enrich_costs(session_with(ce), [res("a", "aws.secretsmanager")], ACCT)["cost_status"] == "partial"

    def test_spend_but_nothing_matches_is_partial_with_zero_coverage_not_ok(self):
        ce = FakeCE(pages=[ce_page({"arn:aws:identitystore:::user/x": week(19.0)})])
        inventory = [res("fn-1")]

        summary = collectors._enrich_costs(session_with(ce), inventory, ACCT)

        assert summary["cost_status"] == "partial"
        assert summary["cost_coverage"] == 0.0 and summary["cost_coverage_usd"] == 0.0
        assert "monthly_cost" not in inventory[0].metrics

    @pytest.mark.parametrize(
        "exc, status",
        [
            (client_error("AccessDeniedException", "not authorized to perform: ce:GetCostAndUsageWithResources"), "access_denied"),
            (client_error("DataUnavailableException"), "not_enabled"),
            (client_error("ThrottlingException"), "error"),
        ],
    )
    def test_no_cost_data_means_no_cost_anywhere_and_the_reason_is_reported(self, exc, status):
        inventory = [res("a"), res("b")]

        summary = collectors._enrich_costs(session_with(FakeCE(error=exc)), inventory, ACCT)

        assert summary["cost_status"] == status
        assert summary["cost_detail"]
        assert summary["cost_coverage"] is None and summary["cost_coverage_usd"] is None
        assert summary["cost_account_monthly"] is None
        assert all("monthly_cost" not in r.metrics for r in inventory)

    def test_one_cost_id_matched_by_two_resources_never_reports_more_than_100_percent(self):
        # same function name in two regions: both inventory entries match the single Cost Explorer id
        ce = FakeCE(pages=[ce_page({"arn:aws:lambda:us-east-1:%s:function:shared" % ACCT: week(10.0)})])
        inventory = [res("shared"), res("shared")]

        summary = collectors._enrich_costs(session_with(ce), inventory, ACCT)

        assert summary["cost_coverage_usd"] == 1.0
        assert summary["cost_status"] == "ok"

    def test_no_spend_reports_zero_account_spend(self):
        summary = collectors._enrich_costs(session_with(FakeCE(pages=[ce_page({})])), [res("a")], ACCT)
        assert summary["cost_status"] == "no_data"
        assert summary["cost_account_monthly"] == 0.0

    def test_it_never_raises(self, monkeypatch):
        monkeypatch.setattr(CostCollector, "collect", lambda self: (_ for _ in ()).throw(RuntimeError("bug")))
        summary = collectors._enrich_costs(session_with(FakeCE()), [res("a")], ACCT)
        assert summary["cost_status"] == "error"


# --------------------------------------------------------------------------- collect_all
class _Lambdas:
    """One fake inventory collector (3 lambdas), so collect_all has resources to enrich."""

    instances = 0

    def __init__(self, session, region, account_id):
        self.region, self.account_id = region, account_id

    def collect(self):
        return [Resource(resource_id=f"fn-{i}", resource_type="aws.lambda", region=self.region, account_id=self.account_id) for i in range(3)]


class _Meta:
    """Would double the Cost Explorer requests if collect_all still ran it in the resource loop."""

    built = 0

    def __init__(self, session, region, account_id):
        _Meta.built += 1

    def collect(self):
        return []


@pytest.fixture
def engine(monkeypatch):
    _Meta.built = 0
    registry = {"aws.lambda": _Lambdas, "aws.cost": _Meta, "aws.commitments": _Meta}
    monkeypatch.setattr(collectors, "COLLECTORS", registry)
    monkeypatch.setattr(collectors, "ALL_TYPES", list(registry))
    return registry


class TestCollectAll:
    def test_meta_collectors_do_not_run_in_the_resource_loop(self, engine):
        collectors.collect_all(session=session_with(FakeCE()))
        assert _Meta.built == 0

    def test_meta_collectors_stay_registered_for_the_catalog(self):
        from sofe.collectors.aws import COLLECTORS, ALL_TYPES

        assert "aws.cost" in COLLECTORS and "aws.commitments" in ALL_TYPES

    def test_no_naive_split_even_when_the_account_total_is_known(self, engine):
        """The old fallback divided last month's account total ($100) among ALL resources ($33.33 each).

        The total call answers with a REAL payload here, so the old code would have used it
        (a raising stub would be swallowed by its bare ``except``, hiding the bug).
        """
        ce = FakeCE(pages=[ce_page({})])
        ce.get_cost_and_usage = lambda **kw: (ce.calls.update(["get_cost_and_usage"]) or {"ResultsByTime": [{"Total": {"UnblendedCost": {"Amount": "100.0"}}}]})

        out = collectors.collect_all(session=session_with(ce))

        assert ce.calls["get_cost_and_usage"] == 0
        assert all("monthly_cost" not in r.metrics for r in out)

    def test_cost_summary_travels_on_the_account_resource(self, engine):
        ce = FakeCE(pages=[ce_page({"arn:aws:lambda:us-east-1:%s:function:fn-0" % ACCT: week(12.0)})])

        out = collectors.collect_all(session=session_with(ce))

        account = [r for r in out if r.resource_type == "aws.account"][0]
        assert account.properties["cost_status"] == "ok"  # all of the account's spend is matched
        assert account.properties["cost_coverage"] == round(1 / 3, 4)
        assert account.properties["cost_account_monthly"] == 12.0
        assert [r.metrics["monthly_cost"] for r in out if r.resource_id == "fn-0"] == [12.0]
        assert all("monthly_cost" not in r.metrics for r in out if r.resource_id in ("fn-1", "fn-2"))

    def test_a_denied_account_still_evaluates_and_says_why(self, engine):
        ce = FakeCE(error=client_error("AccessDeniedException", "not authorized to perform: ce:GetCostAndUsageWithResources"))

        out = collectors.collect_all(session=session_with(ce))

        assert len([r for r in out if r.resource_type == "aws.lambda"]) == 3
        account = [r for r in out if r.resource_type == "aws.account"][0]
        assert account.properties["cost_status"] == "access_denied"

    def test_exact_number_of_cost_explorer_requests(self, engine):
        ce = FakeCE()

        collectors.collect_all(session=session_with(ce))

        assert collectors.CE_REQUESTS_PER_EVALUATION == 7
        assert ce.total == collectors.CE_REQUESTS_PER_EVALUATION, dict(ce.calls)
        assert ce.calls["get_cost_and_usage_with_resources"] == 1  # was 2
        assert ce.calls["get_savings_plans_utilization"] == 1      # was 2

    def test_requests_grow_by_one_per_extra_region(self, engine):
        ce = FakeCE()

        collectors.collect_all(session=session_with(ce), regions=["us-east-1", "us-west-2"])

        assert ce.total == collectors.expected_ce_requests(2) == 8

    def test_signature_unchanged(self):
        import inspect

        names = list(inspect.signature(collectors.collect_all).parameters)
        assert names == ["profile", "resource_types", "regions", "session"]


# --------------------------------------------------------------------------- policies
class TestPoliciesWithUnknownCost:
    @staticmethod
    def _policy(name: str):
        return [p for p in load_policies("policies/") if p.metadata.name == name]

    def test_cost_threshold_policy_skips_a_resource_without_cost(self):
        ec2 = Resource(resource_id="i-1", resource_type="aws.ec2", region="us-east-1", account_id=ACCT)

        findings = evaluate(self._policy("resource-cost-exceeds-50"), [ec2])

        assert findings == []

    def test_cost_threshold_policy_fires_on_a_real_cost_and_savings_equal_that_cost(self):
        ec2 = Resource(resource_id="i-1", resource_type="aws.ec2", region="us-east-1", account_id=ACCT, metrics={"monthly_cost": 80.0})

        findings = evaluate(self._policy("resource-cost-exceeds-50"), [ec2])

        assert [(f.resource_id, f.estimated_savings) for f in findings] == [("i-1", 80.0)]

    def test_idle_expensive_needs_a_real_cost_to_fire(self):
        idle = Resource(resource_id="i-2", resource_type="aws.ec2", region="us-east-1", account_id=ACCT, metrics={"avg_cpu_utilization": 1.0})
        costly = Resource(resource_id="i-3", resource_type="aws.ec2", region="us-east-1", account_id=ACCT, metrics={"avg_cpu_utilization": 1.0, "monthly_cost": 40.0})

        findings = evaluate(self._policy("idle-expensive-resource"), [idle, costly])

        assert [f.resource_id for f in findings] == ["i-3"]
