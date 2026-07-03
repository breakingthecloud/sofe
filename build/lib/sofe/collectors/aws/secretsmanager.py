"""AWS Secrets Manager collector — secrets and rotation status."""
from .base import BaseCollector
from ...models import Resource

class SecretsManagerCollector(BaseCollector):
    resource_type = "aws.secretsmanager"
    def collect(self) -> list[Resource]:
        try:
            client = self.session.client('secretsmanager', region_name=self.region)
            secrets = client.list_secrets().get('SecretList', [])
            return [self._make_resource(
                resource_id=s['Name'],
                tags={t['Key']: t['Value'] for t in s.get('Tags', [])},
                properties={'rotation_enabled': s.get('RotationEnabled', False), 'last_accessed': str(s.get('LastAccessedDate', ''))},
                metrics={'rotation_enabled': 1.0 if s.get('RotationEnabled') else 0.0},
            ) for s in secrets]
        except Exception as e:
            print(f"  ⚠️  Secrets Manager scan failed in {self.region}: {e}")
            return []
