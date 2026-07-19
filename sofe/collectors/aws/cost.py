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
        """Query Cost Explorer for per-resource costs (last 7 days, extrapolated to monthly).
        
        Returns empty list — this collector enriches, doesn't create resources.
        Call get_cost_map() after collect() to get the cost data.
        """
        try:
            # Cost Explorer API is global (only us-east-1)
            ce = self.session.client("ce", region_name="us-east-1")

            end = datetime.utcnow().date()
            start = end - timedelta(days=7)

            response = ce.get_cost_and_usage_with_resources(
                TimePeriod={
                    "Start": start.isoformat(),
                    "End": end.isoformat(),
                },
                Granularity="DAILY",
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

            # Parse costs per resource ID (sum all days, then extrapolate to monthly)
            self._cost_map: dict[str, float] = {}
            for result in response.get("ResultsByTime", []):
                for group in result.get("Groups", []):
                    resource_id = group["Keys"][0]
                    amount = float(group["Metrics"]["UnblendedCost"]["Amount"])
                    if amount > 0:
                        self._cost_map[resource_id] = self._cost_map.get(resource_id, 0) + amount

            # Handle pagination
            while response.get("NextPageToken"):
                response = ce.get_cost_and_usage_with_resources(
                    TimePeriod={"Start": start.isoformat(), "End": end.isoformat()},
                    Granularity="DAILY",
                    Metrics=["UnblendedCost"],
                    GroupBy=[{"Type": "DIMENSION", "Key": "RESOURCE_ID"}],
                    Filter={"Not": {"Dimensions": {"Key": "RECORD_TYPE", "Values": ["Credit", "Refund", "Tax"]}}},
                    NextPageToken=response["NextPageToken"],
                )
                for result in response.get("ResultsByTime", []):
                    for group in result.get("Groups", []):
                        resource_id = group["Keys"][0]
                        amount = float(group["Metrics"]["UnblendedCost"]["Amount"])
                        if amount > 0:
                            self._cost_map[resource_id] = self._cost_map.get(resource_id, 0) + amount

            # Extrapolate 7-day cost to monthly (x 30/7 ≈ x 4.29)
            # Only keep resources with meaningful cost (> $0.001/month)
            monthly_map: dict[str, float] = {}
            for rid, cost_7d in self._cost_map.items():
                monthly = cost_7d * (30 / 7)
                if monthly > 0.001:
                    monthly_map[rid] = round(monthly, 2)
            self._cost_map = monthly_map

            # Build reverse index: extract short resource IDs from ARNs
            self._reverse_index: dict[str, float] = {}
            for arn, cost in self._cost_map.items():
                short_id = self._extract_resource_id(arn)
                if short_id and short_id != arn:
                    self._reverse_index[short_id] = self._reverse_index.get(short_id, 0) + cost

        except Exception as e:
            # Cost Explorer not enabled, no permissions, or other error
            # Graceful fallback — evaluation still works without cost data
            self._cost_map = {}
            import sys
            print(f"  ⚠️ Cost collector error: {e}", file=sys.stderr)

        return []  # This collector doesn't create resources

    def get_cost_map(self) -> dict[str, float]:
        """Return the cost map after collect() has been called."""
        return getattr(self, "_cost_map", {})

    def get_cost_for_resource(self, resource_id: str) -> float | None:
        """Lookup cost for a specific resource ID or ARN.
        
        Uses multi-strategy matching:
        1. Exact match in cost_map (full ARN)
        2. Reverse index lookup (short ID extracted from ARN)
        3. Fuzzy: resource_id contained in ARN key
        """
        cost_map = self.get_cost_map()
        if not cost_map:
            return None

        # Strategy 1: Exact match (resource_id is already a full ARN)
        if resource_id in cost_map:
            return cost_map[resource_id]

        # Strategy 2: Reverse index (short ID → cost)
        reverse = getattr(self, "_reverse_index", {})
        if resource_id in reverse:
            return round(reverse[resource_id], 2)

        # Strategy 3: Fuzzy match — resource_id contained in ARN
        for arn, cost in cost_map.items():
            # Match function:NAME, table/NAME, bucket NAME, instance/ID, key/ID
            if (arn.endswith(f"/{resource_id}") or 
                arn.endswith(f":{resource_id}") or
                arn.endswith(f":::{resource_id}") or
                f"function:{resource_id}" in arn or
                f"table/{resource_id}" in arn or
                f"instance/{resource_id}" in arn or
                f"volume/{resource_id}" in arn or
                f"snapshot/{resource_id}" in arn or
                f"distribution/{resource_id}" in arn or
                f"repository/{resource_id}" in arn or
                f"cluster/{resource_id}" in arn or
                f"key/{resource_id}" in arn or
                f"secret:{resource_id}" in arn or
                f"endpoint/{resource_id}" in arn):
                return round(cost, 2)

        return None

    @staticmethod
    def _extract_resource_id(arn: str) -> str:
        """Extract the short resource ID from a full ARN.
        
        Examples:
          arn:aws:lambda:us-east-1:123:function:my-func → my-func
          arn:aws:dynamodb:us-east-1:123:table/books → books
          arn:aws:s3:::my-bucket → my-bucket
          arn:aws:ec2:us-east-1:123:instance/i-0abc → i-0abc
          arn:aws:ec2:us-east-1:123:snapshot/snap-abc → snap-abc
          arn:aws:ecr:us-east-1:123:repository/my-repo → my-repo
          arn:aws:cloudfront::123:distribution/EABC → EABC
          arn:aws:kms:us-west-2:123:key/uuid → uuid
          arn:aws:secretsmanager:us-east-1:123:secret:name-abc → name
        """
        if not arn.startswith("arn:"):
            # Not an ARN — might be a bucket name or NoResourceId
            return arn

        parts = arn.split(":")
        if len(parts) < 6:
            return arn

        # S3: arn:aws:s3:::bucket-name
        if parts[2] == "s3" and len(parts) >= 6:
            return parts[5]  # bucket name

        # Get the resource part (everything after the 6th colon or the slash-separated part)
        resource_part = ":".join(parts[5:]) if len(parts) > 6 else parts[5]

        # Lambda: function:name
        if "function:" in resource_part:
            return resource_part.split("function:")[-1]

        # Most services use / separator: table/name, instance/id, etc.
        if "/" in resource_part:
            return resource_part.split("/")[-1]

        # Secrets Manager: secret:name-randomsuffix (strip the 6-char random suffix)
        if parts[2] == "secretsmanager" and "secret:" in resource_part:
            secret_name = resource_part.split("secret:")[-1]
            # Remove the -XXXXXX suffix AWS adds
            if "-" in secret_name and len(secret_name.split("-")[-1]) == 6:
                return "-".join(secret_name.split("-")[:-1])
            return secret_name

        # Colon-separated: key:id, log-group:name
        if ":" in resource_part:
            return resource_part.split(":")[-1]

        return resource_part

    def get_total_cost(self) -> float:
        """Total monthly cost across all resources."""
        return round(sum(self.get_cost_map().values()), 2)

    def get_top_costs(self, n: int = 10) -> list[tuple[str, float]]:
        """Top N most expensive resources."""
        return sorted(self.get_cost_map().items(), key=lambda x: x[1], reverse=True)[:n]
