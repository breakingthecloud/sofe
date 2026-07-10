"""AWS EBS collector — volumes with type, size, attachment metrics."""

from .base import BaseCollector
from ...models import Resource


class EBSCollector(BaseCollector):
    resource_type = "aws.ebs"

    def collect(self) -> list[Resource]:
        try:
            ec2 = self.session.client('ec2', region_name=self.region)
            volumes = ec2.describe_volumes().get('Volumes', [])
            resources = []
            for vol in volumes:
                tags = {t['Key']: t['Value'] for t in vol.get('Tags', [])}
                attached = len(vol.get('Attachments', [])) > 0
                volume_type = vol.get('VolumeType', 'gp2')

                resources.append(self._make_resource(
                    resource_id=vol['VolumeId'],
                    tags=tags,
                    properties={
                        'volume_type': volume_type,
                        'size_gb': vol.get('Size', 0),
                        'iops': vol.get('Iops'),
                        'state': vol.get('State'),
                        'encrypted': vol.get('Encrypted', False),
                    },
                    metrics={
                        'attached': 1.0 if attached else 0.0,
                        'size_gb': float(vol.get('Size', 0)),
                        'volume_type_gp2': 1.0 if volume_type == 'gp2' else 0.0,
                        'encrypted': 1.0 if vol.get('Encrypted', False) else 0.0,
                    },
                ))
            return resources
        except Exception as e:
            print(f"  ⚠️  EBS scan failed in {self.region}: {e}")
            return []
