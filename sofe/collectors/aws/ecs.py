"""AWS ECS collector — services with running/desired count, launch type."""

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
                services = client.list_services(cluster=cluster_arn).get('serviceArns', [])
                if not services:
                    continue
                described = client.describe_services(cluster=cluster_arn, services=services[:10]).get('services', [])
                for svc in described:
                    running = svc.get('runningCount', 0)
                    desired = svc.get('desiredCount', 0)
                    launch_type = svc.get('launchType', 'EC2')

                    resources.append(self._make_resource(
                        resource_id=svc['serviceName'],
                        tags={t['key']: t['value'] for t in svc.get('tags', [])},
                        properties={
                            'cluster': cluster_arn.split('/')[-1],
                            'launch_type': launch_type,
                            'running_count': running,
                            'desired_count': desired,
                            'status': svc.get('status'),
                        },
                        metrics={
                            'running_count': float(running),
                            'desired_count': float(desired),
                            'launch_type_fargate': 1.0 if launch_type == 'FARGATE' else 0.0,
                        },
                    ))
            return resources
        except Exception as e:
            print(f"  ⚠️  ECS scan failed in {self.region}: {e}")
            return []
