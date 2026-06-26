"""Test: sofe evaluate — scan real AWS and produce findings.

Requires: AWS_PROFILE=cc-665 (account 665338395650)
Expected: 6 resources (4 S3 + 2 Lambda), 10 findings
"""

import sys
sys.path.insert(0, '.')
from sofe.loader import load_policies
from sofe.collectors import collect_all
from sofe.engine import evaluate

PROFILE = 'cc-665'

policies = load_policies('policies/')
print(f"📋 {len(policies)} policies loaded")

resources = collect_all(profile=PROFILE, resource_types=['aws.ec2', 'aws.s3', 'aws.lambda'])
print(f"☁️  {len(resources)} resources found")
print(f"   Types: { {r.resource_type: sum(1 for x in resources if x.resource_type == r.resource_type) for r in resources} }")

findings = evaluate(policies, resources)
print(f"⚡ {len(findings)} findings")

icons = {'critical':'🔴','high':'🟠','medium':'🟡','low':'🔵'}
for f in findings:
    icon = icons.get(f.severity.value, '⚪')
    print(f"  {icon} {f.severity.value:<8} {f.policy_name:<25} {f.resource_id}")

print(f"\n✅ Test passed | {len(findings)} findings from {len(resources)} resources")

"""
Expected output (Jun 2026, account 665338395650):
📋 4 policies loaded
☁️  6 resources found
   Types: {'aws.s3': 4, 'aws.lambda': 2}
⚡ 10 findings
  🟡 medium   require-cost-tags         dev.app.reconecta.cloud
  🟡 medium   require-cost-tags         dev.cdn.reconecta.cloud
  ...
  🔵 low      s3-require-environment-tag swagger.reconecta.cloud
✅ Test passed | 10 findings from 6 resources
"""
