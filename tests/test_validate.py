"""Test: sofe validate — load and validate all policy YAML files."""

import sys
sys.path.insert(0, '.')
from sofe.loader import load_policies, validate_policies

# All policies must validate (el repo crece; no fijar un count exacto)
results = validate_policies('policies/')
print(f"Policies validated: {len(results)}")
for r in results:
    status = "✅" if r["valid"] else "❌"
    print(f"  {status} {r['file']}")
    assert r["valid"], f"Policy {r['file']} failed: {r['error']}"

# Load and verify structure (>= baseline mínimo de policies canónicas)
policies = load_policies('policies/')
assert len(policies) >= 10, f"Se esperaban al menos 10 policies válidas, got {len(policies)}"
print(f"\n✅ All {len(policies)} policies valid and loadable")

"""
Expected output:
Policies validated: N (>=10)
  ✅ <policy>.yaml   (N válidas, ninguna inválida)

✅ All N policies valid and loadable
"""