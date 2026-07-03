"""AWS Redshift collector — clusters."""
from .base import BaseCollector
from ...models import Resource

class RedshiftCollector(BaseCollector):
    resource_type = "aws.redshift"
    def collect(self) -> list[Resource]:
        try:
            client = self.session.client('redshift', region_name=self.region)
            clusters = client.describe_clusters().get('Clusters', [])
            return [self._make_resource(
                resource_id=c['ClusterIdentifier'],
                tags={t['Key']: t['Value'] for t in c.get('Tags', [])},
                properties={'node_type': c.get('NodeType'), 'num_nodes': c.get('NumberOfNodes'), 'status': c.get('ClusterStatus')},
            ) for c in clusters]
        except Exception as e:
            print(f"  ⚠️  Redshift scan failed in {self.region}: {e}")
            return []
