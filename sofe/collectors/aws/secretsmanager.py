"""AWS Secrets Manager collector — secrets with rotation and access metrics."""

from datetime import datetime, timezone
from .base import BaseCollector
from ...models import Resource


class SecretsManagerCollector(BaseCollector):
    resource_type = "aws.secretsmanager"

    def collect(self) -> list[Resource]:
        try:
            client = self.session.client('secretsmanager', region_name=self.region)
            secrets = client.list_secrets().get('SecretList', [])
            resources = []
            now = datetime.now(timezone.utc)
            for s in secrets:
                rotation_enabled = s.get('RotationEnabled', False)
                last_rotated = s.get('LastRotatedDate')
                last_accessed = s.get('LastAccessedDate')

                days_since_rotation = 999
                if last_rotated:
                    days_since_rotation = (now - last_rotated).days

                days_since_access = 999
                if last_accessed:
                    days_since_access = (now - last_accessed).days

                tags = {t['Key']: t['Value'] for t in s.get('Tags', [])}

                resources.append(self._make_resource(
                    resource_id=s.get('Name', s.get('ARN', '')),
                    tags=tags,
                    properties={
                        'rotation_enabled': rotation_enabled,
                        'last_rotated': str(last_rotated) if last_rotated else None,
                        'last_accessed': str(last_accessed) if last_accessed else None,
                    },
                    metrics={
                        'rotation_enabled': 1.0 if rotation_enabled else 0.0,
                        'days_since_last_rotation': float(days_since_rotation),
                        'days_since_last_access': float(days_since_access),
                    },
                ))
            return resources
        except Exception as e:
            print(f"  ⚠️  SecretsManager scan failed in {self.region}: {e}")
            return []
