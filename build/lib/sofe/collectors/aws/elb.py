"""AWS ELB/ALB collector — load balancers."""
from .base import BaseCollector
from ...models import Resource

class ELBCollector(BaseCollector):
    resource_type = "aws.elb"
    def collect(self) -> list[Resource]:
        try:
            client = self.session.client('elbv2', region_name=self.region)
            lbs = client.describe_load_balancers().get('LoadBalancers', [])
            resources = []
            for lb in lbs:
                # Check if has targets (idle detection)
                arn = lb['LoadBalancerArn']
                tgs = client.describe_target_groups(LoadBalancerArn=arn).get('TargetGroups', [])
                has_targets = any(tg.get('TargetType') for tg in tgs)
                resources.append(self._make_resource(
                    resource_id=lb['LoadBalancerName'],
                    properties={'type': lb.get('Type'), 'scheme': lb.get('Scheme'), 'state': lb.get('State', {}).get('Code')},
                    metrics={'has_targets': 1.0 if has_targets else 0.0},
                ))
            return resources
        except Exception as e:
            print(f"  ⚠️  ELB scan failed in {self.region}: {e}")
            return []
