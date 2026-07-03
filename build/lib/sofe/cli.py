"""SOFE CLI — sofe evaluate, sofe validate."""

import json
import click
from .loader import load_policies, validate_policies
from .engine import evaluate
from .models import Finding


@click.group()
@click.version_option(version="0.1.0")
def main():
    """SOFE — Stairway Open FinOps Engine. Policies as Code for AWS."""
    pass


@main.command()
@click.option("--policies", "-p", required=True, help="Path to policies directory or file")
@click.option("--format", "-f", "fmt", default="table", type=click.Choice(["table", "json", "markdown"]))
@click.option("--min-severity", default=None, type=click.Choice(["critical", "high", "medium", "low", "info"]))
@click.option("--fail-on", default=None, type=click.Choice(["critical", "high", "medium", "low"]))
@click.option("--profile", default=None, help="AWS profile to use")
@click.option("--dry-run", is_flag=True, help="Show what would be evaluated without calling AWS")
def evaluate_cmd(policies, fmt, min_severity, fail_on, profile, dry_run):
    """Evaluate policies against live AWS resources."""
    from .collectors import collect_all

    click.echo(f"📋 Loading policies from: {policies}")
    policy_list = load_policies(policies)
    click.echo(f"   Found {len(policy_list)} policies")

    if dry_run:
        click.echo("\n🔍 DRY RUN — would evaluate:")
        for p in policy_list:
            click.echo(f"   • {p.metadata.name} ({p.spec.severity.value}) → {p.spec.scope.resource_types}")
        return

    click.echo(f"\n☁️  Scanning AWS resources (profile: {profile or 'default'})...")
    resources = collect_all(profile=profile, resource_types=_get_required_types(policy_list))
    click.echo(f"   Found {len(resources)} resources")

    click.echo(f"\n⚡ Evaluating {len(policy_list)} policies against {len(resources)} resources...")
    findings = evaluate(policy_list, resources)

    # Filter by severity
    if min_severity:
        severity_order = ["critical", "high", "medium", "low", "info"]
        min_idx = severity_order.index(min_severity)
        findings = [f for f in findings if severity_order.index(f.severity.value) <= min_idx]

    # Output
    if fmt == "json":
        click.echo(json.dumps([f.model_dump(mode="json") for f in findings], indent=2, default=str))
    elif fmt == "markdown":
        _output_markdown(findings)
    else:
        _output_table(findings)

    # Exit code
    if fail_on and findings:
        severity_order = ["critical", "high", "medium", "low"]
        fail_idx = severity_order.index(fail_on)
        blocking = [f for f in findings if severity_order.index(f.severity.value) <= fail_idx]
        if blocking:
            raise SystemExit(1)


@main.command()
@click.option("--policies", "-p", required=True, help="Path to policies directory or file")
def validate(policies):
    """Validate policy YAML files without evaluating."""
    results = validate_policies(policies)
    all_valid = True
    for r in results:
        status = "✅" if r["valid"] else "❌"
        click.echo(f"  {status} {r['file']}")
        if r["error"]:
            click.echo(f"     {r['error']}")
            all_valid = False

    click.echo(f"\n{'All valid ✅' if all_valid else 'Some invalid ❌'}")
    if not all_valid:
        raise SystemExit(1)


def _get_required_types(policies) -> list[str]:
    types = set()
    for p in policies:
        types.update(p.spec.scope.resource_types)
    return list(types)


def _output_table(findings: list[Finding]):
    if not findings:
        click.echo("\n✅ No violations found!")
        return

    click.echo(f"\n{'─'*80}")
    click.echo(f"{'Severity':<10} {'Policy':<25} {'Resource':<20} {'Message':<25}")
    click.echo(f"{'─'*80}")
    icons = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🔵", "info": "⚪"}
    for f in findings:
        icon = icons.get(f.severity.value, "⚪")
        click.echo(f"{icon} {f.severity.value:<8} {f.policy_name:<25} {f.resource_id:<20} {f.message:<25}")

    total_savings = sum(f.estimated_savings or 0 for f in findings)
    click.echo(f"{'─'*80}")
    click.echo(f"Summary: {len(findings)} findings | Potential savings: ${total_savings:.2f}/mo")


def _output_markdown(findings: list[Finding]):
    click.echo(f"# SOFE Evaluation Results\n")
    click.echo(f"**Findings:** {len(findings)}\n")
    click.echo("| Severity | Policy | Resource | Message |")
    click.echo("|----------|--------|----------|---------|")
    for f in findings:
        click.echo(f"| {f.severity.value} | {f.policy_name} | {f.resource_id} | {f.message} |")


if __name__ == "__main__":
    main()
