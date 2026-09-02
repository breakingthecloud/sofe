<p align="center">
  <img alt="SOFE" src="https://img.shields.io/badge/🏗️-SOFE-8A2BE2?style=for-the-badge" height="50">
</p>

<p align="center">
  <b>FinOps Policies as Code for AWS</b><br>
  Declarative YAML policies → live infrastructure evaluation → findings with dollar savings.
</p>

<p align="center">
  <a href="#quick-start">Quick Start</a>
  ·
  <a href="#why-sofe">Why SOFE?</a>
  ·
  <a href="#comparison">Comparison</a>
  ·
  <a href="#pre-built-policies">Policies</a>
  ·
  <a href="#cicd">CI/CD</a>
</p>

<p align="center">
  <img src="https://img.shields.io/pypi/v/sofe?style=flat-square&logo=pypi&color=8A2BE2" alt="PyPI">
  <img src="https://img.shields.io/badge/python-3.10%2B-blue?style=flat-square&logo=python" alt="Python">
  <img src="https://img.shields.io/badge/license-Apache_2.0-8A2BE2?style=flat-square" alt="License">
  <img src="https://img.shields.io/badge/policies-39-success?style=flat-square" alt="39 policies">
  <img src="https://img.shields.io/badge/collectors-19-blue?style=flat-square" alt="19 collectors">
  <img src="https://img.shields.io/badge/PRs-welcome-brightgreen?style=flat-square" alt="PRs">
</p>

---

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

## Why SOFE?

### The Problem

Teams today manage cloud costs **reactively** — they see the bill spike, panic, then scramble to find what changed. Existing tools either alert on total spend (no root cause), scan for security (not cost-focused), or lock you into a vendor.

**No tool does:** declarative cost + governance policies that evaluate against **live** infrastructure and produce findings with dollar-amount savings.

### The Solution

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

## Who Should Use SOFE?

| Role | Why SOFE matters |
|------|-----------------|
| **Cloud/DevOps Engineers** | Automate governance checks in CI/CD. `sofe evaluate --fail-on high` blocks deploys that violate cost policies. |
| **FinOps Practitioners** | Define cost optimization rules as code. Track compliance across accounts. Quantify waste. |
| **Platform Engineers** | Enforce tagging standards, idle resource cleanup, and architecture best practices at scale. |
| **CTOs / Engineering Managers** | Visibility into cloud waste without manual audits. |
| **AWS Partners / Consultants** | Deliver FinOps assessments faster with repeatable, auditable policy evaluations. |

## Quick Start

```bash
# Install
pip install sofe

# Write your first policy
cat > policies/require-tags.yaml << 'EOF'
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
EOF

# Validate
sofe validate --policies ./policies/

# Evaluate against live AWS
sofe evaluate --policies ./policies/ --profile production

# CI/CD mode (exit code 1 if high/critical found)
sofe evaluate --policies ./policies/ --fail-on high

# JSON output for automation
sofe evaluate --policies ./policies/ --format json > findings.json
```

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

## Supported Metrics

| Metric | Source | Resources |
|--------|--------|-----------|
| `avg_cpu_utilization` | CloudWatch (30d avg) | EC2, RDS |
| `monthly_cost` | Cost Explorer | All |
| `sp_coverage_pct` | Cost Explorer (Savings Plans) | All (account-level) |
| `sp_utilization_pct` | Cost Explorer (Savings Plans) | All (account-level) |
| `ri_utilization_pct` | Cost Explorer (RI) | All (account-level) |
| `running_days` | LaunchTime | EC2, RDS |
| `has_tag:{key}` | Tags API | All |
| `storage_used_gb` | CloudWatch | S3, EBS |
| `connections` | CloudWatch | RDS |
| `invocations` | CloudWatch | Lambda |

## CI/CD

```yaml
- name: FinOps Policy Check
  run: |
    pip install sofe
    sofe evaluate --policies ./policies/ --fail-on high --format json > findings.json
```

| Exit Code | Meaning |
|:---------:|---------|
| 0 | No violations (or below `--fail-on` threshold) |
| 1 | Violations found at or above `--fail-on` severity |

## Comparison

| Tool | Cost Policies | Live Eval | Savings Calc | CI/CD | Open Source |
|------|:---:|:---:|:---:|:---:|:---:|
| **SOFE** | ✅ | ✅ | ✅ | ✅ | ✅ |
| AWS Budgets | ❌ (alerts only) | ❌ | ❌ | ❌ | ❌ |
| Infracost | 🟡 (pre-deploy) | ❌ | ✅ | ✅ | ✅ |
| OPA/Rego | ✅ (security) | ❌ | ❌ | ✅ | ✅ |
| Prowler | ❌ (security only) | ✅ | ❌ | ✅ | ✅ |
| Cloud Custodian | 🟡 (not FinOps-first) | ✅ | ❌ | 🟡 | ✅ |

### The SOFE Position

SOFE lives in the **RUN** phase: evaluate live infrastructure against declarative FinOps policies. Produce findings with dollar savings.

```
PLAN           DEPLOY       RUN               OPTIMIZE
Infracost      OPA/Rego     ★ SOFE ★          Spot.io
Checkov        Sentinel     Cloud Custodian   CAST AI
                            AWS Config
```

## BYaML Integration

SOFE derives an [Architecture Graph v0.4](https://github.com/breakingthecloud/byaml-spec) (BYaML v2)
— nodos con tipos canónicos (`aws.ec2`, `aws.s3`, …) y aristas tipadas. Es el mismo estándar del registry
`schema.byaml.org` y de `byaml-mcp` (agentes). `/evaluations/:id/graph` expone el grafo derivado.

## Ecosystem

| Project | Description |
|---------|-------------|
| [sofe-server](https://github.com/breakingthecloud/sofe-server) | REST API server (FastAPI) |
| [sofe-cli](https://github.com/breakingthecloud/sofe-cli) | Go CLI (19 commands, TUI) |
| [sofe-action](https://github.com/breakingthecloud/sofe-action) | GitHub Action |
| [byaml-spec](https://github.com/breakingthecloud/byaml-spec) | Estándar Architecture Graph v0.4 (schema/catalog/relationships) |
| [byaml-mcp](https://github.com/breakingthecloud/byaml-mcp) | MCP tools para agentes (graph + insights + remediation) |
| [FinOptix](https://github.com/breakingthecloud/finoptix) | AI model for FinOps reasoning |

## License

Apache 2.0 — free to use, modify, and distribute.

---

<p align="center">
  Built by engineers who got tired of surprise AWS bills.<br>
  <a href="https://sofe.dev">sofe.dev</a> · <a href="https://github.com/breakingthecloud/sofe">GitHub</a> · <a href="https://finoptix.dev">finoptix.dev</a>
</p>
<p align="center">
  <sub>Write a policy once. Run it daily. Save money.</sub>
</p>
