"""S3 collector — S112.

These tests use botocore's Stubber on a REAL S3 client: every request we send and every
response we feed back is validated against the S3 service model. A wrong response key
(`BucketName` instead of `Name`) or a wrong parameter fails here, which `MagicMock` never did.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import boto3
import pytest
from botocore.stub import Stubber

from sofe.collectors.aws.s3 import S3Collector
from sofe.engine import evaluate
from sofe.loader import load_policies
from sofe.models import Resource

REGION = "us-east-1"
ACCT = "111122223333"
CREATED = datetime(2024, 1, 1, tzinfo=timezone.utc)

# metadata.name of the policies (they are not always the file names)
S3_POLICIES = (
    "s3-encryption-required", "s3-lifecycle-required", "s3-public-access-not-blocked",
    "s3-versioning-required", "s3-require-environment-tag",
)


@pytest.fixture(autouse=True)
def _sequential(monkeypatch):
    """Stubber answers calls in the order they were queued, so the tests that use it run one worker."""
    monkeypatch.setattr(S3Collector, "MAX_WORKERS", 1)


def real_client(region: str = REGION):
    return boto3.client("s3", region_name=region, aws_access_key_id="x", aws_secret_access_key="x")


def collector(client, region: str = REGION) -> S3Collector:
    session = MagicMock(name="session")
    session.client.return_value = client
    return S3Collector(session=session, region=region, account_id=ACCT)


def listing(*names, region: str = REGION):
    """A real ListBuckets response (with the server-side region filter applied)."""
    return {"Buckets": [{"Name": n, "CreationDate": CREATED, "BucketRegion": region} for n in names], "Owner": {"ID": "x"}}


def err(stub, op, code, **params):
    stub.add_client_error(op, service_error_code=code, service_message="m", http_status_code=403 if "Denied" in code else 404, expected_params=params or None)


def healthy_bucket(stub, name):
    """Queue the six per-bucket calls in the collector's order, all configured."""
    stub.add_response("get_bucket_encryption", {"ServerSideEncryptionConfiguration": {"Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]}}, {"Bucket": name})
    stub.add_response("get_bucket_lifecycle_configuration", {"Rules": [{"Status": "Enabled", "Filter": {}, "ID": "r"}]}, {"Bucket": name})
    stub.add_response("get_bucket_versioning", {"Status": "Enabled"}, {"Bucket": name})
    stub.add_response("get_public_access_block", {"PublicAccessBlockConfiguration": {
        "BlockPublicAcls": True, "IgnorePublicAcls": True, "BlockPublicPolicy": True, "RestrictPublicBuckets": True}}, {"Bucket": name})
    stub.add_response("get_bucket_logging", {"LoggingEnabled": {"TargetBucket": "logs", "TargetPrefix": "p"}}, {"Bucket": name})
    stub.add_response("get_bucket_tagging", {"TagSet": [{"Key": "Environment", "Value": "prod"}]}, {"Bucket": name})


def policies():
    return [p for p in load_policies("policies/") if p.metadata.name in S3_POLICIES]


# --------------------------------------------------------------------------- inventory
class TestInventory:
    def test_a_bucket_is_collected_with_the_real_response_shape(self):
        """Regression: the collector read b['BucketName'] and never returned a bucket."""
        client = real_client()
        with Stubber(client) as stub:
            stub.add_response("list_buckets", listing("my-bucket"), {"BucketRegion": REGION})
            healthy_bucket(stub, "my-bucket")

            out = collector(client).collect()

            stub.assert_no_pending_responses()
        assert [r.resource_id for r in out] == ["my-bucket"]
        r = out[0]
        assert (r.resource_type, r.region, r.account_id) == ("aws.s3", REGION, ACCT)
        assert r.metrics == {
            "encryption_enabled": 1.0, "has_lifecycle_rules": 1.0, "versioning_enabled": 1.0,
            "public_access_blocked": 1.0, "logging_enabled": 1.0,
        }
        assert r.tags == {"Environment": "prod"}
        assert "unchecked" not in r.properties

    def test_the_listing_asks_only_for_this_regions_buckets(self):
        client = real_client("eu-west-1")
        with Stubber(client) as stub:
            stub.add_response("list_buckets", listing("eu-bucket", region="eu-west-1"), {"BucketRegion": "eu-west-1"})
            healthy_bucket(stub, "eu-bucket")

            out = collector(client, region="eu-west-1").collect()

        assert [(r.resource_id, r.region) for r in out] == [("eu-bucket", "eu-west-1")]

    def test_every_bucket_is_collected_once_across_two_regions(self):
        us, eu = real_client(), real_client("eu-west-1")
        with Stubber(us) as s_us, Stubber(eu) as s_eu:
            s_us.add_response("list_buckets", listing("us-b"), {"BucketRegion": REGION})
            healthy_bucket(s_us, "us-b")
            s_eu.add_response("list_buckets", listing("eu-b", region="eu-west-1"), {"BucketRegion": "eu-west-1"})
            healthy_bucket(s_eu, "eu-b")

            found = collector(us).collect() + collector(eu, region="eu-west-1").collect()

        assert sorted((r.resource_id, r.region) for r in found) == [("eu-b", "eu-west-1"), ("us-b", "us-east-1")]

    def test_a_listing_split_in_pages_is_read_completely(self):
        client = real_client()
        with Stubber(client) as stub:
            first = {**listing("a"), "ContinuationToken": "t1"}
            stub.add_response("list_buckets", first, {"BucketRegion": REGION})
            stub.add_response("list_buckets", listing("b"), {"BucketRegion": REGION, "ContinuationToken": "t1"})
            for n in ("a", "b"):
                healthy_bucket(stub, n)

            out = collector(client).collect()

        assert [r.resource_id for r in out] == ["a", "b"]

    def test_no_buckets(self):
        client = real_client()
        with Stubber(client) as stub:
            stub.add_response("list_buckets", {"Buckets": [], "Owner": {"ID": "x"}}, {"BucketRegion": REGION})
            assert collector(client).collect() == []

    def test_a_listing_failure_returns_nothing_without_raising(self, capsys):
        client = real_client()
        with Stubber(client) as stub:
            err(stub, "list_buckets", "AccessDenied", BucketRegion=REGION)

            assert collector(client).collect() == []

        assert "AccessDenied" in capsys.readouterr().err


class TestOldSdkWithoutTheRegionFilter:
    """boto3 builds that predate ListBuckets(BucketRegion=...) list everything and ask each bucket where it lives."""

    def _client(self, buckets, regions):
        c = MagicMock(name="s3")
        c.meta.service_model.operation_model.return_value.input_shape.members = {"MaxBuckets": 1}  # no BucketRegion
        c.can_paginate.return_value = False
        c.list_buckets.return_value = {"Buckets": [{"Name": n, "CreationDate": CREATED} for n in buckets]}
        c.head_bucket.side_effect = lambda Bucket: {"ResponseMetadata": {"HTTPHeaders": {"x-amz-bucket-region": regions[Bucket]}}}
        return c

    def test_only_buckets_of_this_region_are_kept(self):
        c = self._client(["here", "elsewhere"], {"here": REGION, "elsewhere": "eu-west-1"})
        c.get_bucket_encryption.return_value = {}
        c.get_bucket_lifecycle_configuration.return_value = {"Rules": []}
        c.get_bucket_versioning.return_value = {}
        c.get_public_access_block.return_value = {"PublicAccessBlockConfiguration": {}}
        c.get_bucket_logging.return_value = {}
        c.get_bucket_tagging.return_value = {"TagSet": []}

        out = collector(c).collect()

        assert [r.resource_id for r in out] == ["here"]
        c.list_buckets.assert_called_once_with()  # no BucketRegion kwarg on an SDK that lacks it


# --------------------------------------------------------------------------- unknown vs absent
def collect_one(configure):
    client = real_client()
    with Stubber(client) as stub:
        stub.add_response("list_buckets", listing("b"), {"BucketRegion": REGION})
        configure(stub)
        return collector(client).collect()[0]


def queue(stub, enc=None, life=None, ver=None, pab=None, log=None, tag=None):
    """Each argument is a response dict or an error code string; None = healthy default."""
    d = {
        "enc": ("get_bucket_encryption", {"ServerSideEncryptionConfiguration": {"Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]}}),
        "life": ("get_bucket_lifecycle_configuration", {"Rules": [{"Status": "Enabled", "Filter": {}, "ID": "r"}]}),
        "ver": ("get_bucket_versioning", {"Status": "Enabled"}),
        "pab": ("get_public_access_block", {"PublicAccessBlockConfiguration": {"BlockPublicAcls": True, "IgnorePublicAcls": True, "BlockPublicPolicy": True, "RestrictPublicBuckets": True}}),
        "log": ("get_bucket_logging", {"LoggingEnabled": {"TargetBucket": "l", "TargetPrefix": "p"}}),
        "tag": ("get_bucket_tagging", {"TagSet": [{"Key": "Environment", "Value": "prod"}]}),
    }
    given = dict(enc=enc, life=life, ver=ver, pab=pab, log=log, tag=tag)
    for key in ("enc", "life", "ver", "pab", "log", "tag"):
        op, default = d[key]
        v = given[key]
        if isinstance(v, str):
            err(stub, op, v, Bucket="b")
        else:
            stub.add_response(op, v if v is not None else default, {"Bucket": "b"})


class TestUnknownIsNotAViolation:
    @pytest.mark.parametrize(
        "arg, code, metric, label",
        [
            ("enc", "AccessDenied", "encryption_enabled", "encryption"),
            ("life", "AccessDenied", "has_lifecycle_rules", "lifecycle"),
            ("ver", "AccessDenied", "versioning_enabled", "versioning"),
            ("pab", "AccessDenied", "public_access_blocked", "public_access_block"),
            ("log", "AccessDenied", "logging_enabled", "logging"),
            ("enc", "SlowDown", "encryption_enabled", "encryption"),
        ],
    )
    def test_a_check_that_could_not_run_leaves_the_metric_out(self, arg, code, metric, label):
        r = collect_one(lambda stub: queue(stub, **{arg: code}))

        assert metric not in r.metrics            # NOT 0.0
        assert label in r.properties["unchecked"]
        assert len(r.metrics) == 4                # the other four checks still answered

    def test_unreadable_tags_are_reported_and_are_not_empty_tags(self):
        r = collect_one(lambda stub: queue(stub, tag="AccessDenied"))

        assert "tags" in r.properties["unchecked"]

    @pytest.mark.parametrize(
        "arg, code, metric",
        [
            ("enc", "ServerSideEncryptionConfigurationNotFoundError", "encryption_enabled"),
            ("life", "NoSuchLifecycleConfiguration", "has_lifecycle_rules"),
            ("pab", "NoSuchPublicAccessBlockConfiguration", "public_access_blocked"),
        ],
    )
    def test_a_missing_configuration_is_a_real_answer_zero(self, arg, code, metric):
        r = collect_one(lambda stub: queue(stub, **{arg: code}))

        assert r.metrics[metric] == 0.0
        assert "unchecked" not in r.properties

    def test_no_tag_set_means_no_tags_not_unknown(self):
        r = collect_one(lambda stub: queue(stub, tag="NoSuchTagSet"))

        assert r.tags == {} and "unchecked" not in r.properties

    def test_disabled_versioning_and_logging_are_zero(self):
        r = collect_one(lambda stub: queue(stub, ver={}, log={}))

        assert r.metrics["versioning_enabled"] == 0.0 and r.metrics["logging_enabled"] == 0.0

    def test_partially_blocked_public_access_is_not_blocked(self):
        cfg = {"PublicAccessBlockConfiguration": {"BlockPublicAcls": True, "IgnorePublicAcls": True, "BlockPublicPolicy": False, "RestrictPublicBuckets": True}}
        r = collect_one(lambda stub: queue(stub, pab=cfg))

        assert r.metrics["public_access_blocked"] == 0.0

    def test_empty_lifecycle_rules_mean_no_lifecycle(self):
        r = collect_one(lambda stub: queue(stub, life={"Rules": []}))

        assert r.metrics["has_lifecycle_rules"] == 0.0


# --------------------------------------------------------------------------- policies
class TestPolicies:
    def _evaluate(self, resource: Resource):
        return {f.policy_name for f in evaluate(policies(), [resource])}

    def test_a_well_configured_bucket_has_no_s3_findings(self):
        r = collect_one(lambda stub: queue(stub))

        assert self._evaluate(r) == set()

    def test_real_zeroes_fire_their_policies(self):
        r = collect_one(lambda stub: queue(stub, enc="ServerSideEncryptionConfigurationNotFoundError", life="NoSuchLifecycleConfiguration",
                                           ver={"Status": "Suspended"}, pab="NoSuchPublicAccessBlockConfiguration"))

        assert self._evaluate(r) >= {"s3-encryption-required", "s3-lifecycle-required", "s3-versioning-required", "s3-public-access-not-blocked"}

    def test_unchecked_metrics_do_not_fire_policies(self):
        """The reason for S112: AccessDenied used to be reported as 'not encrypted / not blocked / no versioning'."""
        r = collect_one(lambda stub: queue(stub, enc="AccessDenied", life="AccessDenied", ver="AccessDenied", pab="AccessDenied"))

        assert self._evaluate(r) == set()

    def test_a_bucket_with_unreadable_tags_does_not_fire_tag_policies_via_collect_all(self, monkeypatch):
        import sofe.collectors as collectors

        r = collect_one(lambda stub: queue(stub, tag="AccessDenied"))
        monkeypatch.setattr(collectors, "COLLECTORS", {"aws.s3": lambda session, region, account_id: type("C", (), {"collect": lambda self: [r]})()})
        monkeypatch.setattr(collectors, "ALL_TYPES", ["aws.s3"])
        monkeypatch.setattr(collectors, "_enrich_metrics", lambda *a, **k: None)
        monkeypatch.setattr(collectors, "_enrich_commitments", lambda *a, **k: {})
        monkeypatch.setattr(collectors, "_enrich_recommendations", lambda *a, **k: None)
        monkeypatch.setattr(collectors, "_enrich_costs", lambda *a, **k: {})
        session = MagicMock(); session.region_name = REGION
        session.client.return_value.get_caller_identity.return_value = {"Account": ACCT}

        out = collectors.collect_all(session=session)

        bucket = [x for x in out if x.resource_type == "aws.s3"][0]
        assert not any(k.startswith("has_tag:") for k in bucket.metrics)


# --------------------------------------------------------------------------- concurrency
class TestParallelChecks:
    @staticmethod
    def _fake_client(names, failing=()):
        c = MagicMock(name="s3")
        c.meta.service_model.operation_model.return_value.input_shape.members = {"BucketRegion": 1}
        c.can_paginate.return_value = False
        c.list_buckets.return_value = {"Buckets": [{"Name": n, "CreationDate": CREATED, "BucketRegion": REGION} for n in names]}

        def boom_or(value):
            def f(Bucket):
                if Bucket in failing:
                    raise RuntimeError("unexpected")
                return value
            return f

        c.get_bucket_encryption.side_effect = boom_or({})
        c.get_bucket_lifecycle_configuration.side_effect = boom_or({"Rules": []})
        c.get_bucket_versioning.side_effect = boom_or({})
        c.get_public_access_block.side_effect = boom_or({"PublicAccessBlockConfiguration": {}})
        c.get_bucket_logging.side_effect = boom_or({})
        c.get_bucket_tagging.side_effect = boom_or({"TagSet": []})
        return c

    def test_many_buckets_are_collected_in_parallel_and_in_listing_order(self, monkeypatch):
        monkeypatch.setattr(S3Collector, "MAX_WORKERS", 10)
        names = [f"bucket-{i:02d}" for i in range(40)]
        c = self._fake_client(names)

        out = collector(c).collect()

        assert [r.resource_id for r in out] == names
        assert c.get_bucket_encryption.call_count == 40

    def test_the_parallelism_is_bounded(self, monkeypatch):
        import threading, time
        monkeypatch.setattr(S3Collector, "MAX_WORKERS", 4)
        c = self._fake_client([f"b{i}" for i in range(30)])
        active, peak, lock = 0, 0, threading.Lock()
        original = c.get_bucket_encryption.side_effect

        def slow(Bucket):
            nonlocal active, peak
            with lock:
                active += 1; peak = max(peak, active)
            time.sleep(0.01)
            with lock:
                active -= 1
            return original(Bucket)

        c.get_bucket_encryption.side_effect = slow
        collector(c).collect()

        assert 1 < peak <= 4

    def test_one_bucket_failing_unexpectedly_does_not_abort_the_others(self, monkeypatch, capsys):
        monkeypatch.setattr(S3Collector, "MAX_WORKERS", 10)
        c = self._fake_client(["ok-1", "bad", "ok-2"])
        # an exception that is not a ClientError is "could not check", handled per check ...
        out = collector(c).collect()
        assert [r.resource_id for r in out] == ["ok-1", "bad", "ok-2"]

        # ... and a failure OUTSIDE the checks (e.g. building the resource) drops only that bucket
        original = S3Collector._collect_bucket
        monkeypatch.setattr(S3Collector, "_collect_bucket", lambda self, s3, name, b: (_ for _ in ()).throw(RuntimeError("x")) if name == "bad" else original(self, s3, name, b))
        out = collector(self._fake_client(["ok-1", "bad", "ok-2"])).collect()

        assert [r.resource_id for r in out] == ["ok-1", "ok-2"]
        assert "S3 bucket skipped" in capsys.readouterr().err


class TestTimeBudget:
    def test_when_the_budget_is_spent_buckets_are_listed_but_unchecked_and_no_call_is_made(self, monkeypatch):
        monkeypatch.setattr(S3Collector, "TIME_BUDGET_SECONDS", -1.0)  # already over budget
        c = TestParallelChecks._fake_client(["a", "b"])

        out = collector(c).collect()

        assert [r.resource_id for r in out] == ["a", "b"]  # inventoried
        for r in out:
            assert r.metrics == {}                          # nothing invented
            assert r.properties["unchecked"] == ["encryption", "lifecycle", "versioning", "public_access_block", "logging", "tags"]
        for call in (c.get_bucket_encryption, c.get_bucket_versioning, c.get_public_access_block, c.get_bucket_tagging):
            assert call.call_count == 0

    def test_buckets_over_budget_do_not_fire_policies(self, monkeypatch):
        monkeypatch.setattr(S3Collector, "TIME_BUDGET_SECONDS", -1.0)
        out = collector(TestParallelChecks._fake_client(["a"])).collect()

        assert {f.policy_name for f in evaluate(policies(), out)} == set()

    def test_the_default_budget_is_comfortably_below_the_gateway_limit(self):
        assert 0 < S3Collector.TIME_BUDGET_SECONDS <= 12  # the hosted API gateway cuts at 30 s
