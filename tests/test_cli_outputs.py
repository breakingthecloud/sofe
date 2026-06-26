"""Test: CLI output formats — table, json, markdown.

Run: python tests/test_cli_outputs.py
Requires: AWS_PROFILE=cc-665
"""

import subprocess
import json
import sys

SOFE_CMD = [sys.executable, "-m", "sofe.cli"]
PROFILE = "cc-665"

# 1. Validate
print("=== sofe validate ===")
r = subprocess.run(SOFE_CMD + ["validate", "-p", "policies/"], capture_output=True, text=True)
print(r.stdout)
assert r.returncode == 0, f"Validate failed: {r.stderr}"
assert "All valid" in r.stdout

# 2. Evaluate (table)
print("=== sofe evaluate (table) ===")
r = subprocess.run(SOFE_CMD + ["evaluate", "-p", "policies/", "--profile", PROFILE], capture_output=True, text=True)
print(r.stdout[:500])
assert r.returncode == 0
assert "findings" in r.stdout.lower()

# 3. Evaluate (json)
print("\n=== sofe evaluate (json) ===")
r = subprocess.run(SOFE_CMD + ["evaluate", "-p", "policies/", "--profile", PROFILE, "-f", "json"], capture_output=True, text=True)
findings = json.loads(r.stdout)
print(f"  JSON findings: {len(findings)}")
assert len(findings) > 0
assert "policy_name" in findings[0]

# 4. Evaluate (markdown)
print("\n=== sofe evaluate (markdown) ===")
r = subprocess.run(SOFE_CMD + ["evaluate", "-p", "policies/", "--profile", PROFILE, "-f", "markdown"], capture_output=True, text=True)
print(r.stdout[:300])
assert "| Severity |" in r.stdout

# 5. Dry-run
print("\n=== sofe evaluate --dry-run ===")
r = subprocess.run(SOFE_CMD + ["evaluate", "-p", "policies/", "--dry-run"], capture_output=True, text=True)
print(r.stdout)
assert "DRY RUN" in r.stdout
assert r.returncode == 0

print("\n✅ All CLI tests passed!")
