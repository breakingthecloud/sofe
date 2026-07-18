# Contributing to SOFE

Thanks for your interest in contributing to SOFE! This document explains how to get started.

## Project Structure

```
sofe/
├── sofe/
│   ├── collectors/aws/    # AWS service collectors (1 file per service)
│   ├── engine/            # Core evaluation engine + architecture analysis
│   ├── models/            # Pydantic models (Policy, Resource, Finding)
│   ├── loader/            # YAML policy loader
│   ├── remediation/       # Fix command mappings per policy
│   └── cli.py             # Click CLI entry point
├── policies/              # Pre-built FinOps policies (YAML)
└── tests/
```

## Setting Up Development

```bash
git clone https://github.com/breakingthecloud/sofe.git
cd sofe
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"
```

## Adding a New Collector

1. Create `sofe/collectors/aws/your_service.py`:

```python
from .base import BaseCollector

class YourServiceCollector(BaseCollector):
    service_name = "your-service"
    
    def collect(self, session, regions=None) -> list:
        # Use boto3 via session to describe resources
        # Return list of Resource objects with metrics
        ...
```

2. Register in `sofe/collectors/aws/__init__.py`:
```python
from .your_service import YourServiceCollector

COLLECTORS = {
    ...
    "aws.your-service": YourServiceCollector,
}
```

3. Add at least one policy in `policies/`:
```yaml
apiVersion: sofe/v1
kind: Policy
metadata:
  name: your-service-policy-name
  description: "What this policy checks"
  tags: [cost-optimization, your-service]
spec:
  scope:
    resource_types: ["aws.your-service"]
  rule:
    metric: your_metric_name
    operator: ">"
    threshold: 80
  severity: medium
  message: "your_metric = {value} (threshold: >{threshold})"
```

4. Test locally:
```bash
sofe evaluate --profile your-profile --resource-types aws.your-service
```

## Adding a New Policy

Create a YAML file in `policies/` following this structure:

```yaml
apiVersion: sofe/v1
kind: Policy
metadata:
  name: kebab-case-unique-name
  description: "Human-readable description"
  author: your-email
  tags: [category, service]
spec:
  scope:
    resource_types: ["aws.service-type"]
  rule:
    metric: metric_name_from_collector
    operator: ">"     # <, >, <=, >=, ==, !=
    threshold: 50
  severity: critical  # critical, high, medium, low, info
  actions:
    - type: finding
      suggestion: "What to do about it"
      estimated_savings: "$X/month"
```

**Important:** `severity` goes in `spec`, NOT in `metadata`.

## Code Standards

- Python 3.11+
- Type hints everywhere
- Pydantic models for data structures
- Collectors use boto3 `describe_*` calls (read-only, never write)
- Policies are pure YAML (no code in policies)
- All collectors extend `BaseCollector`

## Pull Requests

1. Fork the repo
2. Create a feature branch: `git checkout -b feat/add-kinesis-collector`
3. Make your changes
4. Test: `sofe evaluate` works with your collector
5. Submit PR with description of what you added

## What We Accept

- ✅ New AWS collectors (any service with cost/governance implications)
- ✅ New policies (cost, security, governance)
- ✅ Bug fixes
- ✅ Documentation improvements
- ✅ Metric expansions for existing collectors

## What We Don't Accept (yet)

- ❌ GCP/Azure collectors (planned for future, not ready)
- ❌ Changes to the evaluation engine logic
- ❌ Breaking changes to the Policy YAML schema
- ❌ Dependencies on paid services

## Questions?

Open an issue or reach out on [LinkedIn](https://www.linkedin.com/in/carloscortezcloud).
