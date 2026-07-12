"""AWS resource collectors for SOFE — scan resources + fetch metrics."""

from __future__ import annotations
import boto3
from datetime import datetime, timedelta
from ..models import Resource
from .aws import COLLECTORS, ALL_TYPES


def collect_all(profile: str = None, resource_types: list[str] = None, regions: list[str] = None) -> list[Resource]:
    """Collect resources from AWS using the given profile and modular collectors."""
    session = boto3.Session(profile_name=profile) if profile else boto3.Session()
    account_id = session.client('sts').get_caller_identity()['Account']
    target_regions = regions or [session.region_name or 'us-east-1']
    types = resource_types or ALL_TYPES

    resources: list[Resource] = []

    for region in target_regions:
        for rtype in types:
            if rtype in COLLECTORS:
                collector = COLLECTORS[rtype](session=session, region=region, account_id=account_id)
                collected = collector.collect()
                resources.extend(collected)
                if collected:
                    print(f"  ✓ {rtype}: {len(collected)} resources in {region}")

    # Enrich with metrics
    _enrich_metrics(session, resources, target_regions[0])

    # Enrich with real costs from Cost Explorer
    _enrich_costs(session, resources, account_id)

    # Tag-based metrics
    for r in resources:
        for key in ['owner', 'env', 'costCenter', 'Environment', 'Name']:
            r.metrics[f'has_tag:{key}'] = 1.0 if key in r.tags else 0.0

    return resources


def _enrich_metrics(session: boto3.Session, resources: list[Resource], region: str):
    """Fetch CloudWatch metrics for resources (CPU, cost)."""
    try:
        cw = session.client('cloudwatch', region_name=region)
        ce = session.client('ce', region_name='us-east-1')
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

        # Monthly cost per resource — now handled by _enrich_costs() via CostCollector
        # (keeping this as fallback for accounts without ce:GetCostAndUsageWithResources)
        try:
            s = (now.replace(day=1) - timedelta(days=1)).replace(day=1).strftime('%Y-%m-%d')
            e = now.replace(day=1).strftime('%Y-%m-%d')
            resp = ce.get_cost_and_usage(TimePeriod={'Start': s, 'End': e}, Granularity='MONTHLY', Metrics=['UnblendedCost'])
            total = float(resp['ResultsByTime'][0]['Total']['UnblendedCost']['Amount'])
            if resources and not any(r.metrics.get('monthly_cost') for r in resources):
                # Only apply naïve distribution if CostCollector didn't run
                per_resource = total / len(resources)
                for r in resources:
                    r.metrics.setdefault('monthly_cost', round(per_resource, 2))
        except:
            pass
    except:
        pass


def _enrich_costs(session: boto3.Session, resources: list[Resource], account_id: str):
    """Enrich resources with real per-resource costs from Cost Explorer."""
    from .aws.cost import CostCollector

    try:
        cost_collector = CostCollector(session=session, region="us-east-1", account_id=account_id)
        cost_collector.collect()
        cost_map = cost_collector.get_cost_map()

        if not cost_map:
            return  # No cost data available (permission denied or CE not enabled)

        enriched = 0
        for r in resources:
            cost = cost_collector.get_cost_for_resource(r.resource_id)
            if cost is not None:
                r.metrics['monthly_cost'] = cost
                enriched += 1

        if enriched:
            print(f"  💰 Cost data: {enriched}/{len(resources)} resources enriched (total: ${cost_collector.get_total_cost()}/mo)")

    except Exception:
        pass  # Graceful fallback — evaluation works without cost data
