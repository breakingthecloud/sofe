"""Test: CLI output formats — table, json, markdown (integración con AWS).

Requiere credenciales AWS válidas (SSO). Profile de TEST = `cc-665` (cuenta user side),
nunca `cc` (producción). Se omite (skip) si el SSO no tiene token válido.

Run: python tests/test_cli_outputs.py (o pytest tests/test_cli_outputs.py -v)
"""

import os
import subprocess
import sys

import pytest

SOFE_CMD = [sys.executable, "-m", "sofe.cli"]
PROFILE = os.environ.get("SOFE_TEST_PROFILE", "cc-665")  # TEST account (user side), NOT production


def _aws_available() -> bool:
    """True si el perfil SSO tiene token válido (sin hacer el parse pesado)."""
    try:
        import subprocess as sp

        r = sp.run(
            ["aws", "sts", "get-caller-identity", "--profile", PROFILE],
            capture_output=True,
            text=True,
            timeout=20,
        )
        return r.returncode == 0
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _aws_available(),
    reason=f"AWS SSO sin token válido para {PROFILE} (corre 'aws sso login --profile {PROFILE}'). Test de integración.",
)


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(SOFE_CMD + args, capture_output=True, text=True)


def test_validate():
    r = _run(["validate", "-p", "policies/"])
    print(r.stdout)
    assert r.returncode == 0, f"Validate failed: {r.stderr}"
    assert "All valid" in r.stdout


def test_evaluate_table():
    r = _run(["evaluate", "-p", "policies/", "--profile", PROFILE])
    print(r.stdout[:500])
    assert r.returncode == 0, r.stderr
    assert "findings" in r.stdout.lower()


def test_evaluate_json():
    import json

    r = _run(["evaluate", "-p", "policies/", "--profile", PROFILE, "-f", "json"])
    findings = json.loads(r.stdout)
    print(f"  JSON findings: {len(findings)}")
    assert len(findings) > 0
    assert "policy_name" in findings[0]


def test_evaluate_markdown():
    r = _run(["evaluate", "-p", "policies/", "--profile", PROFILE, "-f", "markdown"])
    print(r.stdout[:300])
    assert "| Severity |" in r.stdout


def test_dry_run():
    r = _run(["evaluate", "-p", "policies/", "--dry-run"])
    print(r.stdout)
    assert "DRY RUN" in r.stdout
    assert r.returncode == 0