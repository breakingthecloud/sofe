"""AWS CloudFront collector — distributions with HTTPS, WAF, compression metrics."""

from .base import BaseCollector
from ...models import Resource


class CloudFrontCollector(BaseCollector):
    resource_type = "aws.cloudfront"

    def collect(self) -> list[Resource]:
        try:
            client = self.session.client('cloudfront', region_name='us-east-1')  # CloudFront is global
            distributions = client.list_distributions().get('DistributionList', {}).get('Items', [])
            resources = []
            for dist in distributions:
                dist_id = dist['Id']
                default_behavior = dist.get('DefaultCacheBehavior', {})
                viewer_policy = default_behavior.get('ViewerProtocolPolicy', '')
                compress = default_behavior.get('Compress', False)
                waf_id = dist.get('WebACLId', '')
                price_class = dist.get('PriceClass', 'PriceClass_All')

                # Tags
                tags = {}
                try:
                    tag_resp = client.list_tags_for_resource(Resource=dist['ARN'])
                    tags = {t['Key']: t['Value'] for t in tag_resp.get('Tags', {}).get('Items', [])}
                except:
                    pass

                resources.append(self._make_resource(
                    resource_id=dist_id,
                    tags=tags,
                    properties={
                        'domain': dist.get('DomainName'),
                        'status': dist.get('Status'),
                        'price_class': price_class,
                        'viewer_protocol_policy': viewer_policy,
                    },
                    metrics={
                        'compression_enabled': 1.0 if compress else 0.0,
                        'https_only': 1.0 if viewer_policy in ('redirect-to-https', 'https-only') else 0.0,
                        'waf_enabled': 1.0 if waf_id else 0.0,
                        'price_class_all': 1.0 if price_class == 'PriceClass_All' else 0.0,
                    },
                ))
            return resources
        except Exception as e:
            print(f"  ⚠️  CloudFront scan failed: {e}")
            return []
