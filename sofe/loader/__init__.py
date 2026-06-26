"""Load and validate YAML policy files."""

import os
import yaml
from pathlib import Path
from ..models import Policy


def load_policies(path: str) -> list[Policy]:
    """Load all .yaml policy files from a directory or single file."""
    policies = []
    p = Path(path)

    if p.is_file():
        policies.append(_load_single(p))
    elif p.is_dir():
        for f in sorted(p.glob("*.yaml")):
            policies.append(_load_single(f))
        for f in sorted(p.glob("*.yml")):
            policies.append(_load_single(f))
    else:
        raise FileNotFoundError(f"Policy path not found: {path}")

    return policies


def _load_single(path: Path) -> Policy:
    """Load and validate a single policy file."""
    with open(path) as f:
        data = yaml.safe_load(f)

    if not data:
        raise ValueError(f"Empty policy file: {path}")

    try:
        return Policy(**data)
    except Exception as e:
        raise ValueError(f"Invalid policy {path.name}: {e}")


def validate_policies(path: str) -> list[dict]:
    """Validate policies without evaluating. Returns list of {file, valid, error}."""
    results = []
    p = Path(path)
    files = [p] if p.is_file() else list(p.glob("*.yaml")) + list(p.glob("*.yml"))

    for f in files:
        try:
            _load_single(f)
            results.append({"file": f.name, "valid": True, "error": None})
        except Exception as e:
            results.append({"file": f.name, "valid": False, "error": str(e)})

    return results
