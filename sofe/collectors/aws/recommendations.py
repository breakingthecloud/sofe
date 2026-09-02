"""Recommendations collector — maps AWS purchase/rightsizing recommendations to findings.

S082 — consumes GetSavingsPlansPurchaseRecommendation + GetRightsizingRecommendation.
Exposes findings (not resources) via get_findings(); the engine merges them into the
evaluation results and computes potential_savings_monthly.

IAM required on the SOFE reader role:
  - ce:GetSavingsPlansPurchaseRecommendation
  - ce:GetRightsizingRecommendation
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from ..aws.base import BaseCollector
from ...models import Finding, Resource, Severity


class RecommendationsCollector(BaseCollector):
    """Meta-collector that turns AWS savings recommendations into SOFE findings."""

    resource_type = "aws.recommendations"

    def __init__(self, session, region: str, account_id: str, target_regions: list[str] | None = None):
        super().__init__(session, region, account_id)
        self.target_regions = target_regions or ["us-east-1"]
        self._findings: list[Finding] = []

    def collect(self) -> list[Resource]:
        """Query CE recommendations and convert to SOFE findings."""
        try:
            ce = self.session.client("ce", region_name="us-east-1")
        except Exception:
            return []

        self._findings = []
        seen: set[tuple] = set()

        # --- Savings Plans Purchase Recommendations ---
        try:
            for spp_type in ["COMPUTE", "EC2_INSTANCE"]:
                resp = ce.get_savings_plans_purchase_recommendation(
                    SavingsPlansType=spp_type,
                    LookbackPeriodInDays="30",
                    TermInYears="1",
                    PaymentOption="NO_UPFRONT",
                    AccountScope="SHARED",
                )
                recs = resp.get("SavingsPlansPurchaseRecommendation", {})
                details = recs.get("SavingsPlansPurchaseRecommendationDetails", [])
                for d in details:
                    f = self._purchase_finding(d, spp_type)
                    key = (f.policy_name, f.resource_id, round(f.estimated_savings or 0, 2))
                    if key in seen:
                        continue
                    seen.add(key)
                    self._findings.append(f)
        except Exception:
            pass

        # --- Rightsizing Recommendations (per region) ---
        for region in self.target_regions:
            try:
                token = None
                while True:
                    kwargs = {
                        "Service": "AmazonEC2",
                        "Configuration": {"BenefitsConsidered": True},
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
            except Exception:
                continue

        return []  # meta-collector: findings exposed via get_findings()

    def _purchase_finding(self, detail: dict, spp_type: str) -> Finding:
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

    def get_findings(self) -> list[Finding]:
        return getattr(self, "_findings", [])