"""AWS resource collectors for SOFE — scan resources + fetch metrics."""

import boto3
from ..models import Resource


def collect_all(profile: str = None, resource_types: list[str] = None, regions: list[str] = None) -> list[Resource]:
    """Collect resources from AWS using the given profile."""
    session = boto3.Session(profile_name=profile) if profile else boto3.Session()
    account_id = session.client('sts').get_caller_identity()['Account']
    target_regions = regions or [session.region_name or 'us-east-1']

    resources: list[Resource] = []
    types = resource_types or ['aws.ec2', 'aws.s3', 'aws.lambda', 'aws.rds']

    for region in target_regions:
        if 'aws.ec2' in types:
            resources.extend(_collect_ec2(session, region, account_id))
        if 'aws.s3' in types:
            resources.extend(_collect_s3(session, region, account_id))
        if 'aws.lambda' in types:
            resources.extend(_collect_lambda(session, region, account_id))
        if 'aws.rds' in types:
            resources.extend(_collect_rds(session, region, account_id))

    # Fetch metrics for collected resources
    _enrich_metrics(session, resources, target_regions[0])

    return resources


def _collect_ec2(session: boto3.Session, region: str, account_id: str) -> list[Resource]:
    try:
        ec2 = session.client('ec2', region_name=region)
        resp = ec2.describe_instances(Filters=[{'Name': 'instance-state-name', 'Values': ['running']}])
        resources = []
        for res in resp.get('Reservations', []):
            for inst in res.get('Instances', []):
                tags = {t['Key']: t['Value'] for t in inst.get('Tags', [])}
                resources.append(Resource(
                    resource_id=inst['InstanceId'],
                    resource_type='aws.ec2',
                    region=region,
                    account_id=account_id,
                    tags=tags,
                    properties={'instance_type': inst.get('InstanceType'), 'launch_time': str(inst.get('LaunchTime', ''))},
                ))
        return resources
    except Exception as e:
        print(f"  ⚠️  EC2 scan failed in {region}: {e}")
        return []


def _collect_s3(session: boto3.Session, region: str, account_id: str) -> list[Resource]:
    try:
        s3 = session.client('s3', region_name=region)
        buckets = s3.list_buckets().get('Buckets', [])
        return [Resource(
            resource_id=b['Name'],
            resource_type='aws.s3',
            region='global',
            account_id=account_id,
            properties={'creation_date': str(b.get('CreationDate', ''))},
        ) for b in buckets]
    except Exception as e:
        print(f"  ⚠️  S3 scan failed: {e}")
        return []


def _collect_lambda(session: boto3.Session, region: str, account_id: str) -> list[Resource]:
    try:
        client = session.client('lambda', region_name=region)
        functions = client.list_functions().get('Functions', [])
        return [Resource(
            resource_id=fn['FunctionName'],
            resource_type='aws.lambda',
            region=region,
            account_id=account_id,
            properties={'runtime': fn.get('Runtime'), 'memory': fn.get('MemorySize')},
        ) for fn in functions]
    except Exception as e:
        print(f"  ⚠️  Lambda scan failed in {region}: {e}")
        return []


def _collect_rds(session: boto3.Session, region: str, account_id: str) -> list[Resource]:
    try:
        client = session.client('rds', region_name=region)
        instances = client.describe_db_instances().get('DBInstances', [])
        return [Resource(
            resource_id=db['DBInstanceIdentifier'],
            resource_type='aws.rds',
            region=region,
            account_id=account_id,
            properties={'engine': db.get('Engine'), 'class': db.get('DBInstanceClass')},
        ) for db in instances]
    except Exception as e:
        print(f"  ⚠️  RDS scan failed in {region}: {e}")
        return []


def _enrich_metrics(session: boto3.Session, resources: list[Resource], region: str):
    """Fetch CloudWatch metrics for resources (CPU, cost)."""
    from datetime import datetime, timedelta
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

            # Running days
            if 'launch_time' in r.properties and r.properties['launch_time']:
                try:
                    from dateutil.parser import parse
                    launch = parse(r.properties['launch_time'])
                    r.metrics['running_days'] = (now - launch.replace(tzinfo=None)).days
                except:
                    pass

        # Monthly cost (simplified: total / resource count as estimate)
        try:
            s = (now.replace(day=1) - timedelta(days=1)).replace(day=1).strftime('%Y-%m-%d')
            e = now.replace(day=1).strftime('%Y-%m-%d')
            resp = ce.get_cost_and_usage(TimePeriod={'Start': s, 'End': e}, Granularity='MONTHLY', Metrics=['UnblendedCost'])
            total = float(resp['ResultsByTime'][0]['Total']['UnblendedCost']['Amount'])
            if resources:
                per_resource = total / len(resources)
                for r in resources:
                    r.metrics['monthly_cost'] = round(per_resource, 2)
        except:
            pass
    except:
        pass
