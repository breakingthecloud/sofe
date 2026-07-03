"""AWS SageMaker collector — endpoints and notebook instances."""
from .base import BaseCollector
from ...models import Resource

class SageMakerCollector(BaseCollector):
    resource_type = "aws.sagemaker"
    def collect(self) -> list[Resource]:
        try:
            client = self.session.client('sagemaker', region_name=self.region)
            resources = []
            # Endpoints
            endpoints = client.list_endpoints().get('Endpoints', [])
            for ep in endpoints:
                resources.append(self._make_resource(
                    resource_id=f"endpoint/{ep['EndpointName']}",
                    properties={'status': ep.get('EndpointStatus'), 'type': 'endpoint'},
                ))
            # Notebook instances
            notebooks = client.list_notebook_instances().get('NotebookInstances', [])
            for nb in notebooks:
                resources.append(self._make_resource(
                    resource_id=f"notebook/{nb['NotebookInstanceName']}",
                    properties={'status': nb.get('NotebookInstanceStatus'), 'instance_type': nb.get('InstanceType'), 'type': 'notebook'},
                ))
            return resources
        except Exception as e:
            print(f"  ⚠️  SageMaker scan failed in {self.region}: {e}")
            return []
