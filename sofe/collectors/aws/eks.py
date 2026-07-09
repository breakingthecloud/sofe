"""AWS EKS collector — Kubernetes clusters with version, access metrics."""

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
                version = detail.get('version', '0')
                access_config = detail.get('resourcesVpcConfig', {})
                endpoint_public = access_config.get('endpointPublicAccess', True)
                logging_enabled = bool(detail.get('logging', {}).get('clusterLogging', [{}])[0].get('enabled', False)) if detail.get('logging', {}).get('clusterLogging') else False

                resources.append(self._make_resource(
                    resource_id=name,
                    tags=detail.get('tags', {}),
                    properties={
                        'version': version,
                        'status': detail.get('status'),
                        'platform_version': detail.get('platformVersion'),
                        'endpoint_public': endpoint_public,
                    },
                    metrics={
                        'endpoint_public_access': 1.0 if endpoint_public else 0.0,
                        'logging_enabled': 1.0 if logging_enabled else 0.0,
                        'version_outdated': 1.0 if float(version or '0') < 1.28 else 0.0,
                    },
                ))
            return resources
        except Exception as e:
            print(f"  ⚠️  EKS scan failed in {self.region}: {e}")
            return []
