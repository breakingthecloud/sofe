"""Cost Explorer collector — fetches real monthly costs per resource from AWS."""

from __future__ import annotations
from datetime import datetime, timedelta
from ..aws.base import BaseCollector
from ...models import Resource


class CostCollector(BaseCollector):
    """Meta-collector that queries Cost Explorer for per-resource costs.
    
    Does NOT create resources — enriches existing ones with monthly_cost metric.
    Must run AFTER all other collectors have finished.
    """

    resource_type = "aws.cost"

    def collect(self) -> list[Resource]:
        """Query Cost Explorer for per-resource costs (last 30 days).
        
        Returns empty list — this collector enriches, doesn't create resources.
        Call get_cost_map() after collect() to get the cost data.
        """
        try:
            # Cost Explorer API is global (only us-east-1)
            ce = self.session.client("ce", region_name="us-east-1")

            end = datetime.utcnow().date()
            start = end - timedelta(days=30)

            response = ce.get_cost_and_usage_with_resources(
                TimePeriod={
                    "Start": start.isoformat(),
                    "End": end.isoformat(),
                },
                Granularity="MONTHLY",
                Metrics=["UnblendedCost"],
                GroupBy=[
                    {"Type": "DIMENSION", "Key": "RESOURCE_ID"},
                ],
                Filter={
                    "Not": {
                        "Dimensions": {
                            "Key": "RECORD_TYPE",
                            "Values": ["Credit", "Refund", "Tax"],
                        }
                    }
                },
            )

            # Parse costs per resource ID
            self._cost_map: dict[str, float] = {}
            for result in response.get("ResultsByTime", []):
                for group in result.get("Groups", []):
                    resource_id = group["Keys"][0]
                    amount = float(group["Metrics"]["UnblendedCost"]["Amount"])
                    if amount > 0.01:  # Ignore sub-penny costs
                        self._cost_map[resource_id] = self._cost_map.get(resource_id, 0) + round(amount, 2)

            # Handle pagination
            while response.get("NextPageToken"):
                response = ce.get_cost_and_usage_with_resources(
                    TimePeriod={"Start": start.isoformat(), "End": end.isoformat()},
                    Granularity="MONTHLY",
                    Metrics=["UnblendedCost"],
                    GroupBy=[{"Type": "DIMENSION", "Key": "RESOURCE_ID"}],
                    Filter={"Not": {"Dimensions": {"Key": "RECORD_TYPE", "Values": ["Credit", "Refund", "Tax"]}}},
                    NextPageToken=response["NextPageToken"],
                )
                for result in response.get("ResultsByTime", []):
                    for group in result.get("Groups", []):
                        resource_id = group["Keys"][0]
                        amount = float(group["Metrics"]["UnblendedCost"]["Amount"])
                        if amount > 0.01:
                            self._cost_map[resource_id] = self._cost_map.get(resource_id, 0) + round(amount, 2)

        except Exception as e:
            # Cost Explorer not enabled, no permissions, or other error
            # Graceful fallback — evaluation still works without cost data
            self._cost_map = {}

        return []  # This collector doesn't create resources

    def get_cost_map(self) -> dict[str, float]:
        """Return the cost map after collect() has been called."""
        return getattr(self, "_cost_map", {})

    def get_cost_for_resource(self, resource_id: str) -> float | None:
        """Lookup cost for a specific resource ID or ARN.
        
        Handles fuzzy matching since Cost Explorer uses full ARNs
        but SOFE resources might use short IDs.
        """
        cost_map = self.get_cost_map()
        if not cost_map:
            return None

        # Exact match first
        if resource_id in cost_map:
            return cost_map[resource_id]

        # Fuzzy: resource_id might be contained in an ARN key
        for arn, cost in cost_map.items():
            if resource_id in arn or arn.endswith(f"/{resource_id}") or arn.endswith(f":{resource_id}"):
                return cost

        return None

    def get_total_cost(self) -> float:
        """Total monthly cost across all resources."""
        return round(sum(self.get_cost_map().values()), 2)

    def get_top_costs(self, n: int = 10) -> list[tuple[str, float]]:
        """Top N most expensive resources."""
        return sorted(self.get_cost_map().items(), key=lambda x: x[1], reverse=True)[:n]
