"""Cost Explorer query/normalization helpers (S116-B1).

The helpers are intentionally small and testable: they build valid AWS parameters,
follow pagination with a hard cap, normalize amounts to integer cents, and map
botocore errors to the same ``cost_status`` values used by S111.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from botocore.exceptions import ClientError

from .models import CostGroup, CostQuery, CostResult, CostStatus

MAX_PAGES = 50
EXCLUDED_RECORD_TYPES = ["Credit", "Refund", "Tax"]


def to_cents(amount: str | float | Decimal) -> int:
    """Convert an AWS amount string/float to integer cents using decimal rounding."""
    return int((Decimal(str(amount)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def classify_ce_error(exc: Exception) -> tuple[CostStatus, str]:
    """Map a Cost Explorer failure to ``(cost_status, short_detail)``.

    The detail never contains the AWS error message: those embed role/account ARNs
    (possibly of other accounts). Only the error code and, for IAM denials, the action.
    """
    code, msg = "", str(exc)
    if isinstance(exc, ClientError):
        err = exc.response.get("Error", {})
        code, msg = err.get("Code", ""), err.get("Message", "") or ""
    low = msg.lower()

    # "not enabled" covers Cost Explorer itself and its opt-in features
    # (resource-level data, rightsizing), which AWS reports as AccessDeniedException.
    if "not enabled" in low or "opt-in" in low or code in ("DataUnavailableException", "OptInRequired"):
        return CostStatus.not_enabled, code or "CostExplorerNotEnabled"
    if code.startswith("AccessDenied") or code in ("UnauthorizedOperation", "UnauthorizedAccess"):
        action = re.search(r"perform:\s*([a-z0-9-]+:[A-Za-z0-9*]+)", msg)
        return CostStatus.access_denied, f"{code}: {action.group(1)}" if action else code
    return CostStatus.error, code or type(exc).__name__


def account_cost_filter(
    account_id: str | None,
    *,
    exclude_credits: bool = True,
    exclude_refunds: bool = True,
    exclude_taxes: bool = True,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a Cost Explorer filter scoped to one account.

    Without ``LINKED_ACCOUNT`` a management account receives every member account's
    spend; using the evaluated account id prevents cross-account attribution.
    """
    excluded: list[str] = []
    if exclude_credits:
        excluded.append("Credit")
    if exclude_refunds:
        excluded.append("Refund")
    if exclude_taxes:
        excluded.append("Tax")

    clauses: list[dict[str, Any]] = []
    if excluded:
        clauses.append({"Not": {"Dimensions": {"Key": "RECORD_TYPE", "Values": excluded}}})
    if account_id:
        clauses.append({"Dimensions": {"Key": "LINKED_ACCOUNT", "Values": [account_id]}})
    if extra:
        clauses.append(extra)

    if not clauses:
        raise ValueError("a Cost Explorer filter needs at least one clause")
    if len(clauses) == 1:
        return clauses[0]
    return {"And": clauses}


def _metric_amount(metrics: dict[str, Any], metric: str) -> tuple[str, str]:
    item = metrics.get(metric) or metrics.get("UnblendedCost") or {}
    return str(item.get("Amount", "0") or "0"), str(item.get("Unit", "USD") or "USD")


def query_costs(
    client: Any,
    query: CostQuery,
    *,
    with_resources: bool = False,
) -> CostResult:
    """Run a Cost Explorer query, follow pagination, and normalize to cents.

    ``client`` is a boto3 Cost Explorer client (or a compatible stub). The caller
    owns credentials/AssumeRole; these helpers never create sessions.
    """
    operation = "get_cost_and_usage_with_resources" if with_resources else "get_cost_and_usage"
    call = getattr(client, operation, None)
    if call is None:
        raise AttributeError(f"Cost Explorer client has no {operation} operation")

    kwargs = query.to_aws()
    if with_resources and not query.group_by:
        kwargs["GroupBy"] = [{"Type": "DIMENSION", "Key": "RESOURCE_ID"}]

    groups: list[CostGroup] = []
    total_cents = 0
    requests = 0
    token: str | None = None

    try:
        for _ in range(MAX_PAGES):
            page_kwargs = dict(kwargs)
            if token:
                page_kwargs["NextPageToken"] = token
            requests += 1
            response = call(**page_kwargs)

            for result in response.get("ResultsByTime", []) or []:
                page_groups = result.get("Groups") or []
                if page_groups:
                    period = str((result.get("TimePeriod") or {}).get("Start", "") or "")
                    for group in page_groups:
                        amount, unit = _metric_amount(group.get("Metrics", {}), query.metric.value)
                        cents = to_cents(amount)
                        groups.append(
                            CostGroup(
                                keys=list(group.get("Keys", [])),
                                amount_cents=cents,
                                unit=unit,
                                period=period,
                            )
                        )
                        total_cents += cents
                else:
                    total = (result.get("Total") or {})
                    if total:
                        amount, _unit = _metric_amount(total, query.metric.value)
                        total_cents += to_cents(amount)

            token = response.get("NextPageToken")
            if not token:
                break
        else:
            return CostResult(
                groups=groups,
                total_cents=total_cents,
                requests=requests,
                status=CostStatus.error,
                detail="pagination_limit",
                period_start=query.start,
                period_end=query.end,
            )
    except Exception as exc:  # noqa: BLE001 - normalize every boto/network failure
        status, detail = classify_ce_error(exc)
        return CostResult(
            groups=[],
            total_cents=0,
            requests=requests,
            status=status,
            detail=detail,
            period_start=query.start,
            period_end=query.end,
        )

    return CostResult(
        groups=groups,
        total_cents=total_cents,
        requests=requests,
        status=CostStatus.ok if total_cents > 0 else CostStatus.no_data,
        detail="",
        period_start=query.start,
        period_end=query.end,
    )


def query_resources(client: Any, query: CostQuery) -> CostResult:
    """Resource-level daily query (last 14 days by AWS limit) normalized to cents."""
    return query_costs(client, query, with_resources=True)


def date_range(start: date, end: date) -> tuple[date, date]:
    """Small guard used by callers that accept user-supplied dates."""
    if end <= start:
        raise ValueError("end must be after start")
    return start, end
