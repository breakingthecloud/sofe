#!/usr/bin/env python3
"""IAM gap check — does the customer role grant what the engine actually calls? (S111 Phase C, AC9)

    python scripts/iam_gap_check.py ../sofe-platform/public/cfn/sofe-read-only-role-v2.yaml [--validate]

1. Scans ``sofe/collectors`` (AST, no AWS access) for every ``client.<method>(...)`` /
   ``get_paginator('<op>')`` and turns each into the IAM action AWS authorizes it with.
2. Reads the role's policies from the CloudFormation template and checks that each required
   action is allowed by some ``Allow`` pattern and not hit by a ``Deny``.
3. With ``--validate`` (needs AWS credentials), asks IAM Access Analyzer whether every action
   name in the template and every derived action actually exists. This is what catches a
   misnamed action: IAM accepts ``s3:GetBucketEncryption`` in a policy without complaint,
   it simply never matches anything.

Exit status 1 when a required action is missing/denied or a name does not exist.
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import json
import pathlib
import subprocess
import sys
from collections import defaultdict

import boto3
import yaml

COLLECTORS_DIR = pathlib.Path(__file__).resolve().parent.parent / "sofe" / "collectors"

# boto3 service name -> IAM service prefix, when they differ
IAM_PREFIX = {"elbv2": "elasticloadbalancing", "elb": "elasticloadbalancing"}

# API operation -> IAM action, when they differ (S3 names its permissions after the resource, not the call)
OPERATION_TO_ACTION = {
    ("s3", "ListBuckets"): "s3:ListAllMyBuckets",
    ("s3", "GetBucketEncryption"): "s3:GetEncryptionConfiguration",
    ("s3", "GetBucketLifecycleConfiguration"): "s3:GetLifecycleConfiguration",
    ("s3", "GetPublicAccessBlock"): "s3:GetBucketPublicAccessBlock",
}

# API Gateway v1 authorizes every read with a single verb-style action
SERVICE_SINGLE_ACTION = {"apigateway": "apigateway:GET"}

# Calls that need no permission
NO_PERMISSION = {("sts", "GetCallerIdentity")}

# Calls the engine only makes as a fallback for old SDKs. s3:HeadBucket is authorized by s3:ListBucket, which
# would also let the role list object keys, so the hosted role deliberately does not grant it: the hosted
# Lambda runs a boto3 whose ListBuckets filters by region server-side and never reaches that path.
OPTIONAL = {("s3", "HeadBucket")}

# The Billing API credit operations are signed manually (JSON 1.0 + SigV4), not called
# through a boto3 client, so the AST scan cannot see them.
STATIC_ACTIONS = {
    "billing:GetCredits": "sofe/billing/credits.py: GetCredits",
    "billing:GetCreditAllocationHistory": "sofe/billing/credits.py: GetCreditAllocationHistory",
}


def _models():
    cache: dict[str, object] = {}

    def get(service: str):
        if service not in cache:
            cache[service] = boto3.client(service, region_name="us-east-1", aws_access_key_id="x", aws_secret_access_key="x")
        return cache[service]

    return get


def _client_services(tree: ast.AST) -> dict[str, set[str]]:
    """variable name -> boto3 services assigned to it (``x = <anything>.client('svc', ...)``)."""
    found: dict[str, set[str]] = defaultdict(set)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            call = node.value
            if isinstance(call.func, ast.Attribute) and call.func.attr == "client" and call.args and isinstance(call.args[0], ast.Constant):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        found[target.id].add(call.args[0].value)
    return found


def required_actions(collectors_dir: pathlib.Path = COLLECTORS_DIR) -> dict[str, list[str]]:
    """IAM action -> where the engine needs it (``file: service.method``)."""
    client_for = _models()
    needs: dict[str, set[str]] = defaultdict(set)
    for path in sorted(collectors_dir.rglob("*.py")):
        tree = ast.parse(path.read_text())
        variables = _client_services(tree)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)):
                continue
            services = variables.get(node.func.value.id)
            if not services or len(services) != 1:  # a name bound to two services in one file would need scope tracking
                continue
            (service,) = services
            method = node.func.attr
            if method == "get_paginator" and node.args and isinstance(node.args[0], ast.Constant):
                method = node.args[0].value
            mapping = client_for(service).meta.method_to_api_mapping
            if method not in mapping:
                continue  # not an API call (e.g. can_paginate, meta)
            operation = mapping[method]
            if (service, operation) in NO_PERMISSION or (service, operation) in OPTIONAL:
                continue
            action = (
                SERVICE_SINGLE_ACTION.get(service)
                or OPERATION_TO_ACTION.get((service, operation))
                or f"{IAM_PREFIX.get(service, service)}:{operation}"
            )
            needs[action].add(f"{path.name}: {service}.{method}")
    for action, source in STATIC_ACTIONS.items():
        needs[action].add(source)
    return {a: sorted(src) for a, src in sorted(needs.items())}


def template_statements(template: pathlib.Path) -> list[dict]:
    """All IAM policy statements of the role defined in a CloudFormation template."""

    class Loader(yaml.SafeLoader):
        pass

    for tag in ("!Ref", "!Sub", "!GetAtt", "!Join", "!If", "!Select", "!FindInMap", "!ImportValue", "!Equals", "!Not"):
        Loader.add_constructor(tag, lambda l, n: l.construct_scalar(n) if isinstance(n, yaml.ScalarNode) else l.construct_sequence(n))
    doc = yaml.load(template.read_text(), Loader=Loader)
    role = next(v for v in doc["Resources"].values() if v["Type"] == "AWS::IAM::Role")
    statements: list[dict] = []
    for policy in role["Properties"].get("Policies", []):
        statements += policy["PolicyDocument"]["Statement"]
    return statements


def _patterns(statements: list[dict], effect: str) -> list[str]:
    out: list[str] = []
    for s in statements:
        if s.get("Effect") == effect:
            actions = s.get("Action", [])
            out += [actions] if isinstance(actions, str) else list(actions)
    return out


def _matches(action: str, patterns: list[str]) -> bool:
    # IAM action names are case-insensitive
    return any(fnmatch.fnmatchcase(action.lower(), p.lower()) for p in patterns)


def gaps(needed: dict[str, list[str]], statements: list[dict]) -> tuple[list[str], list[str]]:
    """(not allowed by any Allow, hit by a Deny)."""
    allow, deny = _patterns(statements, "Allow"), _patterns(statements, "Deny")
    missing = [a for a in needed if not _matches(a, allow)]
    denied = [a for a in needed if _matches(a, allow) and _matches(a, deny)]
    return missing, denied


def nonexistent_actions(actions: list[str], profile: str | None = None) -> list[str]:
    """Action names IAM Access Analyzer says do not exist (needs AWS credentials)."""
    doc = json.dumps({"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Action": sorted(set(actions)), "Resource": "*"}]})
    cmd = ["aws", "accessanalyzer", "validate-policy", "--region", "us-east-1", "--policy-type", "IDENTITY_POLICY", "--policy-document", doc, "--output", "json"]
    if profile:
        cmd += ["--profile", profile]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Access Analyzer validation failed: {result.stderr.strip()[:200]}")
    bad: list[str] = []
    for f in json.loads(result.stdout)["findings"]:
        if f["issueCode"] == "INVALID_ACTION":
            bad.append(f["findingDetails"].replace("The action ", "").replace(" does not exist.", ""))
    return sorted(bad)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("template", type=pathlib.Path)
    ap.add_argument("--validate", action="store_true", help="also check action names with IAM Access Analyzer (needs AWS credentials)")
    ap.add_argument("--profile", help="AWS profile for --validate")
    args = ap.parse_args(argv)

    needed = required_actions()
    statements = template_statements(args.template)
    missing, denied = gaps(needed, statements)

    print(f"engine needs {len(needed)} distinct IAM actions; template: {args.template.name}")
    for title, items in (("MISSING (not allowed by any statement)", missing), ("DENIED (allowed but also denied)", denied)):
        print(f"\n{title}: {len(items)}")
        for a in items:
            print(f"  - {a}   <- {', '.join(needed[a][:2])}")

    bad: list[str] = []
    if args.validate:
        allowed_in_template = [x for x in _patterns(statements, "Allow") if "*" not in x]
        bad = nonexistent_actions(list(needed) + allowed_in_template, args.profile)
        print(f"\nNAMES THAT DO NOT EXIST (Access Analyzer): {len(bad)}")
        for a in bad:
            print(f"  - {a}")

    ok = not (missing or denied or bad)
    print("\nRESULT:", "0 gaps" if ok else "GAPS FOUND")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
