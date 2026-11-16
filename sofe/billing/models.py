"""Typed models for Billing & Cost Explorer helpers (S116-B1).

These models are the engine's view of the frozen Billing API v1
(``sofe-platform/docs/billing-contract-v1.schema.json``). They deliberately do not
import FastAPI: ``sofe-server`` maps them to HTTP, while the CLI/engine can reuse them.
"""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CostStatus(str, Enum):
    """The same values the evaluation and the Billing API expose."""

    ok = "ok"
    partial = "partial"
    access_denied = "access_denied"
    not_enabled = "not_enabled"
    no_data = "no_data"
    error = "error"


class Granularity(str, Enum):
    daily = "DAILY"
    monthly = "MONTHLY"


class Metric(str, Enum):
    unblended = "UnblendedCost"
    amortized = "AmortizedCost"
    net_amortized = "NetAmortizedCost"
    net_unblended = "NetUnblendedCost"
    blended = "BlendedCost"


class GroupBy(BaseModel):
    """One Cost Explorer ``GroupBy`` entry; the API accepts at most two per query."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["DIMENSION", "TAG", "COST_CATEGORY"]
    key: str

    def to_aws(self) -> dict[str, str]:
        return {"Type": self.type, "Key": self.key}


class CostQuery(BaseModel):
    """A validated Cost Explorer query.

    ``end`` is exclusive, matching the AWS API. Dates are UTC ``date`` objects;
    the server/CLI may pass ISO strings and Pydantic converts them.
    """

    model_config = ConfigDict(extra="forbid")

    start: date
    end: date
    granularity: Granularity = Granularity.monthly
    metric: Metric = Metric.unblended
    group_by: list[GroupBy] = Field(default_factory=list, max_length=2)
    filter: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _validate(self) -> "CostQuery":
        if self.end <= self.start:
            raise ValueError("end must be after start")
        if len(self.group_by) > 2:
            raise ValueError("Cost Explorer accepts at most 2 group_by dimensions")
        for group in self.group_by:
            if not group.key.strip():
                raise ValueError("group_by key cannot be empty")
        return self

    def to_aws(self) -> dict[str, Any]:
        params: dict[str, Any] = {
            "TimePeriod": {"Start": self.start.isoformat(), "End": self.end.isoformat()},
            "Granularity": self.granularity.value,
            "Metrics": [self.metric.value],
        }
        if self.group_by:
            params["GroupBy"] = [group.to_aws() for group in self.group_by]
        if self.filter:
            params["Filter"] = self.filter
        return params


class CostGroup(BaseModel):
    """One normalized Cost Explorer group (keys → amount in integer cents)."""

    keys: list[str] = Field(default_factory=list)
    amount_cents: int
    unit: str = "USD"


class CostResult(BaseModel):
    """Normalized Cost Explorer response; amounts are integer cents (no float drift)."""

    groups: list[CostGroup] = Field(default_factory=list)
    total_cents: int = 0
    requests: int = 0
    status: CostStatus = CostStatus.no_data
    detail: str = ""
    period_start: date
    period_end: date


class OrganizationAccount(BaseModel):
    id: str
    name: str = ""
    email: str = ""
    status: str = ""
    joined_method: str = ""
    ou_id: str | None = None
    ou_path: str | None = None
    is_payer: bool = False
    alias: str | None = None


class OrganizationResult(BaseModel):
    available: bool = False
    is_payer: bool = False
    management_account_id: str | None = None
    accounts: list[OrganizationAccount] = Field(default_factory=list)
    detail: str = ""
