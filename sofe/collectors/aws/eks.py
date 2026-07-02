"""AWS EKS collector — Kubernetes clusters."""

from .base import BaseCollector
from ...models import Resource


class EKSCollector(BaseCollector):
    resource_type = "aws.eks"

    def collect(self) -> list[Resource]:
        try:
            client = self.session.client('eks', region_name=self.region)
            clusters = client.list_clusters().get('clusters', [])
            resources = []
            for name in clusters:
                detail = client.describe_cluster(name=name).get('cluster', {})
                resources.append(self._make_resource(
                    resource_id=name,
                    tags=detail.get('tags', {}),
                    properties={
                        'version': detail.get('version'),
                        'status': detail.get('status'),
                        'platform_version': detail.get('platformVersion'),
                    },
                ))
            return resources
        except Exception as e:
            print(f"  ⚠️  EKS scan failed in {self.region}: {e}")
            return []
