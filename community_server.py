"""
SOFE Community Server — Basic HTTP API for self-hosted users.

Run: uvicorn community_server:app --host 0.0.0.0 --port 8080
Or: docker run -p 8080:8080 ghcr.io/breakingthecloud/sofe-community

No auth, no rate limiting, no history. Just the engine over HTTP.
For Pro features (scheduled evals, alerts, dashboard), use platform.sofe.dev
"""

from __future__ import annotations
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI
from pydantic import BaseModel
from typing import Optional

from sofe.collectors import collect_all
from sofe.collectors.aws import COLLECTORS, ALL_TYPES
from sofe.loader import load_policies
from sofe.engine import evaluate

app = FastAPI(
    title="SOFE Community Server",
    version="0.2.0",
    description="Open FinOps Engine — Community Edition. No auth, no limits.",
)


class EvaluateRequest(BaseModel):
    profile: Optional[str] = None
    resource_types: Optional[list[str]] = None
    regions: Optional[list[str]] = None
    policies_dir: str = "./policies"


@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.2.0", "edition": "community", "collectors": len(COLLECTORS), "policies": 20}


@app.post("/evaluate")
async def evaluate_endpoint(req: EvaluateRequest):
    resources = collect_all(
        profile=req.profile,
        resource_types=req.resource_types,
        regions=req.regions,
    )
    policies = load_policies(req.policies_dir)
    findings = evaluate(resources, policies)

    return {
        "evaluation_id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "resources_scanned": len(resources),
        "policies_evaluated": len(policies),
        "findings_count": len(findings),
        "findings": [
            {
                "id": str(uuid.uuid4()),
                "policy_name": f.policy_name,
                "severity": f.severity,
                "resource_id": f.resource_id,
                "resource_type": f.resource_type,
                "region": f.region,
                "message": f.message,
            }
            for f in findings
        ],
    }


@app.get("/policies")
async def list_policies(policies_dir: str = "./policies"):
    policies = load_policies(policies_dir)
    return [
        {"name": p.metadata.name, "description": p.metadata.description, "severity": p.spec.severity.value}
        for p in policies
    ]


@app.get("/collectors")
async def list_collectors():
    return [
        {"name": cls.__name__.replace("Collector", ""), "resource_type": rt}
        for rt, cls in COLLECTORS.items()
    ]


@app.post("/validate")
async def validate_endpoint(body: dict):
    """Validate policy YAML syntax."""
    from sofe.loader import validate_policies
    yaml_content = body.get("policy_yaml", "")
    if not yaml_content:
        return {"valid": False, "errors": ["No policy_yaml provided"]}
    try:
        import tempfile, os
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            f.flush()
            policies = load_policies(os.path.dirname(f.name))
        os.unlink(f.name)
        return {"valid": True, "errors": [], "policies_parsed": len(policies)}
    except Exception as e:
        return {"valid": False, "errors": [str(e)]}
