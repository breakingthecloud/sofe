"""AWS S3 collector — buckets with encryption, versioning, lifecycle metrics.

S112: this collector used to read ``b['BucketName']`` (the key is ``Name``) so it never
returned a bucket, and every failed check was recorded as ``0.0``. Both are fixed:

* each bucket is collected once, in its own region (``ListBuckets`` is filtered server-side
  by ``BucketRegion`` when the SDK supports it);
* a metric exists only when AWS answered. "The configuration does not exist" is a real
  answer (``0.0``); "I could not check" (AccessDenied, throttling, ...) leaves the metric
  out and is listed in ``properties["unchecked"]``, so policies skip the rule instead of
  reporting a violation nobody verified.
"""

from concurrent.futures import ThreadPoolExecutor

from botocore.exceptions import ClientError

from .base import BaseCollector
from ...models import Resource
import sys

# ClientError codes that mean "this bucket has no such configuration" — a legitimate answer.
_ABSENT = {
    "encryption": {"ServerSideEncryptionConfigurationNotFoundError"},
    "lifecycle": {"NoSuchLifecycleConfiguration"},
    "public_access_block": {"NoSuchPublicAccessBlockConfiguration"},
    "tags": {"NoSuchTagSet"},
}


def _code(exc: Exception) -> str:
    if isinstance(exc, ClientError):
        return exc.response.get("Error", {}).get("Code", "ClientError")
    return type(exc).__name__


class S3Collector(BaseCollector):
    resource_type = "aws.s3"

    # Six requests per bucket, one after another, would take minutes on an account with dozens of
    # buckets, and the hosted API gateway cuts the evaluation at 30 s. boto3 clients are thread-safe;
    # 10 matches urllib3's default connection pool, so no "pool is full" warnings.
    MAX_WORKERS = 10

    def collect(self) -> list[Resource]:
        try:
            s3 = self.session.client('s3', region_name=self.region)
            buckets = self._list_buckets(s3)
        except Exception as e:
            print(f"  ⚠️  S3 scan failed in {self.region}: {_code(e)}", file=sys.stderr)
            return []

        named = [b for b in buckets if b.get('Name')]
        if self.MAX_WORKERS > 1 and len(named) > 1:
            with ThreadPoolExecutor(max_workers=min(self.MAX_WORKERS, len(named))) as pool:
                collected = list(pool.map(lambda b: self._collect_bucket_safely(s3, b), named))  # keeps listing order
        else:
            collected = [self._collect_bucket_safely(s3, b) for b in named]
        return [r for r in collected if r is not None]

    def _collect_bucket_safely(self, s3, listing: dict):
        """One misbehaving bucket must not abort the evaluation of the others."""
        try:
            return self._collect_bucket(s3, listing['Name'], listing)
        except Exception as e:
            print(f"  ⚠️  S3 bucket skipped in {self.region}: {_code(e)}", file=sys.stderr)
            return None

    # -- listing ---------------------------------------------------------------------

    def _list_buckets(self, s3) -> list[dict]:
        """Buckets that live in ``self.region`` (each bucket belongs to exactly one region)."""
        if self._supports_region_filter(s3):
            # ListBuckets must be sent to the regional endpoint it filters for: the client
            # is already built for self.region.
            return self._paginate(s3, BucketRegion=self.region)
        # Older SDK: no server-side filter. List everything and keep what is in this region.
        return [b for b in self._paginate(s3) if self._region_of(s3, b['Name']) == self.region]

    @staticmethod
    def _supports_region_filter(s3) -> bool:
        shape = s3.meta.service_model.operation_model('ListBuckets').input_shape
        return 'BucketRegion' in shape.members

    @staticmethod
    def _paginate(s3, **kwargs) -> list[dict]:
        if not s3.can_paginate('list_buckets'):
            return s3.list_buckets(**kwargs).get('Buckets', [])
        buckets: list[dict] = []
        for page in s3.get_paginator('list_buckets').paginate(**kwargs):
            buckets.extend(page.get('Buckets', []))
        return buckets

    @staticmethod
    def _region_of(s3, name: str) -> str | None:
        """Bucket region from the ``x-amz-bucket-region`` header (also present on 403 responses)."""
        try:
            resp = s3.head_bucket(Bucket=name)
        except ClientError as e:
            resp = e.response
        except Exception:
            return None
        return (resp.get('ResponseMetadata', {}).get('HTTPHeaders', {}) or {}).get('x-amz-bucket-region')

    # -- per-bucket checks -----------------------------------------------------------

    def _collect_bucket(self, s3, name: str, listing: dict) -> Resource:
        metrics: dict[str, float] = {}
        unchecked: list[str] = []
        properties = {'creation_date': str(listing.get('CreationDate', ''))}

        def check(label, call, on_answer, absent_value=None):
            """Run one check. Answer -> metric; known 'absent' error -> absent_value; otherwise unchecked."""
            try:
                on_answer(call())
            except ClientError as e:
                if _code(e) in _ABSENT.get(label, ()) and absent_value is not None:
                    absent_value()
                else:
                    unchecked.append(label)
            except Exception:
                unchecked.append(label)

        def metric(key, value):
            return lambda: metrics.__setitem__(key, value)

        check('encryption', lambda: s3.get_bucket_encryption(Bucket=name),
              lambda _r: metrics.__setitem__('encryption_enabled', 1.0), metric('encryption_enabled', 0.0))

        check('lifecycle', lambda: s3.get_bucket_lifecycle_configuration(Bucket=name),
              lambda r: metrics.__setitem__('has_lifecycle_rules', 1.0 if r.get('Rules') else 0.0),
              metric('has_lifecycle_rules', 0.0))

        # Versioning / logging answer with an empty configuration when disabled: no "absent" error exists.
        check('versioning', lambda: s3.get_bucket_versioning(Bucket=name),
              lambda r: metrics.__setitem__('versioning_enabled', 1.0 if r.get('Status') == 'Enabled' else 0.0))

        def on_pab(r):
            config = r.get('PublicAccessBlockConfiguration', {})
            all_blocked = all([
                config.get('BlockPublicAcls', False),
                config.get('IgnorePublicAcls', False),
                config.get('BlockPublicPolicy', False),
                config.get('RestrictPublicBuckets', False),
            ])
            metrics['public_access_blocked'] = 1.0 if all_blocked else 0.0

        check('public_access_block', lambda: s3.get_public_access_block(Bucket=name), on_pab,
              metric('public_access_blocked', 0.0))

        check('logging', lambda: s3.get_bucket_logging(Bucket=name),
              lambda r: metrics.__setitem__('logging_enabled', 1.0 if r.get('LoggingEnabled') else 0.0))

        tags: dict[str, str] = {}
        check('tags', lambda: s3.get_bucket_tagging(Bucket=name),
              lambda r: tags.update({t['Key']: t['Value'] for t in r.get('TagSet', [])}),
              lambda: None)
        # (an absent tag set is simply "no tags": absent_value must be callable and not None)

        if unchecked:
            properties['unchecked'] = unchecked

        return self._make_resource(resource_id=name, tags=tags, properties=properties, metrics=metrics)
