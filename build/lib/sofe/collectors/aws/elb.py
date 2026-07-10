"""AWS ELB/ALB collector — load balancers with target, WAF, scheme metrics."""
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
                arn = lb['LoadBalancerArn']
                tgs = client.describe_target_groups(LoadBalancerArn=arn).get('TargetGroups', [])
                has_targets = any(tg.get('TargetType') for tg in tgs)
                scheme = lb.get('Scheme', 'internal')
                # Check WAF
                waf_enabled = False
                try:
                    waf = self.session.client('wafv2', region_name=self.region)
                    waf_resp = waf.get_web_acl_for_resource(ResourceArn=arn)
                    waf_enabled = bool(waf_resp.get('WebACL'))
                except:
                    pass
                resources.append(self._make_resource(
                    resource_id=lb['LoadBalancerName'],
                    properties={'type': lb.get('Type'), 'scheme': scheme, 'state': lb.get('State', {}).get('Code')},
                    metrics={
                        'has_targets': 1.0 if has_targets else 0.0,
                        'waf_enabled': 1.0 if waf_enabled else 0.0,
                        'internet_facing': 1.0 if scheme == 'internet-facing' else 0.0,
                    },
                ))
            return resources
        except Exception as e:
            print(f"  ⚠️  ELB scan failed in {self.region}: {e}")
            return []
