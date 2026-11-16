"""S116-B5.1 — AWS Billing API credit balances and allocation history."""

from __future__ import annotations

from datetime import date

from botocore.credentials import Credentials

from sofe.billing import credits
from sofe.billing.credits import (
    BillingApiError,
    classify_billing_error,
    get_credit_allocations,
    get_credits,
)
from sofe.billing.models import CostStatus

CREDS = Credentials("AKIDEXAMPLE", "secret-key", "session-token")


def credit(credit_id: str, account: str, initial: str, remaining: str, estimated: str | None = None):
    item = {
        "creditId": credit_id,
        "accountId": account,
        "creditType": "Promotion",
        "description": f"credit {credit_id}",
        "initialAmount": {"currencyAmount": initial, "currencyCode": "USD"},
        "remainingAmount": {"currencyAmount": remaining, "currencyCode": "USD"},
        "startDate": 1763251200,
        "endDate": 1794787200,
        "exhaustDate": None,
        "creditStatus": "ENABLED",
        "applicableProductNames": ["Amazon S3"],
    }
    if estimated is not None:
        item["estimatedAmount"] = {"currencyAmount": estimated, "currencyCode": "USD"}
    return item


class TestGetCredits:
    def test_parses_amounts_to_cents_and_aggregates_by_account(self):
        calls: list[tuple[str, dict]] = []

        def request(_credentials, operation, payload):
            calls.append((operation, payload))
            return {
                "credits": [
                    credit("1", "111122223333", "3000.00", "3000.00", "3000.00"),
                    credit("2", "111122223333", "1000.00", "979.90", "947.43"),
                    credit("3", "444455556666", "50.00", "26.54"),
                ]
            }

        result = get_credits(
            CREDS,
            account_id="111122223333",
            start=date(2025, 11, 16),
            end=date(2026, 11, 16),
            payer_account_flag=True,
            request=request,
        )

        assert calls[0][0] == "GetCredits"
        assert calls[0][1]["payerAccountFlag"] is True
        assert calls[0][1]["accountId"] == "111122223333"
        assert result.status == CostStatus.ok
        assert result.total_initial_cents == 405000
        assert result.total_remaining_cents == 400644
        assert result.total_estimated_cents == 394743
        assert result.by_account == {"111122223333": 397990, "444455556666": 2654}
        assert result.credits[0].initial_cents == 300000
        assert result.credits[1].start_date == date(2025, 11, 16)

    def test_access_denied_is_classified_without_arns(self):
        def request(_credentials, _operation, _payload):
            raise BillingApiError(
                "com.amazon.coral.service#AccessDeniedException",
                "User: arn:aws:sts::999999999999:assumed-role/r/s is not authorized to perform: billing:GetCredits",
                400,
            )

        result = get_credits(CREDS, account_id="111122223333", start=date(2025, 11, 16), request=request)

        assert result.status == CostStatus.access_denied
        assert "billing:GetCredits" in result.detail
        assert "arn:" not in result.detail
        assert "999999999999" not in result.detail

    def test_throttling_is_reported_as_an_error(self):
        status, detail = classify_billing_error(
            BillingApiError("com.amazon.coral.service#ThrottlingException", "slow down", 400)
        )
        assert status == CostStatus.error
        assert detail == "throttled"


class TestCreditAllocations:
    def test_follows_pagination_and_aggregates_positive_magnitudes(self):
        calls: list[dict] = []

        def request(_credentials, operation, payload):
            calls.append(payload)
            if "nextToken" not in payload:
                return {
                    "creditAllocationHistoryList": [
                        {
                            "accountId": "111122223333",
                            "billingMonth": "2026-10",
                            "appliedServiceName": "AmazonQ",
                            "creditId": "1",
                            "creditAmount": {"currencyAmount": "-19.00", "currencyCode": "USD"},
                            "description": "q",
                            "isEstimatedBill": False,
                        }
                    ],
                    "nextToken": "t1",
                    "partialResults": False,
                }
            return {
                "creditAllocationHistoryList": [
                    {
                        "accountId": "111122223333",
                        "billingMonth": "2026-10",
                        "appliedServiceName": "awswaf",
                        "creditId": "1",
                        "creditAmount": {"currencyAmount": "-7.00", "currencyCode": "USD"},
                        "description": "waf",
                        "isEstimatedBill": True,
                    }
                ],
                "partialResults": True,
                "failedMonths": ["2026-09"],
            }

        result = get_credit_allocations(
            CREDS,
            account_id="111122223333",
            start=date(2026, 10, 1),
            end=date(2026, 11, 1),
            request=request,
        )

        assert calls[0]["startDate"] == 1790812800
        assert calls[0]["endDate"] == 1793491200
        assert calls[0]["maxResults"] == 1000
        assert calls[1]["nextToken"] == "t1"
        assert result.status == CostStatus.ok
        assert result.requests == 2
        assert result.total_cents == 2600
        assert result.by_month == {"2026-10": 2600}
        assert result.by_account == {"111122223333": 2600}
        assert result.by_service == {"AmazonQ": 1900, "awswaf": 700}
        assert result.partial is True
        assert result.failed_months == ["2026-09"]
        assert result.entries[0].amount_cents == 1900
        assert result.entries[1].estimated is True

    def test_empty_history_is_no_data(self):
        result = get_credit_allocations(
            CREDS,
            account_id="111122223333",
            start=date(2026, 10, 1),
            end=date(2026, 11, 1),
            request=lambda *a: {"creditAllocationHistoryList": []},
        )
        assert result.status == CostStatus.no_data
        assert result.total_cents == 0


def test_request_signs_the_billing_target(monkeypatch):
    captured: dict = {}

    class Response:
        status = 200
        data = b'{"credits": []}'

    class Pool:
        def request(self, method, url, body=None, headers=None, **kwargs):
            captured.update(method=method, url=url, body=body, headers=headers)
            return Response()

    monkeypatch.setattr(credits, "_pool", lambda: Pool())

    data = credits._request(CREDS, "GetCredits", {"accountId": "111122223333", "startDate": 1})

    assert captured["method"] == "POST"
    assert captured["url"] == credits.BILLING_ENDPOINT
    assert captured["headers"]["X-Amz-Target"] == "AWSBilling.GetCredits"
    assert captured["headers"]["X-Amz-Security-Token"] == "session-token"
    assert "Authorization" in captured["headers"]
    assert data == {"credits": []}
