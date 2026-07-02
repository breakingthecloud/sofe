"""AWS NAT Gateway collector — often expensive and overlooked."""
from .base import BaseCollector
from ...models import Resource

class NATGatewayCollector(BaseCollector):
    resource_type = "aws.natgateway"
    def collect(self) -> list[Resource]:
        try:
            ec2 = self.session.client('ec2', region_name=self.region)
            nats = ec2.describe_nat_gateways(Filter=[{'Name': 'state', 'Values': ['available']}]).get('NatGateways', [])
            return [self._make_resource(
                resource_id=n['NatGatewayId'],
                tags={t['Key']: t['Value'] for t in n.get('Tags', [])},
                properties={'subnet_id': n.get('SubnetId'), 'vpc_id': n.get('VpcId'), 'connectivity_type': n.get('ConnectivityType')},
            ) for n in nats]
        except Exception as e:
            print(f"  ⚠️  NAT Gateway scan failed in {self.region}: {e}")
            return []
