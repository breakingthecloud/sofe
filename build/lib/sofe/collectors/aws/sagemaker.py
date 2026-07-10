"""AWS SageMaker collector — endpoints with instance count, variant metrics."""

from .base import BaseCollector
from ...models import Resource


class SageMakerCollector(BaseCollector):
    resource_type = "aws.sagemaker"

    def collect(self) -> list[Resource]:
        try:
            client = self.session.client('sagemaker', region_name=self.region)
            endpoints = client.list_endpoints(StatusEquals='InService').get('Endpoints', [])
            resources = []
            for ep in endpoints:
                name = ep['EndpointName']
                # Get endpoint config for instance details
                total_instances = 0
                try:
                    detail = client.describe_endpoint(EndpointName=name)
                    variants = detail.get('ProductionVariants', [])
                    total_instances = sum(v.get('CurrentInstanceCount', 0) for v in variants)
                except:
                    pass

                # Tags
                tags = {}
                try:
                    tag_resp = client.list_tags(ResourceArn=ep['EndpointArn'])
                    tags = {t['Key']: t['Value'] for t in tag_resp.get('Tags', [])}
                except:
                    pass

                resources.append(self._make_resource(
                    resource_id=name,
                    tags=tags,
                    properties={
                        'status': ep.get('EndpointStatus'),
                        'creation_time': str(ep.get('CreationTime', '')),
                        'instance_count': total_instances,
                    },
                    metrics={
                        'instance_count': float(total_instances),
                        'endpoint_active': 1.0,
                    },
                ))
            return resources
        except Exception as e:
            print(f"  ⚠️  SageMaker scan failed in {self.region}: {e}")
            return []
