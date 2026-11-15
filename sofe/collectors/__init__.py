"""AWS resource collectors for SOFE — scan resources + fetch metrics."""

from __future__ import annotations
import sys
import boto3
from datetime import datetime, timedelta
from ..models import Resource
from .aws import COLLECTORS, ALL_TYPES

# S082 — recommendation findings collected during the last collect_all() run
_RECOMMENDATION_FINDINGS: list = []


# Meta-collectors: they enrich resources / expose account-level data, they do not inventory
# anything. They run ONCE through _enrich_costs / _enrich_commitments, never in the resource loop
# (running them in the loop too doubled every Cost Explorer request, and once per region).
_META_TYPES = frozenset({"aws.cost", "aws.commitments"})

# Cost Explorer API requests per evaluation (AWS bills ~$0.01 per request), assuming no pagination:
#   cost 1 + commitments 3 (SP utilization, SP coverage, RI utilization)
#   + recommendations 2 (Savings Plans COMPUTE / EC2_INSTANCE) + 1 rightsizing per region
_CE_REQUESTS_FIXED = 6


def expected_ce_requests(regions: int = 1) -> int:
    """Minimum Cost Explorer requests one evaluation makes (more if a response is paginated)."""
    return _CE_REQUESTS_FIXED + max(1, regions)


CE_REQUESTS_PER_EVALUATION = expected_ce_requests(1)  # 7 for the default single region

# cost_status becomes "ok" when at least this share of the account's spend is matched to inventoried resources
COST_OK_MIN_COVERAGE_USD = 0.95


def get_recommendations() -> list:
    """Return recommendation findings (S082) from the last collect_all() run."""
    return _RECOMMENDATION_FINDINGS


def collect_all(
    profile: str = None,
    resource_types: list[str] = None,
    regions: list[str] = None,
    session: boto3.Session | None = None,
) -> list[Resource]:
    """Collect resources from AWS using modular collectors.

    Credentials come from, in order of precedence:
      1. ``session`` — an already-built boto3 session (e.g. temporary credentials
         from an assumed role, S111). Used as-is; ``profile`` is ignored.
      2. ``profile`` — a named local AWS profile.
      3. The default boto3 credential chain.
    """
    global _RECOMMENDATION_FINDINGS
    if session is None:
        session = boto3.Session(profile_name=profile) if profile else boto3.Session()
    account_id = session.client('sts').get_caller_identity()['Account']
    target_regions = regions or [session.region_name or 'us-east-1']
    types = resource_types or ALL_TYPES

    resources: list[Resource] = []

    for region in target_regions:
        for rtype in types:
            if rtype in COLLECTORS and rtype not in _META_TYPES:
                collector = COLLECTORS[rtype](session=session, region=region, account_id=account_id)
                collected = collector.collect()
                resources.extend(collected)
                if collected:
                    print(f"  ✓ {rtype}: {len(collected)} resources in {region}", file=sys.stderr)

    # Enrich with metrics
    _enrich_metrics(session, resources, target_regions[0])

    # Enrich with real costs from Cost Explorer (never invents a number; reports why when it can't)
    cost_summary = _enrich_costs(session, resources, account_id)

    # Enrich with commitment coverage/utilization (SP + RI)
    commit_metrics = _enrich_commitments(session, resources, account_id)

    # S082 — collect savings/rightsizing recommendations (findings)
    _enrich_recommendations(session, account_id, target_regions)

    # Tag-based metrics
    for r in resources:
        for key in ['owner', 'env', 'costCenter', 'Environment', 'Name']:
            r.metrics[f'has_tag:{key}'] = 1.0 if key in r.tags else 0.0

    # S083 — synthetic account resource (account-level metrics for savings policies)
    account_resource = _build_account_resource(account_id, commit_metrics, target_regions[0], cost_summary)
    resources.append(account_resource)

    return resources


def _enrich_metrics(session: boto3.Session, resources: list[Resource], region: str):
    """Fetch CloudWatch metrics for resources (CPU, running days).

    Cost is NOT handled here: dividing the account total equally among resources produced
    invented per-resource numbers (S111 B1). Real costs come from _enrich_costs().
    """
    try:
        cw = session.client('cloudwatch', region_name=region)
        now = datetime.utcnow()
        start = now - timedelta(days=30)

        for r in resources:
            if r.resource_type == 'aws.ec2':
                try:
                    resp = cw.get_metric_statistics(
                        Namespace='AWS/EC2', MetricName='CPUUtilization',
                        Dimensions=[{'Name': 'InstanceId', 'Value': r.resource_id}],
                        StartTime=start, EndTime=now, Period=86400 * 30, Statistics=['Average'],
                    )
                    if resp['Datapoints']:
                        r.metrics['avg_cpu_utilization'] = round(resp['Datapoints'][0]['Average'], 2)
                except:
                    pass

            # Running days for EC2
            if r.resource_type == 'aws.ec2' and 'launch_time' in r.properties and r.properties['launch_time']:
                try:
                    from dateutil.parser import parse
                    launch = parse(r.properties['launch_time'])
                    r.metrics['running_days'] = (now - launch.replace(tzinfo=None)).days
                except:
                    pass
    except:
        pass


def _enrich_costs(session: boto3.Session, resources: list[Resource], account_id: str) -> dict:
    """Enrich resources with real per-resource costs from Cost Explorer (S111 Phase B).

    Sets ``monthly_cost`` ONLY on resources that Cost Explorer matched. Everything else
    stays without the metric: policies then skip the rule instead of evaluating an
    invented number, and findings get no made-up ``estimated_savings``.

    Always returns a summary describing what happened (never raises):
      cost_status          ok | partial | access_denied | not_enabled | no_data | error
      cost_detail          short ARN-free reason when there is no (or partial) data
      cost_coverage        matched resources / inventoried resources            (None if no data)
      cost_coverage_usd    matched spend / account spend in Cost Explorer       (None if no data)
      cost_account_monthly Cost Explorer monthly spend of THIS account          (None if no data)
      cost_matched_monthly sum of monthly_cost over the matched resources
    """
    from .aws.cost import CostCollector, COST_OK, COST_PARTIAL

    summary: dict = {
        "cost_status": "error",
        "cost_detail": "",
        "cost_coverage": None,
        "cost_coverage_usd": None,
        "cost_account_monthly": None,
        "cost_matched_monthly": 0.0,
    }
    try:
        collector = CostCollector(session=session, region="us-east-1", account_id=account_id)
        collector.collect()
        summary["cost_status"] = collector.get_status()
        summary["cost_detail"] = collector.get_detail()

        cost_map = collector.get_cost_map()
        if not cost_map:
            if summary["cost_status"] == "no_data":
                summary["cost_account_monthly"] = 0.0  # the call worked: the account has no spend
            return summary  # access_denied / not_enabled / error / no_data: no per-resource cost

        enriched, matched = 0, 0.0
        for r in resources:
            cost = collector.get_cost_for_resource(r.resource_id)
            if cost is not None:
                r.metrics["monthly_cost"] = cost
                enriched += 1
                matched += cost

        total = collector.get_total_cost()
        coverage_usd = min(matched / total, 1.0) if total > 0 else None
        summary.update(
            cost_coverage=round(enriched / len(resources), 4) if resources else None,
            cost_coverage_usd=round(coverage_usd, 4) if coverage_usd is not None else None,
            cost_account_monthly=total,
            cost_matched_monthly=round(matched, 2),
            cost_status=COST_OK if (coverage_usd or 0) >= COST_OK_MIN_COVERAGE_USD else COST_PARTIAL,
        )
        if summary["cost_status"] == COST_PARTIAL:
            summary["cost_detail"] = "part of the spend belongs to services/resources SOFE does not inventory"

        print(
            f"  💰 Cost data: {enriched}/{len(resources)} resources matched "
            f"(${matched:.2f} of ${total:.2f}/mo account spend, status={summary['cost_status']})",
            file=sys.stderr,
        )
    except Exception as e:  # defensive: evaluation must work without cost data
        from .aws.cost import classify_ce_error
        summary["cost_status"], summary["cost_detail"] = classify_ce_error(e)
    return summary


def _enrich_commitments(session: boto3.Session, resources: list[Resource], account_id: str) -> dict[str, float | None]:
    """Enrich resources with commitment coverage/utilization from Cost Explorer.

    Returns the commitment metrics dict (S081) for account-level aggregation.
    """
    from .aws.commitments import CommitmentsCollector

    try:
        collector = CommitmentsCollector(session=session, region="us-east-1", account_id=account_id)
        collector.collect()
        metrics = collector.get_metrics()

        # Attach non-None metrics to each resource (account-level, but queryable per-resource)
        for r in resources:
            for k, v in metrics.items():
                if v is not None:
                    r.metrics[k] = v

        # Log once (if any metric available)
        shown = {k: v for k, v in metrics.items() if v is not None}
        if shown:
            print(f"  📊 Commitments: " + ", ".join(f"{k}={v:.1f}%" for k, v in shown.items()), file=sys.stderr)

        return metrics

    except Exception:
        return {}


def _build_account_resource(
    account_id: str,
    commit_metrics: dict[str, float | None],
    region: str,
    cost_summary: dict | None = None,
) -> Resource:
    """Synthetic account-level resource (S083) carrying savings metrics for policies.

    Policies scope to resource_types: [aws.account] to evaluate the account ONCE.
    The cost summary (S111) travels in ``properties`` (not metrics: it holds strings), so a
    caller reads it from the returned list instead of a module-level global shared by threads.
    """
    metrics: dict[str, float] = {}
    for k, v in commit_metrics.items():
        if v is not None:
            metrics[k] = v
    potential = sum(f.estimated_savings or 0 for f in get_recommendations())
    metrics["potential_savings_monthly"] = round(potential, 2)

    return Resource(
        resource_id=f"account-{account_id}",
        resource_type="aws.account",
        region=region,
        account_id=account_id,
        tags={},
        properties=dict(cost_summary or {}),
        metrics=metrics,
    )


def _enrich_recommendations(session: boto3.Session, account_id: str, target_regions: list[str]):
    """Collect savings/rightsizing recommendation findings (S082)."""
    global _RECOMMENDATION_FINDINGS
    from .aws.recommendations import RecommendationsCollector

    try:
        collector = RecommendationsCollector(
            session=session,
            region="us-east-1",
            account_id=account_id,
            target_regions=target_regions,
        )
        collector.collect()
        _RECOMMENDATION_FINDINGS = collector.get_findings()
        if _RECOMMENDATION_FINDINGS:
            print(f"  💡 Recommendations: {len(_RECOMMENDATION_FINDINGS)} savings/rightsizing findings", file=sys.stderr)
    except Exception:
        _RECOMMENDATION_FINDINGS = []
