"""AWS Route53 collector — hosted zones with record count metrics."""

from .base import BaseCollector
from ...models import Resource


class Route53Collector(BaseCollector):
    resource_type = "aws.route53"

    def collect(self) -> list[Resource]:
        try:
            client = self.session.client('route53')  # Route53 is global
            zones = client.list_hosted_zones().get('HostedZones', [])
            resources = []
            for zone in zones:
                zone_id = zone['Id'].split('/')[-1]
                name = zone['Name'].rstrip('.')
                record_count = zone.get('ResourceRecordSetCount', 0)
                private = zone.get('Config', {}).get('PrivateZone', False)

                resources.append(self._make_resource(
                    resource_id=name,
                    properties={
                        'zone_id': zone_id,
                        'record_count': record_count,
                        'private_zone': private,
                    },
                    metrics={
                        'record_count': float(record_count),
                        'private_zone': 1.0 if private else 0.0,
                    },
                ))
            return resources
        except Exception as e:
            print(f"  ⚠️  Route53 scan failed: {e}")
            return []
