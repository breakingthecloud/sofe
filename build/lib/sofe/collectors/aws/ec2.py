"""AWS EC2 collector — running instances with extended metrics."""

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
                    instance_type = inst.get('InstanceType', '')
                    # Determine instance generation (t2=old, t3/m5=current, t4/m6/m7=latest)
                    family = instance_type.split('.')[0] if instance_type else ''
                    gen_num = int(''.join(c for c in family if c.isdigit()) or '0')
                    generation = 'latest' if gen_num >= 6 else ('current' if gen_num >= 3 else 'old')

                    resources.append(self._make_resource(
                        resource_id=inst['InstanceId'],
                        tags=tags,
                        properties={
                            'instance_type': instance_type,
                            'launch_time': str(inst.get('LaunchTime', '')),
                            'state': inst.get('State', {}).get('Name'),
                            'purchase_option': 'spot' if inst.get('InstanceLifecycle') == 'spot' else 'on-demand',
                            'instance_generation': generation,
                            'ebs_optimized': inst.get('EbsOptimized', False),
                            'public_ip': inst.get('PublicIpAddress'),
                            'platform': inst.get('Platform', 'linux'),
                        },
                        metrics={
                            'purchase_option_spot': 1.0 if inst.get('InstanceLifecycle') == 'spot' else 0.0,
                            'instance_generation_old': 1.0 if generation == 'old' else 0.0,
                            'ebs_optimized': 1.0 if inst.get('EbsOptimized', False) else 0.0,
                            'public_ip_attached': 1.0 if inst.get('PublicIpAddress') else 0.0,
                        },
                    ))
            return resources
        except Exception as e:
            print(f"  ⚠️  EC2 scan failed in {self.region}: {e}")
            return []
