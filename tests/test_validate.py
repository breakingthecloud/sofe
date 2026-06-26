"""Test: sofe validate — load and validate all policy YAML files."""

import sys
sys.path.insert(0, '.')
from sofe.loader import load_policies, validate_policies

# Expected: all 4 policies valid
results = validate_policies('policies/')
print(f"Policies validated: {len(results)}")
for r in results:
    status = "✅" if r["valid"] else "❌"
    print(f"  {status} {r['file']}")
    assert r["valid"], f"Policy {r['file']} failed: {r['error']}"

# Load and verify structure
policies = load_policies('policies/')
assert len(policies) == 4
print(f"\n✅ All {len(policies)} policies valid and loadable")

"""
Expected output:
Policies validated: 4
  ✅ no-idle-ec2.yaml
  ✅ no-unattached-ebs.yaml
  ✅ require-cost-tags.yaml
  ✅ s3-require-environment-tag.yaml

✅ All 4 policies valid and loadable
"""
