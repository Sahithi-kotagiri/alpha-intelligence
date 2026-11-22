"""Load versioned YAML configuration (thresholds, rubrics, decision policy)."""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"


@lru_cache
def load(name: str) -> dict[str, Any]:
    path = CONFIG_DIR / f"{name}.yaml"
    with path.open() as f:
        data = yaml.safe_load(f)
    data["_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    return data


def thresholds() -> dict[str, Any]:
    return load("thresholds")


def rubrics() -> dict[str, Any]:
    return load("rubrics")


def policy() -> dict[str, Any]:
    return load("decision_policy")


def versions() -> dict[str, str]:
    return {"rulebook": load("rulebook")["version"], "thresholds": thresholds()["version"], "rubrics": rubrics()["version"],
            "decision_policy": policy()["version"]}
