"""AWS ECS collector — Fargate + EC2 services."""

from .base import BaseCollector
from ...models import Resource


class ECSCollector(BaseCollector):
    resource_type = "aws.ecs"

    def collect(self) -> list[Resource]:
        try:
            client = self.session.client('ecs', region_name=self.region)
            clusters = client.list_clusters().get('clusterArns', [])
            resources = []
            for cluster_arn in clusters:
                cluster_name = cluster_arn.split('/')[-1]
                services = client.list_services(cluster=cluster_arn).get('serviceArns', [])
                if services:
                    details = client.describe_services(cluster=cluster_arn, services=services[:10]).get('services', [])
                    for svc in details:
                        resources.append(self._make_resource(
                            resource_id=f"{cluster_name}/{svc['serviceName']}",
                            properties={
                                'cluster': cluster_name,
                                'launch_type': svc.get('launchType', 'UNKNOWN'),
                                'desired_count': svc.get('desiredCount'),
                                'running_count': svc.get('runningCount'),
                            },
                        ))
            return resources
        except Exception as e:
            print(f"  ⚠️  ECS scan failed in {self.region}: {e}")
            return []
