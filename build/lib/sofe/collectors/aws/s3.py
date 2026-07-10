"""AWS S3 collector — buckets with encryption, versioning, lifecycle metrics."""

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
                name = b['BucketName']
                metrics = {}
                properties = {'creation_date': str(b.get('CreationDate', ''))}

                # Encryption
                try:
                    s3.get_bucket_encryption(Bucket=name)
                    metrics['encryption_enabled'] = 1.0
                except:
                    metrics['encryption_enabled'] = 0.0

                # Lifecycle
                try:
                    rules = s3.get_bucket_lifecycle_configuration(Bucket=name).get('Rules', [])
                    metrics['has_lifecycle_rules'] = 1.0 if rules else 0.0
                except:
                    metrics['has_lifecycle_rules'] = 0.0

                # Versioning
                try:
                    ver = s3.get_bucket_versioning(Bucket=name)
                    metrics['versioning_enabled'] = 1.0 if ver.get('Status') == 'Enabled' else 0.0
                except:
                    metrics['versioning_enabled'] = 0.0

                # Public access block
                try:
                    pab = s3.get_public_access_block(Bucket=name)
                    config = pab.get('PublicAccessBlockConfiguration', {})
                    all_blocked = all([
                        config.get('BlockPublicAcls', False),
                        config.get('IgnorePublicAcls', False),
                        config.get('BlockPublicPolicy', False),
                        config.get('RestrictPublicBuckets', False),
                    ])
                    metrics['public_access_blocked'] = 1.0 if all_blocked else 0.0
                except:
                    metrics['public_access_blocked'] = 0.0

                # Logging
                try:
                    logging = s3.get_bucket_logging(Bucket=name)
                    metrics['logging_enabled'] = 1.0 if logging.get('LoggingEnabled') else 0.0
                except:
                    metrics['logging_enabled'] = 0.0

                # Tags
                tags = {}
                try:
                    tag_resp = s3.get_bucket_tagging(Bucket=name)
                    tags = {t['Key']: t['Value'] for t in tag_resp.get('TagSet', [])}
                except:
                    pass

                resources.append(self._make_resource(
                    resource_id=name,
                    tags=tags,
                    properties=properties,
                    metrics=metrics,
                ))
            return resources
        except Exception as e:
            print(f"  ⚠️  S3 scan failed in {self.region}: {e}")
            return []
