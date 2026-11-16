"""Cost Explorer collector — fetches real monthly costs per resource from AWS.

S111 Phase B: the collector reports WHY it has (or has not) cost data
(``get_status()``), restricts the query to the evaluated account, and never invents
a number: a resource without a Cost Explorer match has no ``monthly_cost`` at all.
"""

from __future__ import annotations
import sys
from datetime import datetime, timedelta
from ..aws.base import BaseCollector
from ...billing.ce import (
    EXCLUDED_RECORD_TYPES,
    MAX_PAGES,
    account_cost_filter,
    classify_ce_error,
)
from ...billing.models import CostStatus
from ...models import Resource

# cost_status values (also exposed by sofe-server /evaluate)
COST_OK = CostStatus.ok.value
COST_PARTIAL = CostStatus.partial.value
COST_ACCESS_DENIED = CostStatus.access_denied.value
COST_NOT_ENABLED = CostStatus.not_enabled.value
COST_NO_DATA = CostStatus.no_data.value
COST_ERROR = CostStatus.error.value

COST_PERIOD_DAYS = 7                # Cost Explorer resource-level data: we read 7 days and extrapolate to 30


class CostCollector(BaseCollector):
    """Meta-collector that queries Cost Explorer for per-resource costs.

    Does NOT create resources — enriches existing ones with the monthly_cost metric.
    Must run AFTER all other collectors have finished.
    """

    resource_type = "aws.cost"

    def collect(self) -> list[Resource]:
        """Query Cost Explorer for per-resource costs (last 7 days, extrapolated to monthly).

        Returns an empty list — this collector enriches, it does not create resources.
        Afterwards use get_status() / get_detail() / get_cost_map().
        """
        self._cost_map: dict[str, float] = {}
        self._reverse_index: dict[str, float] = {}
        self._status = COST_NO_DATA
        self._detail = ""
        self._requests = 0

        try:
            # Cost Explorer API is global (only us-east-1)
            ce = self.session.client("ce", region_name="us-east-1")
            end = datetime.utcnow().date()
            start = end - timedelta(days=COST_PERIOD_DAYS)
            flt = self._filter()

            cost_7d: dict[str, float] = {}
            token = None
            for _ in range(MAX_PAGES):
                kwargs = dict(
                    TimePeriod={"Start": start.isoformat(), "End": end.isoformat()},
                    Granularity="DAILY",
                    Metrics=["UnblendedCost"],
                    GroupBy=[{"Type": "DIMENSION", "Key": "RESOURCE_ID"}],
                    Filter=flt,
                )
                if token:
                    kwargs["NextPageToken"] = token
                response = ce.get_cost_and_usage_with_resources(**kwargs)
                self._requests += 1
                for result in response.get("ResultsByTime", []):
                    for group in result.get("Groups", []):
                        amount = float(group["Metrics"]["UnblendedCost"]["Amount"])
                        if amount > 0:
                            rid = group["Keys"][0]
                            cost_7d[rid] = cost_7d.get(rid, 0) + amount
                token = response.get("NextPageToken")
                if not token:
                    break
            else:
                raise RuntimeError("Cost Explorer pagination limit reached")

            # Extrapolate the 7-day cost to a month (x 30/7). Keep only meaningful amounts (> $0.001/month).
            for rid, c in cost_7d.items():
                monthly = c * (30 / COST_PERIOD_DAYS)
                if monthly > 0.001:
                    self._cost_map[rid] = round(monthly, 2)

            # Reverse index: short resource ids extracted from ARNs
            for arn, cost in self._cost_map.items():
                short_id = self._extract_resource_id(arn)
                if short_id and short_id != arn:
                    self._reverse_index[short_id] = self._reverse_index.get(short_id, 0) + cost

            self._status = COST_OK if self._cost_map else COST_NO_DATA

        except Exception as e:
            self._cost_map, self._reverse_index = {}, {}
            status, detail = classify_ce_error(e)
            self._status, self._detail = status.value, detail
            print(f"  ⚠️ Cost Explorer unavailable: {self._status} ({self._detail})", file=sys.stderr)

        return []  # This collector doesn't create resources

    def _filter(self) -> dict:
        """Exclude credits/refunds/tax, and (S111) restrict to the evaluated account.

        Without LINKED_ACCOUNT, a management (payer) account receives the resources of
        EVERY member account, so same-named resources of other accounts could be
        matched against this account's inventory.
        """
        return account_cost_filter(self.account_id)

    def get_status(self) -> str:
        """cost_status after collect(): ok | no_data | access_denied | not_enabled | error."""
        return getattr(self, "_status", COST_NO_DATA)

    def get_detail(self) -> str:
        """Short, ARN-free reason when the status is not ok."""
        return getattr(self, "_detail", "")

    def request_count(self) -> int:
        """Cost Explorer requests made by collect() (each is billed ~$0.01)."""
        return getattr(self, "_requests", 0)

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
