"""AWS ElastiCache collector — clusters with engine version, encryption metrics."""

from .base import BaseCollector
from ...models import Resource


class ElastiCacheCollector(BaseCollector):
    resource_type = "aws.elasticache"

    def collect(self) -> list[Resource]:
        try:
            client = self.session.client('elasticache', region_name=self.region)
            clusters = client.describe_cache_clusters(ShowCacheNodeInfo=True).get('CacheClusters', [])
            resources = []
            for cluster in clusters:
                cluster_id = cluster['CacheClusterId']
                engine = cluster.get('Engine', '')
                engine_version = cluster.get('EngineVersion', '')
                num_nodes = cluster.get('NumCacheNodes', 0)
                encrypted = cluster.get('AtRestEncryptionEnabled', False)
                transit_encrypted = cluster.get('TransitEncryptionEnabled', False)

                resources.append(self._make_resource(
                    resource_id=cluster_id,
                    properties={
                        'engine': engine,
                        'engine_version': engine_version,
                        'node_type': cluster.get('CacheNodeType'),
                        'num_nodes': num_nodes,
                        'status': cluster.get('CacheClusterStatus'),
                    },
                    metrics={
                        'at_rest_encryption': 1.0 if encrypted else 0.0,
                        'transit_encryption': 1.0 if transit_encrypted else 0.0,
                        'num_nodes': float(num_nodes),
                    },
                ))
            return resources
        except Exception as e:
            print(f"  ⚠️  ElastiCache scan failed in {self.region}: {e}")
            return []
