"""S081 — Commitments collector usage example (standalone from sofe engine).

Shows how to read Savings Plans / RI coverage + utilization WITHOUT the server,
directly from the sofe engine (your local AWS credentials).

Two ways:
  1. Full scan  -> collect_all() attaches sp_*/ri_* metrics to every resource.
  2. Standalone -> CommitmentsCollector.get_metrics() returns account-level dict.

Requires the SOFE reader role (or any CE-permissioned credentials) with:
    ce:GetSavingsPlansUtilization, ce:GetSavingsPlansCoverage, ce:GetReservationUtilization

Run:
    python examples/commitments.py --profile your-aws-profile
"""

from __future__ import annotations

import argparse
import sys

import boto3

from sofe.collectors.aws.commitments import CommitmentsCollector


def usage_standalone(profile: str | None) -> None:
    """Direct use of the collector — no full scan, just the account metrics."""
    print("\n── Standalone CommitmentsCollector ─────────────────────────")
    session = boto3.Session(profile_name=profile) if profile else boto3.Session()
    account_id = session.client("sts").get_caller_identity()["Account"]

    collector = CommitmentsCollector(
        session=session,
        region="us-east-1",  # Cost Explorer is global / us-east-1 only
        account_id=account_id,
    )
    collector.collect()
    metrics = collector.get_metrics()

    print(f"Account {account_id}:")
    labels = {
        "sp_utilization_pct": "Savings Plans utilization",
        "sp_coverage_pct": "Savings Plans coverage",
        "ri_utilization_pct": "RI utilization",
    }
    for key, label in labels.items():
        val = metrics.get(key)
        if val is None:
            print(f"  ⚠️ {label:28s}: n/a (CE disabled or role lacks ce: permission)")
        else:
            print(f"  ✅ {label:28s}: {val:.1f}%")

    # Interpretation hints (policies S083 will alert on these)
    if metrics.get("sp_coverage_pct") is not None and metrics["sp_coverage_pct"] < 90:
        print("\n  💡 sp_coverage < 90% → consider buying more Savings Plans (S082 recommendation)")
    if metrics.get("ri_utilization_pct") is not None and metrics["ri_utilization_pct"] < 80:
        print("  💡 ri_utilization < 80% → reserved instances are under-used; right-size or exchange")


def usage_full_scan(profile: str | None) -> None:
    """Run the full scan — commitment metrics get attached to every resource."""
    print("\n── Full scan (commitments attach to resources) ─────────────")
    from sofe.collectors import collect_all

    resources = collect_all(profile=profile)
    print(f"Scanned {len(resources)} resources")
    with_commitments = [r for r in resources if r.metrics.get("sp_coverage_pct") is not None]
    print(f"{len(with_commitments)} resources carry commitment metrics "
          f"(e.g. sp_coverage_pct={with_commitments[0].metrics.get('sp_coverage_pct') if with_commitments else 'n/a'})")


def main() -> None:
    parser = argparse.ArgumentParser(description="S081 Commitments collector example")
    parser.add_argument("--profile", default=None, help="AWS profile (defaults to default chain)")
    parser.add_argument("--full-scan", action="store_true", help="Also run collect_all() full scan")
    args = parser.parse_args()

    try:
        usage_standalone(args.profile)
        if args.full_scan:
            usage_full_scan(args.profile)
    except Exception as e:  # noqa: BLE001
        print(f"\n❌ Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()