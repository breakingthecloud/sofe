"""AWS EC2 collector — running instances with CPU metrics."""

from .base import BaseCollector
from ...models import Resource


class EC2Collector(BaseCollector):
    resource_type = "aws.ec2"

    def collect(self) -> list[Resource]:
        try:
            ec2 = self.session.client('ec2', region_name=self.region)
            resp = ec2.describe_instances(Filters=[{'Name': 'instance-state-name', 'Values': ['running']}])
            resources = []
            for res in resp.get('Reservations', []):
                for inst in res.get('Instances', []):
                    tags = {t['Key']: t['Value'] for t in inst.get('Tags', [])}
                    resources.append(self._make_resource(
                        resource_id=inst['InstanceId'],
                        tags=tags,
                        properties={
                            'instance_type': inst.get('InstanceType'),
                            'launch_time': str(inst.get('LaunchTime', '')),
                            'state': inst.get('State', {}).get('Name'),
                        },
                    ))
            return resources
        except Exception as e:
            print(f"  ⚠️  EC2 scan failed in {self.region}: {e}")
            return []
