"""AWS Billing API credit helpers (S116-B5.1).

The Billing API (``billing.us-east-1.api.aws``) exposes credit balances and the
per-billing-month application history. At the time of writing the operations
(``GetCredits`` / ``GetCreditAllocationHistory``) are not in botocore's public
service model, so this module signs a JSON 1.0 request with SigV4 instead of
creating a client. Callers own credentials (AssumeRole); this module never
creates sessions.

Amounts are normalized to positive integer cents: a credit balance is money that
can still be consumed, and an allocation is the magnitude applied to a bill (AWS
reports it as a negative line item).
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Any, Callable

from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

from .ce import to_cents
from .models import (
    CostStatus,
    Credit,
    CreditAllocation,
    CreditAllocationResult,
    CreditBalanceResult,
)

BILLING_ENDPOINT = "https://billing.us-east-1.api.aws/"
BILLING_REGION = "us-east-1"
BILLING_SIGNING_NAME = "billing"
BILLING_TARGET_PREFIX = "AWSBilling"
MAX_PAGES = 20
PAGE_SIZE = 1000

RequestFn = Callable[[Any, str, dict[str, Any]], dict[str, Any]]


class BillingApiError(Exception):
    """A non-2xx response from the Billing API (already parsed)."""

    def __init__(self, code: str, message: str, status: int):
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code
        self.message = message
        self.status = status


def _pool():
    # urllib3 ships with botocore; import lazily so tests can inject a request fn.
    import urllib3

    return urllib3.PoolManager()


def _request(credentials: Any, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
    """POST a signed JSON 1.0 request to the Billing API."""
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    request = AWSRequest(
        method="POST",
        url=BILLING_ENDPOINT,
        data=body,
        headers={
            "Content-Type": "application/x-amz-json-1.0",
            "X-Amz-Target": f"{BILLING_TARGET_PREFIX}.{operation}",
        },
    )
    SigV4Auth(credentials, BILLING_SIGNING_NAME, BILLING_REGION).add_auth(request)
    prepared = request.prepare()
    response = _pool().request(
        "POST",
        BILLING_ENDPOINT,
        body=prepared.body,
        headers={str(key): str(value) for key, value in prepared.headers.items()},
        timeout=15.0,
        retries=False,
    )
    raw = response.data.decode("utf-8", "replace")
    data = json.loads(raw) if raw else {}
    if response.status != 200:
        code = str(data.get("__type") or data.get("code") or f"HTTP{response.status}")
        raise BillingApiError(code, str(data.get("message") or data.get("Message") or ""), response.status)
    return data


def classify_billing_error(exc: Exception) -> tuple[CostStatus, str]:
    """Map a Billing API failure to a ``cost_status`` and an ARN-free detail."""
    if isinstance(exc, BillingApiError):
        code = exc.code.split("#")[-1]
        low = f"{code} {exc.message}".lower()
        if exc.status in (401, 403) or "accessdenied" in low:
            action = re.search(r"perform:\s*([a-z0-9-]+:[A-Za-z0-9*]+)", exc.message)
            return CostStatus.access_denied, f"{code}: {action.group(1)}" if action else code
        if "not enabled" in low or "opt-in" in low:
            return CostStatus.not_enabled, code
        if "throttl" in low:
            return CostStatus.error, "throttled"
        return CostStatus.error, code
    return CostStatus.error, type(exc).__name__


def _epoch(day: date) -> int:
    return int(datetime.combine(day, time(0), tzinfo=timezone.utc).timestamp())


def _date(value: Any) -> date | None:
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).date()
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _decimal(value: Any) -> Decimal:
    if isinstance(value, dict):
        value = value.get("currencyAmount", 0)
    return Decimal(str(value or 0))


def _cents(value: Any) -> int:
    return to_cents(_decimal(value))


def _parse_credit(item: dict[str, Any]) -> Credit:
    estimated = item.get("estimatedAmount")
    return Credit(
        credit_id=str(item.get("creditId", "") or ""),
        account_id=str(item.get("accountId", "") or ""),
        credit_type=str(item.get("creditType", "") or ""),
        description=str(item.get("description", "") or ""),
        initial_cents=_cents(item.get("initialAmount")),
        remaining_cents=_cents(item.get("remainingAmount")),
        estimated_cents=_cents(estimated) if estimated is not None else None,
        start_date=_date(item.get("startDate")),
        end_date=_date(item.get("endDate")),
        exhaust_date=_date(item.get("exhaustDate")),
        status=str(item.get("creditStatus", "") or ""),
        products=[str(name) for name in (item.get("applicableProductNames") or [])],
    )


def _parse_allocation(item: dict[str, Any]) -> CreditAllocation:
    return CreditAllocation(
        account_id=str(item.get("accountId", "") or ""),
        billing_month=str(item.get("billingMonth", "") or ""),
        service=str(item.get("appliedServiceName", "") or ""),
        credit_id=str(item.get("creditId", "") or ""),
        amount_cents=abs(_cents(item.get("creditAmount"))),
        description=str(item.get("description", "") or ""),
        estimated=bool(item.get("isEstimatedBill")),
    )


def get_credits(
    credentials: Any,
    *,
    account_id: str,
    start: date,
    end: date | None = None,
    payer_account_flag: bool = False,
    request: RequestFn = _request,
) -> CreditBalanceResult:
    """Return credit balances for one account or the whole consolidated family.

    ``GetCredits`` requires ``accountId``; when the caller is the management
    account and ``payer_account_flag`` is true, the response aggregates every
    member account in the billing family.
    """
    payload: dict[str, Any] = {"accountId": account_id, "startDate": _epoch(start)}
    if end is not None:
        payload["endDate"] = _epoch(end)
    if payer_account_flag:
        payload["payerAccountFlag"] = True

    try:
        data = request(credentials, "GetCredits", payload)
    except Exception as exc:  # noqa: BLE001 - normalize every transport/API failure
        status, detail = classify_billing_error(exc)
        return CreditBalanceResult(
            status=status,
            detail=detail,
            requests=1,
            period_start=start,
            period_end=end or start,
        )

    credits = [_parse_credit(item) for item in (data.get("credits") or [])]
    by_account: dict[str, int] = {}
    for credit in credits:
        by_account[credit.account_id] = by_account.get(credit.account_id, 0) + credit.remaining_cents
    return CreditBalanceResult(
        credits=credits,
        total_initial_cents=sum(credit.initial_cents for credit in credits),
        total_remaining_cents=sum(credit.remaining_cents for credit in credits),
        total_estimated_cents=sum(credit.estimated_cents or 0 for credit in credits),
        by_account=by_account,
        status=CostStatus.ok,
        requests=1,
        period_start=start,
        period_end=end or start,
    )


def get_credit_allocations(
    credentials: Any,
    *,
    account_id: str,
    start: date,
    end: date,
    credit_id: str | None = None,
    request: RequestFn = _request,
) -> CreditAllocationResult:
    """Return the per-billing-month credit applications for a date range."""
    entries: list[CreditAllocation] = []
    failed_months: list[str] = []
    partial = False
    requests = 0
    token: str | None = None
    # Sum the raw decimals and round once: rounding each line item to cents would
    # drift the monthly/service totals from Cost Explorer.
    raw_total = Decimal("0")
    raw_by_month: dict[str, Decimal] = {}
    raw_by_account: dict[str, Decimal] = {}
    raw_by_service: dict[str, Decimal] = {}

    try:
        for _ in range(MAX_PAGES):
            payload: dict[str, Any] = {
                "accountId": account_id,
                "startDate": _epoch(start),
                "endDate": _epoch(end),
                "maxResults": PAGE_SIZE,
            }
            if credit_id:
                payload["creditId"] = credit_id
            if token:
                payload["nextToken"] = token
            requests += 1
            data = request(credentials, "GetCreditAllocationHistory", payload)
            partial = partial or bool(data.get("partialResults"))
            failed_months.extend(str(month) for month in (data.get("failedMonths") or []))
            for item in data.get("creditAllocationHistoryList") or []:
                entry = _parse_allocation(item)
                entries.append(entry)
                raw = abs(_decimal(item.get("creditAmount")))
                raw_total += raw
                raw_by_month[entry.billing_month] = raw_by_month.get(entry.billing_month, Decimal("0")) + raw
                raw_by_account[entry.account_id] = raw_by_account.get(entry.account_id, Decimal("0")) + raw
                raw_by_service[entry.service] = raw_by_service.get(entry.service, Decimal("0")) + raw
            token = data.get("nextToken")
            if not token:
                break
        else:
            return CreditAllocationResult(
                status=CostStatus.error,
                detail="pagination_limit",
                requests=requests,
                period_start=start,
                period_end=end,
            )
    except Exception as exc:  # noqa: BLE001 - normalize every transport/API failure
        status, detail = classify_billing_error(exc)
        return CreditAllocationResult(
            status=status,
            detail=detail,
            requests=requests,
            period_start=start,
            period_end=end,
        )

    return CreditAllocationResult(
        entries=entries,
        total_cents=to_cents(raw_total),
        by_month={month: to_cents(amount) for month, amount in raw_by_month.items()},
        by_account={account: to_cents(amount) for account, amount in raw_by_account.items()},
        by_service={service: to_cents(amount) for service, amount in raw_by_service.items()},
        failed_months=failed_months,
        partial=partial,
        status=CostStatus.ok if entries else CostStatus.no_data,
        requests=requests,
        period_start=start,
        period_end=end,
    )
