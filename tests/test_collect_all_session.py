"""Tests for collect_all(session=...) — S111.

The hosted deployment evaluates a customer's account with temporary credentials
from an assumed role. The engine must therefore accept an already-built boto3
session and must NOT fall back to the default credential chain when given one.
"""

import inspect
from unittest.mock import MagicMock

import pytest

import sofe.collectors as collectors


class _FakeCollector:
    """Records the session / account it is built with; collects nothing."""

    instances: list = []

    def __init__(self, session, region, account_id):
        self.session = session
        self.region = region
        self.account_id = account_id
        _FakeCollector.instances.append(self)

    def collect(self):
        return []


def _session(account_id="111122223333", region="us-east-1"):
    s = MagicMock(name="session")
    s.region_name = region
    s.client.return_value.get_caller_identity.return_value = {"Account": account_id}
    return s


@pytest.fixture
def engine(monkeypatch):
    """Isolate collect_all from AWS: one fake collector, spied enrichment steps."""
    _FakeCollector.instances = []
    enriched = {}

    monkeypatch.setattr(collectors, "COLLECTORS", {"aws.fake": _FakeCollector})
    monkeypatch.setattr(collectors, "ALL_TYPES", ["aws.fake"])

    def spy(name, ret=None):
        def _f(session, *a, **kw):
            enriched[name] = session
            return ret
        monkeypatch.setattr(collectors, name, _f)

    spy("_enrich_metrics")
    spy("_enrich_costs")
    spy("_enrich_commitments", ret={})
    spy("_enrich_recommendations")

    created = []

    def fake_boto3_session(*args, **kwargs):
        created.append((args, kwargs))
        return _session()

    monkeypatch.setattr(collectors.boto3, "Session", fake_boto3_session)
    return {"enriched": enriched, "created": created}


def test_injected_session_is_used_everywhere(engine):
    session = _session(account_id="444455556666")

    resources = collectors.collect_all(session=session)

    # No default/profile session was ever created.
    assert engine["created"] == []
    # The very same session reaches the collector and every enrichment step.
    assert _FakeCollector.instances[0].session is session
    for step in ("_enrich_metrics", "_enrich_costs", "_enrich_commitments", "_enrich_recommendations"):
        assert engine["enriched"][step] is session
    # Account id comes from the injected session's STS identity.
    assert _FakeCollector.instances[0].account_id == "444455556666"
    account = [r for r in resources if r.resource_type == "aws.account"]
    assert len(account) == 1
    assert account[0].account_id == "444455556666"


def test_session_takes_precedence_over_profile(engine):
    session = _session()

    collectors.collect_all(profile="some-profile", session=session)

    assert engine["created"] == []
    assert _FakeCollector.instances[0].session is session


def test_profile_still_builds_a_named_session(engine):
    collectors.collect_all(profile="dev")

    assert engine["created"] == [((), {"profile_name": "dev"})]


def test_default_credential_chain_still_used_when_nothing_given(engine):
    collectors.collect_all()

    assert engine["created"] == [((), {})]


def test_signature_is_backward_compatible():
    params = list(inspect.signature(collectors.collect_all).parameters.values())
    names = [p.name for p in params]

    # Existing positional order is untouched (CLI/server call with keywords or positionally).
    assert names[:3] == ["profile", "resource_types", "regions"]
    session = params[3]
    assert session.name == "session" and session.default is None
