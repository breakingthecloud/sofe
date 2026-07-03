"""AWS RDS collector — database instances."""

from .base import BaseCollector
from ...models import Resource


class RDSCollector(BaseCollector):
    resource_type = "aws.rds"

    def collect(self) -> list[Resource]:
        try:
            client = self.session.client('rds', region_name=self.region)
            instances = client.describe_db_instances().get('DBInstances', [])
            return [self._make_resource(
                resource_id=db['DBInstanceIdentifier'],
                tags={t['Key']: t['Value'] for t in db.get('TagList', [])},
                properties={'engine': db.get('Engine'), 'class': db.get('DBInstanceClass'), 'storage_gb': db.get('AllocatedStorage')},
            ) for db in instances]
        except Exception as e:
            print(f"  ⚠️  RDS scan failed in {self.region}: {e}")
            return []
