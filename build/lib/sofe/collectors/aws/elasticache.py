"""AWS ElastiCache collector — Redis/Memcached clusters."""

from .base import BaseCollector
from ...models import Resource


class ElastiCacheCollector(BaseCollector):
    resource_type = "aws.elasticache"

    def collect(self) -> list[Resource]:
        try:
            client = self.session.client('elasticache', region_name=self.region)
            clusters = client.describe_cache_clusters(ShowCacheNodeInfo=True).get('CacheClusters', [])
            return [self._make_resource(
                resource_id=c['CacheClusterId'],
                properties={
                    'engine': c.get('Engine'),
                    'node_type': c.get('CacheNodeType'),
                    'num_nodes': c.get('NumCacheNodes'),
                    'status': c.get('CacheClusterStatus'),
                },
            ) for c in clusters]
        except Exception as e:
            print(f"  ⚠️  ElastiCache scan failed in {self.region}: {e}")
            return []
