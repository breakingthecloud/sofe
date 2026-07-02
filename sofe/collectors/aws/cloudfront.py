"""AWS CloudFront collector — distributions."""
from .base import BaseCollector
from ...models import Resource

class CloudFrontCollector(BaseCollector):
    resource_type = "aws.cloudfront"
    def collect(self) -> list[Resource]:
        try:
            client = self.session.client('cloudfront')
            dists = client.list_distributions().get('DistributionList', {}).get('Items', [])
            return [self._make_resource(
                resource_id=d['Id'],
                properties={'domain': d.get('DomainName'), 'status': d.get('Status'), 'origins': len(d.get('Origins', {}).get('Items', []))},
            ) for d in (dists or [])]
        except Exception as e:
            print(f"  ⚠️  CloudFront scan failed: {e}")
            return []
