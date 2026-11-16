"""Billing & Cost Explorer helpers (S116).

Reusable Cost Explorer / Organizations primitives shared by ``sofe-server`` and
the CLI. The HTTP layer lives in ``sofe-server``; this module stays dependency-light
except for Pydantic and boto3/botocore.
"""

from .ce import (
    EXCLUDED_RECORD_TYPES,
    MAX_PAGES,
    account_cost_filter,
    classify_ce_error,
    query_costs,
    query_resources,
    to_cents,
)
from .credits import (
    BillingApiError,
    classify_billing_error,
    get_credit_allocations,
    get_credits,
)
from .models import (
    CostGroup,
    CostQuery,
    CostResult,
    CostStatus,
    Credit,
    CreditAllocation,
    CreditAllocationResult,
    CreditBalanceResult,
    Granularity,
    GroupBy,
    Metric,
    OrganizationAccount,
    OrganizationResult,
)
from .organization import build_organization, describe_organization, list_accounts

__all__ = [
    "EXCLUDED_RECORD_TYPES",
    "MAX_PAGES",
    "BillingApiError",
    "CostGroup",
    "CostQuery",
    "CostResult",
    "CostStatus",
    "Credit",
    "CreditAllocation",
    "CreditAllocationResult",
    "CreditBalanceResult",
    "Granularity",
    "GroupBy",
    "Metric",
    "OrganizationAccount",
    "OrganizationResult",
    "account_cost_filter",
    "build_organization",
    "classify_billing_error",
    "classify_ce_error",
    "describe_organization",
    "get_credit_allocations",
    "get_credits",
    "list_accounts",
    "query_costs",
    "query_resources",
    "to_cents",
]
