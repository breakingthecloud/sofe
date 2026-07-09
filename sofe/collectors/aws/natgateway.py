"""AWS NAT Gateway collector — gateways with connectivity, state metrics."""

from .base import BaseCollector
from ...models import Resource


class NATGatewayCollector(BaseCollector):
    resource_type = "aws.natgateway"

    def collect(self) -> list[Resource]:
        try:
            ec2 = self.session.client('ec2', region_name=self.region)
            gateways = ec2.describe_nat_gateways(
                Filter=[{'Name': 'state', 'Values': ['available']}]
            ).get('NatGateways', [])
            resources = []
            for gw in gateways:
                tags = {t['Key']: t['Value'] for t in gw.get('Tags', [])}
                connectivity = gw.get('ConnectivityType', 'public')

                resources.append(self._make_resource(
                    resource_id=gw['NatGatewayId'],
                    tags=tags,
                    properties={
                        'state': gw.get('State'),
                        'connectivity_type': connectivity,
                        'subnet_id': gw.get('SubnetId'),
                        'vpc_id': gw.get('VpcId'),
                    },
                    metrics={
                        'connectivity_public': 1.0 if connectivity == 'public' else 0.0,
                    },
                ))
            return resources
        except Exception as e:
            print(f"  ⚠️  NAT Gateway scan failed in {self.region}: {e}")
            return []
