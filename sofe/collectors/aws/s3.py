"""AWS S3 collector — buckets."""

from .base import BaseCollector
from ...models import Resource


class S3Collector(BaseCollector):
    resource_type = "aws.s3"

    def collect(self) -> list[Resource]:
        try:
            s3 = self.session.client('s3', region_name=self.region)
            buckets = s3.list_buckets().get('Buckets', [])
            resources = []
            for b in buckets:
                try:
                    tags_resp = s3.get_bucket_tagging(Bucket=b['Name'])
                    tags = {t['Key']: t['Value'] for t in tags_resp.get('TagSet', [])}
                except:
                    tags = {}
                resources.append(self._make_resource(
                    resource_id=b['Name'],
                    tags=tags,
                    properties={'creation_date': str(b.get('CreationDate', ''))},
                ))
            return resources
        except Exception as e:
            print(f"  ⚠️  S3 scan failed: {e}")
            return []
