"""AWS Redshift collector — clusters with encryption, node count metrics."""

from .base import BaseCollector
from ...models import Resource


class RedshiftCollector(BaseCollector):
    resource_type = "aws.redshift"

    def collect(self) -> list[Resource]:
        try:
            client = self.session.client('redshift', region_name=self.region)
            clusters = client.describe_clusters().get('Clusters', [])
            resources = []
            for cluster in clusters:
                cluster_id = cluster['ClusterIdentifier']
                num_nodes = cluster.get('NumberOfNodes', 0)
                encrypted = cluster.get('Encrypted', False)
                publicly_accessible = cluster.get('PubliclyAccessible', False)

                tags = {t['Key']: t['Value'] for t in cluster.get('Tags', [])}

                resources.append(self._make_resource(
                    resource_id=cluster_id,
                    tags=tags,
                    properties={
                        'node_type': cluster.get('NodeType'),
                        'num_nodes': num_nodes,
                        'status': cluster.get('ClusterStatus'),
                        'db_name': cluster.get('DBName'),
                    },
                    metrics={
                        'encrypted': 1.0 if encrypted else 0.0,
                        'publicly_accessible': 1.0 if publicly_accessible else 0.0,
                        'num_nodes': float(num_nodes),
                    },
                ))
            return resources
        except Exception as e:
            print(f"  ⚠️  Redshift scan failed in {self.region}: {e}")
            return []
