"""AWS EBS collector — volumes (attached + unattached)."""

from .base import BaseCollector
from ...models import Resource


class EBSCollector(BaseCollector):
    resource_type = "aws.ebs"

    def collect(self) -> list[Resource]:
        try:
            ec2 = self.session.client('ec2', region_name=self.region)
            volumes = ec2.describe_volumes().get('Volumes', [])
            return [self._make_resource(
                resource_id=v['VolumeId'],
                tags={t['Key']: t['Value'] for t in v.get('Tags', [])},
                properties={'size_gb': v.get('Size'), 'volume_type': v.get('VolumeType'), 'state': v.get('State')},
                metrics={'attached': 1.0 if v.get('Attachments') else 0.0},
            ) for v in volumes]
        except Exception as e:
            print(f"  ⚠️  EBS scan failed in {self.region}: {e}")
            return []
