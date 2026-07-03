"""AWS Lambda collector — functions."""

from .base import BaseCollector
from ...models import Resource


class LambdaCollector(BaseCollector):
    resource_type = "aws.lambda"

    def collect(self) -> list[Resource]:
        try:
            client = self.session.client('lambda', region_name=self.region)
            functions = client.list_functions().get('Functions', [])
            return [self._make_resource(
                resource_id=fn['FunctionName'],
                properties={'runtime': fn.get('Runtime'), 'memory': fn.get('MemorySize'), 'timeout': fn.get('Timeout')},
            ) for fn in functions]
        except Exception as e:
            print(f"  ⚠️  Lambda scan failed in {self.region}: {e}")
            return []
