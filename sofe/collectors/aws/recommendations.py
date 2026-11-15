"""Recommendations collector — maps AWS purchase/rightsizing recommendations to findings.

S082 — consumes GetSavingsPlansPurchaseRecommendation + GetRightsizingRecommendation.
Exposes findings (not resources) via get_findings(); the engine merges them into the
evaluation results and computes potential_savings_monthly.

IAM required on the SOFE reader role:
  - ce:GetSavingsPlansPurchaseRecommendation
  - ce:GetRightsizingRecommendation
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone

from botocore.exceptions import ClientError

from ..aws.base import BaseCollector
from ...models import Finding, Resource, Severity


# S113 — values the Cost Explorer API actually accepts (botocore model). The previous code sent
# "COMPUTE"/"EC2_INSTANCE", "1", "30" and "SHARED": every request was rejected and the error swallowed.
SP_TYPES = {"COMPUTE_SP": "Compute", "EC2_INSTANCE_SP": "EC2 Instance"}   # enum -> label for messages
SP_TERM = "ONE_YEAR"
SP_LOOKBACK = "THIRTY_DAYS"
SP_PAYMENT = "NO_UPFRONT"
# We evaluate ONE account at a time. PAYER would add the whole organization to a management account.
SP_ACCOUNT_SCOPE = "LINKED"
# Rightsizing: stay in the same instance family (no cross-family migrations suggested).
RIGHTSIZING_TARGET = "SAME_INSTANCE_FAMILY"

# Error codes that mean "this account has nothing to recommend / feature not enabled", not a bug.
_EXPECTED = {"DataUnavailableException", "OptInRequired"}


class RecommendationsCollector(BaseCollector):
    """Meta-collector that turns AWS savings recommendations into SOFE findings."""

    resource_type = "aws.recommendations"

    def __init__(self, session, region: str, account_id: str, target_regions: list[str] | None = None):
        super().__init__(session, region, account_id)
        self.target_regions = target_regions or ["us-east-1"]
        self._findings: list[Finding] = []
        self._errors: list[tuple[str, str]] = []  # (source, error code), never the AWS message

    def collect(self) -> list[Resource]:
        """Query CE recommendations and convert to SOFE findings."""
        try:
            ce = self.session.client("ce", region_name="us-east-1")
        except Exception:
            return []

        self._findings = []
        self._errors = []
        seen: set[tuple] = set()

        # --- Savings Plans Purchase Recommendations (each type on its own: one failing must not skip the other) ---
        for spp_type, label in SP_TYPES.items():
            try:
                resp = ce.get_savings_plans_purchase_recommendation(
                    SavingsPlansType=spp_type,
                    LookbackPeriodInDays=SP_LOOKBACK,
                    TermInYears=SP_TERM,
                    PaymentOption=SP_PAYMENT,
                    AccountScope=SP_ACCOUNT_SCOPE,
                )
                recs = resp.get("SavingsPlansPurchaseRecommendation", {})
                details = recs.get("SavingsPlansPurchaseRecommendationDetails", [])
                for d in details:
                    f = self._purchase_finding(d, label)
                    key = (f.policy_name, f.resource_id, round(f.estimated_savings or 0, 2))
                    if key in seen:
                        continue
                    seen.add(key)
                    self._findings.append(f)
            except Exception as e:
                self._record_error(f"savings-plans:{spp_type}", e)

        # --- Rightsizing Recommendations (per region) ---
        for region in self.target_regions:
            try:
                token = None
                while True:
                    kwargs = {
                        "Service": "AmazonEC2",
                        "Configuration": {"RecommendationTarget": RIGHTSIZING_TARGET, "BenefitsConsidered": True},
                        "Filter": {"Dimensions": {"Key": "REGION", "Values": [region]}},
                    }
                    if token:
                        kwargs["NextPageToken"] = token
                    resp = ce.get_rightsizing_recommendation(**kwargs)
                    for rec in resp.get("RightsizingRecommendations", []):
                        f = self._rightsize_finding(rec, region)
                        if not f:
                            continue
                        key = (f.policy_name, f.resource_id, round(f.estimated_savings or 0, 2))
                        if key in seen:
                            continue
                        seen.add(key)
                        self._findings.append(f)
                    token = resp.get("NextPageToken")
                    if not token:
                        break
            except Exception as e:
                self._record_error(f"rightsizing:{region}", e)
                continue

        return []  # meta-collector: findings exposed via get_findings()

    def _purchase_finding(self, detail: dict, spp_type: str) -> Finding:  # spp_type is the human label (e.g. 'Compute')
        monthly = float(detail.get("EstimatedMonthlySavingsAmount", 0) or 0)
        upfront = float(detail.get("UpfrontCost", 0) or 0)
        roi = round(upfront / monthly, 1) if monthly > 0 else None
        plans = detail.get("SavingsPlansDetails", {}) or {}
        family = plans.get("InstanceFamily", "Compute")
        severity = Severity.high if monthly >= 100 else Severity.medium
        roi_txt = f"{roi} meses" if roi is not None else "n/a"
        return Finding(
            id=str(uuid.uuid4())[:8],
            policy_name="savings-plan-purchase",
            severity=severity,
            resource_id=self.account_id,
            resource_type="aws.commitments",
            region="us-east-1",
            account_id=self.account_id,
            message=(
                f"Comprar Savings Plan {spp_type} ({family}): estimado ${monthly:.0f}/mes · "
                f"upfront ${upfront:.0f} · ROI ~{roi_txt}"
            ),
            metric_name="potential_savings",
            metric_value=round(monthly, 2),
            threshold=0.0,
            estimated_savings=round(monthly, 2),
            recommendation=f"Buy a NO_UPFRONT 1yr Savings Plan ({family}) — lookback 30d",
            remediation_eligible=False,
            timestamp=datetime.now(timezone.utc),
        )

    def _rightsize_finding(self, rec: dict, region: str) -> Finding | None:
        current = rec.get("CurrentInstance", {}) or {}
        inst_name = current.get("InstanceName") or current.get("ResourceId") or "instance"
        rightsizing_type = rec.get("RightsizingType", "Modify")

        savings = 0.0
        new_type = None
        if rightsizing_type == "Modify":
            mod = rec.get("ModifyRecommendationDetail", {}) or {}
            targets = mod.get("TargetInstances", []) or []
            if targets:
                target = targets[0]
                savings = float(target.get("EstimatedMonthlySavings", 0) or 0)
                new_type = (target.get("ResourceDetails", {}) or {}).get(
                    "EC2ResourceDetails", {}
                ).get("InstanceType")
        else:
            term = rec.get("TerminateRecommendationDetail", {}) or {}
            savings = float(term.get("EstimatedMonthlySavings", 0) or 0)
            new_type = "terminate"

        if savings <= 0:
            return None
        if not new_type:
            new_type = "smaller"

        severity = Severity.high if savings >= 100 else Severity.medium
        return Finding(
            id=str(uuid.uuid4())[:8],
            policy_name="rightsize-recommendation",
            severity=severity,
            resource_id=inst_name,
            resource_type="aws.ec2",
            region=region,
            account_id=self.account_id,
            message=(
                f"Rightsize {inst_name} → {new_type}: ahorro estimado ${savings:.0f}/mes"
            ),
            metric_name="potential_savings",
            metric_value=round(savings, 2),
            threshold=0.0,
            estimated_savings=round(savings, 2),
            recommendation=f"Resize {inst_name} to {new_type} (rightsizing type: {rightsizing_type})",
            remediation_eligible=False,
            timestamp=datetime.now(timezone.utc),
        )

    def _record_error(self, source: str, exc: Exception) -> None:
        """Make a failed request visible (it used to be swallowed, which hid a total outage for months).

        Only the error code (and, for IAM denials, the missing action) is kept and logged: AWS
        messages can embed role and account ARNs. An opt-in feature that is simply not enabled
        is an expected state, not a defect: it is logged at info level.
        """
        from .cost import classify_ce_error, COST_NOT_ENABLED

        code = exc.response.get("Error", {}).get("Code", "ClientError") if isinstance(exc, ClientError) else type(exc).__name__
        status, detail = classify_ce_error(exc)
        self._errors.append((source, code))
        expected = status == COST_NOT_ENABLED or code in _EXPECTED
        print(f"  {'ℹ️ ' if expected else '⚠️ '} recommendations({source}): {detail}{' (feature not enabled)' if expected else ''}", file=sys.stderr)

    def get_errors(self) -> list[tuple[str, str]]:
        """(source, error code) for every request that failed in the last collect()."""
        return list(self._errors)

    def get_findings(self) -> list[Finding]:
        return getattr(self, "_findings", [])