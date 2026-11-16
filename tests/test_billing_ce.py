"""S116-B1 — Cost Explorer query helpers.

Protects:
  * valid AWS parameters (dates, metric, at most two GroupBy);
  * pagination with a hard cap;
  * integer cents (no float drift);
  * the same cost_status/error mapping used by S111;
  * the account-scoped filter (no cross-account attribution).
"""

from __future__ import annotations

from datetime import date

import pytest
from botocore.exceptions import ClientError
from pydantic import ValidationError

from sofe.billing.ce import (
    MAX_PAGES,
    account_cost_filter,
    classify_ce_error,
    query_costs,
    query_resources,
    to_cents,
)
from sofe.billing.models import CostQuery, CostStatus, Granularity, GroupBy, Metric

START = date(2026, 10, 1)
END = date(2026, 11, 1)


def page(rows: dict[str, str], token: str | None = None, metric: str = "UnblendedCost") -> dict:
    result = {
        "Groups": [
            {"Keys": [key], "Metrics": {metric: {"Amount": amount, "Unit": "USD"}}}
            for key, amount in rows.items()
        ]
    }
    response = {"ResultsByTime": [result]}
    if token:
        response["NextPageToken"] = token
    return response


class FakeCE:
    def __init__(self, pages=None, error: Exception | None = None):
        self.pages = list(pages or [page({})])
        self.error = error
        self.calls: list[tuple[str, dict]] = []

    def _answer(self, name, kwargs):
        self.calls.append((name, kwargs))
        if self.error:
            raise self.error
        return self.pages.pop(0) if len(self.pages) > 1 else self.pages[0]

    def get_cost_and_usage(self, **kwargs):
        return self._answer("get_cost_and_usage", kwargs)

    def get_cost_and_usage_with_resources(self, **kwargs):
        return self._answer("get_cost_and_usage_with_resources", kwargs)


def query(**overrides) -> CostQuery:
    values = {"start": START, "end": END}
    values.update(overrides)
    return CostQuery(**values)


class TestQueryModel:
    def test_builds_valid_aws_parameters(self):
        q = query(
            granularity=Granularity.daily,
            metric=Metric.amortized,
            group_by=[GroupBy(type="DIMENSION", key="LINKED_ACCOUNT"), GroupBy(type="DIMENSION", key="SERVICE")],
        )
        params = q.to_aws()
        assert params["TimePeriod"] == {"Start": "2026-10-01", "End": "2026-11-01"}
        assert params["Granularity"] == "DAILY"
        assert params["Metrics"] == ["AmortizedCost"]
        assert params["GroupBy"] == [
            {"Type": "DIMENSION", "Key": "LINKED_ACCOUNT"},
            {"Type": "DIMENSION", "Key": "SERVICE"},
        ]

    def test_rejects_more_than_two_group_by_dimensions(self):
        with pytest.raises(ValidationError):
            query(
                group_by=[
                    GroupBy(type="DIMENSION", key="LINKED_ACCOUNT"),
                    GroupBy(type="DIMENSION", key="SERVICE"),
                    GroupBy(type="DIMENSION", key="REGION"),
                ]
            )

    def test_rejects_an_empty_or_inverted_range(self):
        with pytest.raises(ValidationError):
            CostQuery(start=END, end=START)
        with pytest.raises(ValidationError):
            CostQuery(start=START, end=START)


class TestPaginationAndCents:
    def test_follows_pages_and_sums_integer_cents(self):
        ce = FakeCE(pages=[page({"a": "1.005"}, token="t1"), page({"a": "1.0", "b": "2.0"})])
        result = query_costs(ce, query(group_by=[GroupBy(type="DIMENSION", key="RESOURCE_ID")]))

        assert [name for name, _ in ce.calls] == ["get_cost_and_usage", "get_cost_and_usage"]
        assert ce.calls[1][1]["NextPageToken"] == "t1"
        assert result.requests == 2
        assert result.status == CostStatus.ok
        assert result.total_cents == 401  # 101 + 100 + 200
        assert [(group.keys, group.amount_cents) for group in result.groups] == [(["a"], 101), (["a"], 100), (["b"], 200)]

    def test_total_without_groups_is_supported(self):
        ce = FakeCE(
            pages=[{"ResultsByTime": [{"Total": {"UnblendedCost": {"Amount": "12.34", "Unit": "USD"}}}]}]
        )
        result = query_costs(ce, query())
        assert result.total_cents == 1234
        assert result.groups == []
        assert result.status == CostStatus.ok

    def test_no_spend_is_no_data_not_zero_dollars(self):
        result = query_costs(FakeCE(pages=[page({})]), query())
        assert result.status == CostStatus.no_data
        assert result.total_cents == 0

    def test_pagination_loop_stops_with_an_error(self):
        ce = FakeCE(pages=[page({"a": "1.0"}, token="again")])
        result = query_costs(ce, query())
        assert result.requests == MAX_PAGES
        assert result.status == CostStatus.error
        assert result.detail == "pagination_limit"

    def test_decimal_cent_rounding(self):
        assert to_cents("1.005") == 101
        assert to_cents("0.004") == 0
        assert to_cents("-3.0") == -300


class TestErrorsAndFilters:
    @pytest.mark.parametrize(
        "exc, expected",
        [
            (ClientError({"Error": {"Code": "AccessDeniedException", "Message": "not authorized to perform: ce:GetCostAndUsage"}}, "op"), CostStatus.access_denied),
            (ClientError({"Error": {"Code": "AccessDeniedException", "Message": "not enabled for cost explorer"}}, "op"), CostStatus.not_enabled),
            (ClientError({"Error": {"Code": "DataUnavailableException", "Message": "x"}}, "op"), CostStatus.not_enabled),
            (ClientError({"Error": {"Code": "ThrottlingException", "Message": "x"}}, "op"), CostStatus.error),
            (RuntimeError("socket closed"), CostStatus.error),
        ],
    )
    def test_failures_are_classified(self, exc, expected):
        status, _detail = classify_ce_error(exc)
        assert status == expected

    def test_denial_names_the_action_but_never_an_arn(self):
        exc = ClientError(
            {
                "Error": {
                    "Code": "AccessDeniedException",
                    "Message": "User: arn:aws:sts::999999999999:assumed-role/r/s is not authorized to perform: ce:GetCostAndUsage on resource: arn:aws:ce:us-east-1:111122223333:/x",
                }
            },
            "op",
        )
        status, detail = classify_ce_error(exc)
        assert status == CostStatus.access_denied
        assert "ce:GetCostAndUsage" in detail
        assert "arn:" not in detail and "999999999999" not in detail

    def test_account_filter_is_scoped_and_excludes_credits_refunds_tax(self):
        flt = account_cost_filter("111122223333")
        assert {"Dimensions": {"Key": "LINKED_ACCOUNT", "Values": ["111122223333"]}} in flt["And"]
        excluded = [item for item in flt["And"] if "Not" in item][0]["Not"]["Dimensions"]
        assert excluded == {"Key": "RECORD_TYPE", "Values": ["Credit", "Refund", "Tax"]}

    def test_org_wide_filter_keeps_record_type_exclusions(self):
        flt = account_cost_filter(None)
        assert flt == {"Not": {"Dimensions": {"Key": "RECORD_TYPE", "Values": ["Credit", "Refund", "Tax"]}}}

    def test_no_filter_when_nothing_is_excluded_or_scoped(self):
        assert (
            account_cost_filter(
                None, exclude_credits=False, exclude_refunds=False, exclude_taxes=False
            )
            is None
        )

    def test_resource_query_uses_the_resource_operation_and_default_group(self):
        ce = FakeCE(pages=[page({"arn:aws:s3:::bucket": "7.0"})])
        result = query_resources(ce, query())
        assert ce.calls[0][0] == "get_cost_and_usage_with_resources"
        assert ce.calls[0][1]["GroupBy"] == [{"Type": "DIMENSION", "Key": "RESOURCE_ID"}]
        assert result.total_cents == 700
