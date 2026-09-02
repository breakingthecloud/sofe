"""Commitment collector — Savings Plans & RI coverage/utilization.

S081 — requires these IAM actions on the SOFE reader role (CloudFormation stack):
  - ce:GetSavingsPlansUtilization
  - ce:GetSavingsPlansCoverage
  - ce:GetReservationUtilization

Graceful fallback: if CE not enabled / no permissions, all metrics = None (evaluation still works).
CE API is global (us-east-1 only).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from ..aws.base import BaseCollector
from ...models import Resource


class CommitmentsCollector(BaseCollector):
    """Meta-collector that queries Cost Explorer for commitment coverage.

    Does NOT create resources — enriches account-level metrics.
    Must run once per evaluation (cached). CE is us-east-1 only.
    """

    resource_type = "aws.commitments"

    def collect(self) -> list[Resource]:
        self._commitments: dict[str, float | None] = {}

        try:
            ce = self.session.client("ce", region_name="us-east-1")
        except Exception:
            self._commitments = {
                "sp_coverage_pct": None,
                "sp_utilization_pct": None,
                "ri_utilization_pct": None,
            }
            return []

        # --- Savings Plans Utilization ---
        try:
            resp = ce.get_savings_plans_utilization(
                TimePeriod=self._period(),
                Granularity="MONTHLY",
            )
            agg = resp.get("SavingsPlansUtilizationsByTime", [{}])[0].get(
                "SavingsPlansUtilization", {}
            )
            self._commitments["sp_utilization_pct"] = float(
                agg.get("UtilizationPercentage", 0) or 0
            )
        except Exception:
            self._commitments["sp_utilization_pct"] = None

        # --- Savings Plans Coverage ---
        try:
            resp = ce.get_savings_plans_coverage(
                TimePeriod=self._period(),
                Granularity="MONTHLY",
            )
            # Coverage response: SavingsPlansCoverages -> list with SavingsPlansCoverage
            coverages = resp.get("SavingsPlansCoverages", [])
            if coverages:
                agg = coverages[0].get("SavingsPlansCoverage", {})
                self._commitments["sp_coverage_pct"] = float(
                    agg.get("CoveragePercentage", 0) or 0
                )
            else:
                # Alternative shape: directly SavingsPlansCoverage
                agg = resp.get("SavingsPlansCoverage", {})
                self._commitments["sp_coverage_pct"] = float(
                    agg.get("CoveragePercentage", 0) or 0
                ) if agg else None
        except Exception:
            self._commitments["sp_coverage_pct"] = None

        # --- Reserved Instance Utilization ---
        try:
            resp = ce.get_reservation_utilization(
                TimePeriod=self._period(),
                Granularity="MONTHLY",
            )
            # RI utilization: UtilizationsByTime -> Utilization
            utils = resp.get("UtilizationsByTime", [])
            if utils:
                agg = utils[0].get("Utilization", {})
                self._commitments["ri_utilization_pct"] = float(
                    agg.get("UtilizationPercentage", 0) or 0
                )
            else:
                # Alternative shape: ReservationUtilization or Total
                total = resp.get("Total", {}) or resp.get("Utilization", {})
                self._commitments["ri_utilization_pct"] = float(
                    total.get("UtilizationPercentage", 0) or 0
                ) if total else None
        except Exception:
            self._commitments["ri_utilization_pct"] = None

        return []

    def _period(self) -> dict:
        end = datetime.utcnow().date()
        start = end - timedelta(days=30)
        return {"Start": start.isoformat(), "End": end.isoformat()}

    def get_metrics(self) -> dict[str, float | None]:
        return getattr(self, "_commitments", {})
