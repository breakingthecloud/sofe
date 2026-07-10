"""AWS RDS collector — DB instances with multi-AZ, encryption, backup metrics."""

from .base import BaseCollector
from ...models import Resource


class RDSCollector(BaseCollector):
    resource_type = "aws.rds"

    def collect(self) -> list[Resource]:
        try:
            client = self.session.client('rds', region_name=self.region)
            instances = client.describe_db_instances().get('DBInstances', [])
            resources = []
            for db in instances:
                identifier = db['DBInstanceIdentifier']

                # Tags
                tags = {}
                try:
                    tag_resp = client.list_tags_for_resource(ResourceName=db['DBInstanceArn'])
                    tags = {t['Key']: t['Value'] for t in tag_resp.get('TagList', [])}
                except:
                    pass

                resources.append(self._make_resource(
                    resource_id=identifier,
                    tags=tags,
                    properties={
                        'instance_class': db.get('DBInstanceClass'),
                        'engine': db.get('Engine'),
                        'engine_version': db.get('EngineVersion'),
                        'multi_az': db.get('MultiAZ', False),
                        'storage_type': db.get('StorageType'),
                        'allocated_storage_gb': db.get('AllocatedStorage'),
                        'status': db.get('DBInstanceStatus'),
                    },
                    metrics={
                        'multi_az': 1.0 if db.get('MultiAZ', False) else 0.0,
                        'storage_encrypted': 1.0 if db.get('StorageEncrypted', False) else 0.0,
                        'backup_retention_days': float(db.get('BackupRetentionPeriod', 0)),
                        'publicly_accessible': 1.0 if db.get('PubliclyAccessible', False) else 0.0,
                    },
                ))
            return resources
        except Exception as e:
            print(f"  ⚠️  RDS scan failed in {self.region}: {e}")
            return []
