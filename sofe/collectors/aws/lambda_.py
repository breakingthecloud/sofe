"""AWS Lambda collector — functions with runtime, memory, timeout metrics."""

from .base import BaseCollector
from ...models import Resource

# Deprecated runtimes as of 2026
DEPRECATED_RUNTIMES = {'python3.7', 'python3.8', 'nodejs14.x', 'nodejs16.x', 'dotnetcore3.1', 'ruby2.7', 'java8', 'go1.x'}


class LambdaCollector(BaseCollector):
    resource_type = "aws.lambda"

    def collect(self) -> list[Resource]:
        try:
            client = self.session.client('lambda', region_name=self.region)
            functions = client.list_functions().get('Functions', [])
            resources = []
            for fn in functions:
                runtime = fn.get('Runtime', '')
                memory = fn.get('MemorySize', 128)
                timeout = fn.get('Timeout', 3)
                code_size = fn.get('CodeSize', 0)

                # Tags
                tags = {}
                try:
                    tag_resp = client.list_tags(Resource=fn['FunctionArn'])
                    tags = tag_resp.get('Tags', {})
                except:
                    pass

                resources.append(self._make_resource(
                    resource_id=fn['FunctionName'],
                    tags=tags,
                    properties={
                        'runtime': runtime,
                        'memory': memory,
                        'timeout': timeout,
                        'code_size': code_size,
                        'handler': fn.get('Handler', ''),
                        'last_modified': fn.get('LastModified', ''),
                    },
                    metrics={
                        'memory_size_mb': float(memory),
                        'timeout_seconds': float(timeout),
                        'runtime_deprecated': 1.0 if runtime in DEPRECATED_RUNTIMES else 0.0,
                        'code_size_mb': round(code_size / 1_048_576, 2),
                    },
                ))
            return resources
        except Exception as e:
            print(f"  ⚠️  Lambda scan failed in {self.region}: {e}")
            return []
