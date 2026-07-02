"""AWS Route53 collector — hosted zones."""
from .base import BaseCollector
from ...models import Resource

class Route53Collector(BaseCollector):
    resource_type = "aws.route53"
    def collect(self) -> list[Resource]:
        try:
            client = self.session.client('route53')
            zones = client.list_hosted_zones().get('HostedZones', [])
            return [self._make_resource(
                resource_id=z['Id'].split('/')[-1],
                properties={'name': z.get('Name'), 'record_count': z.get('ResourceRecordSetCount'), 'private': z.get('Config', {}).get('PrivateZone', False)},
            ) for z in zones]
        except Exception as e:
            print(f"  ⚠️  Route53 scan failed: {e}")
            return []
