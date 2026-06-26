# 🏗️ SOFE — Stairway Open FinOps Engine

**FinOps Policies as Code for AWS.**

SOFE evaluates declarative YAML policies against live AWS infrastructure and produces actionable findings — idle resources, missing tags, governance violations, and cost savings opportunities.

```bash
sofe evaluate --policies ./policies/ --profile production
```

```
────────────────────────────────────────────────────────────────────────────────
Severity   Policy                    Resource             Message
────────────────────────────────────────────────────────────────────────────────
🟠 high    no-idle-ec2               i-0abc123def         avg_cpu = 2.1% (threshold: <5%)
🟡 medium  require-cost-tags         i-0def456ghi         missing: costCenter, owner
🟡 medium  no-unattached-ebs         vol-789abc           180 days old, 500GB
────────────────────────────────────────────────────────────────────────────────
Summary: 3 findings | Potential savings: $365.00/mo
```

---

## Why SOFE?

### The Problem

Teams today manage cloud costs **reactively** — they see the bill spike, panic, then scramble to find what changed. Existing tools either:

- **Alert on total spend** (AWS Budgets) — no root cause, no policy enforcement
- **Scan for security** (Prowler, ScoutSuite) — not cost-focused
- **Estimate costs** (Infracost) — pre-deploy only, no runtime enforcement
- **Lock you in** (Sentinel/HCP) — vendor-specific, not portable

**No tool does:** declarative cost+governance policies that evaluate against **live** infrastructure and produce findings with dollar-amount savings.

### The Solution

SOFE fills this gap:

```yaml
# policies/no-idle-production.yaml
apiVersion: sofe/v1
kind: Policy
metadata:
  name: no-idle-production
  description: "Flag idle EC2 in production (< 5% CPU for 30 days)"
spec:
  scope:
    environments: [production]
    resource_types: [aws.ec2]
  rule:
    metric: avg_cpu_utilization
    period: 30d
    operator: "<"
    threshold: 5
  severity: high
  actions:
    - type: recommend
      suggestion: "Rightsize or terminate"
      estimated_savings: calc
```

Write a policy once. Run it daily. Get findings with savings.

---

## Who Should Use SOFE?

| Role | Why SOFE matters |
|------|-----------------|
| **Cloud/DevOps Engineers** | Automate governance checks in CI/CD. `sofe evaluate --fail-on high` blocks deploys that violate cost policies. |
| **FinOps Practitioners** | Define cost optimization rules as code. Track compliance across accounts. Quantify waste. |
| **Platform Engineers** | Enforce tagging standards, idle resource cleanup, and architecture best practices at scale. |
| **CTOs / Engineering Managers** | Visibility into cloud waste without manual audits. "We save $X/month because of these policies." |
| **AWS Partners / Consultants** | Deliver FinOps assessments faster with repeatable, auditable policy evaluations. |

---

## Why SOFE is Key for FinOps + Governance

### 1. FinOps: Cost Optimization as Code

Traditional FinOps is manual: someone opens Cost Explorer, finds waste, creates a ticket. SOFE automates this:

```
Write policy → sofe evaluate → findings with $ savings → action
```

Every policy produces **quantified savings**: "$340/mo if you terminate this idle instance."

### 2. Cloud Governance: Policies that Actually Enforce

Tags, encryption, public access, budget limits — every team has rules but no enforcement. SOFE makes them executable:

```yaml
- require-cost-tags       → "All resources must have owner + costCenter"
- s3-encryption-required  → "All S3 buckets must have encryption enabled"
- no-public-without-waf   → "No public-facing resource without WAF"
```

Not just documentation. Actual enforcement in CI/CD.

### 3. DevOps: Shift-Left Cost Awareness

Add `sofe evaluate --fail-on high` to your GitHub Action or CI pipeline. Developers see cost violations **before** merge, not after the bill arrives.

### 4. BYaML Integration: Architecture-Aware FinOps

SOFE uses [BYaML](https://byaml.org) component types (`aws.ec2`, `aws.s3`, etc.) — the same type system used for architecture governance. This means:

- Policies reference the same types as your architecture definitions
- Findings map directly to BYaML components
- Cost data correlates with architecture versions

---

## Quick Start

### Install

```bash
pip install sofe
```

### Write Your First Policy

```yaml
# policies/require-tags.yaml
apiVersion: sofe/v1
kind: Policy
metadata:
  name: require-cost-tags
  description: "All resources must have owner and costCenter tags"
spec:
  scope:
    resource_types: [aws.ec2, aws.rds, aws.s3]
  rule:
    metric: has_tag:owner
    operator: "=="
    threshold: 0
  severity: medium
  actions:
    - type: finding
```

### Validate

```bash
sofe validate --policies ./policies/
```

### Evaluate

```bash
# Against real AWS (uses your AWS profile)
sofe evaluate --policies ./policies/ --profile production

# Output as JSON (for automation)
sofe evaluate --policies ./policies/ --format json > findings.json

# CI/CD mode (exit code 1 if high/critical found)
sofe evaluate --policies ./policies/ --fail-on high
```

---

## How It Works

```
┌─────────────────┐     ┌──────────────┐     ┌──────────────────────┐
│ Policy Loader   │     │  Collectors  │     │  Evaluation Engine   │
│                 │     │              │     │                      │
│ Reads YAML      │────▶│ AWS APIs:    │────▶│ For each policy:     │
│ Validates       │     │ EC2, RDS     │     │ match scope →        │
│ schema          │     │ S3, Lambda   │     │ evaluate condition → │
│                 │     │ CloudWatch   │     │ if violated →        │
└─────────────────┘     └──────────────┘     │   generate finding   │
                                             └──────────┬───────────┘
                                                        │
                                              ┌─────────▼─────────┐
                                              │  Output            │
                                              │  • Table (CLI)     │
                                              │  • JSON (CI/CD)    │
                                              │  • Markdown (PRs)  │
                                              └────────────────────┘
```

---

## Supported Metrics

| Metric | Source | Resources |
|--------|--------|-----------|
| `avg_cpu_utilization` | CloudWatch (30d avg) | EC2, RDS |
| `monthly_cost` | Cost Explorer | All |
| `running_days` | LaunchTime | EC2, RDS |
| `has_tag:{key}` | Tags API | All |
| `storage_used_gb` | CloudWatch | S3, EBS |
| `connections` | CloudWatch | RDS |
| `invocations` | CloudWatch | Lambda |

---

## Pre-Built Policies

| Policy | Type | Severity |
|--------|------|----------|
| `no-idle-ec2` | Cost Optimization | high |
| `no-idle-rds` | Cost Optimization | high |
| `require-cost-tags` | Governance | medium |
| `no-oversized-staging` | Cost Optimization | high |
| `s3-lifecycle-required` | Storage | medium |
| `s3-encryption-required` | Security/Cost | high |
| `no-unattached-ebs` | Storage | medium |
| `no-old-snapshots` | Storage | low |
| `budget-exceeded` | Budget | critical |
| `no-public-without-waf` | Security/Cost | high |

---

## CI/CD Integration

### GitHub Actions

```yaml
- name: FinOps Policy Check
  run: |
    pip install sofe
    sofe evaluate --policies ./policies/ --fail-on high --format json > findings.json
```

### Exit Codes

| Code | Meaning |
|:----:|---------|
| 0 | No violations (or below `--fail-on` threshold) |
| 1 | Violations found at or above `--fail-on` severity |

---

## Comparison

| Tool | Cost Policies | Live Eval | Savings Calc | CI/CD | Open Source |
|------|:---:|:---:|:---:|:---:|:---:|
| **SOFE** | ✅ | ✅ | ✅ | ✅ | ✅ |
| AWS Budgets | ❌ (alerts only) | ❌ | ❌ | ❌ | ❌ |
| Infracost | 🟡 (pre-deploy) | ❌ | ✅ | ✅ | ✅ |
| OPA/Rego | ✅ (security) | ❌ | ❌ | ✅ | ✅ |
| Sentinel | ✅ | ❌ | ❌ | ✅ | ❌ (HCP only) |
| Prowler | ❌ (security only) | ✅ | ❌ | ✅ | ✅ |

---

## License

Apache 2.0 — free to use, modify, and distribute.

---

## Contributing

1. Fork the repo
2. Add a policy to `policies/` or a collector to `sofe/collectors/`
3. Submit a PR

---

## Built by

[Carlos Cortez](https://cortez.cloud) — AWS Community Hero, CTO @ BWIT Solutions.
Part of the [BYaML](https://byaml.org) ecosystem for cloud architecture governance.
