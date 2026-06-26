"""Core models for SOFE — Policy, Resource, Finding."""

from pydantic import BaseModel
from typing import Optional, Union
from enum import Enum
from datetime import datetime


class Severity(str, Enum):
    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"
    info = "info"


class Operator(str, Enum):
    lt = "<"
    gt = ">"
    lte = "<="
    gte = ">="
    eq = "=="
    ne = "!="


class AdditionalCondition(BaseModel):
    field: str
    operator: Operator
    value: Union[float, str]


class Scope(BaseModel):
    environments: list[str] = ["*"]
    resource_types: list[str]
    regions: list[str] = ["*"]
    accounts: list[str] = ["*"]
    exclude: Optional[dict] = None


class Rule(BaseModel):
    metric: str
    period: str = "30d"
    operator: Operator
    threshold: float
    additional_conditions: list[AdditionalCondition] = []


class Action(BaseModel):
    type: str  # finding, notify, recommend, block
    channel: Optional[str] = None
    suggestion: Optional[str] = None
    estimated_savings: Optional[str] = None


class Remediation(BaseModel):
    auto_eligible: bool = False
    action: Optional[str] = None
    requires_approval_if: Optional[dict] = None


class PolicyMetadata(BaseModel):
    name: str
    description: str
    author: Optional[str] = None
    created: Optional[str] = None
    tags: list[str] = []


class PolicySpec(BaseModel):
    scope: Scope
    rule: Rule
    severity: Severity
    actions: list[Action] = []
    remediation: Optional[Remediation] = None


class Policy(BaseModel):
    apiVersion: str = "sofe/v1"
    kind: str = "Policy"
    metadata: PolicyMetadata
    spec: PolicySpec


class Resource(BaseModel):
    resource_id: str
    resource_type: str
    region: str
    account_id: str
    tags: dict[str, str] = {}
    properties: dict = {}
    metrics: dict[str, float] = {}


class Finding(BaseModel):
    id: str
    policy_name: str
    severity: Severity
    resource_id: str
    resource_type: str
    region: str
    account_id: str
    message: str
    metric_name: str
    metric_value: float
    threshold: float
    estimated_savings: Optional[float] = None
    recommendation: Optional[str] = None
    remediation_eligible: bool = False
    timestamp: datetime
