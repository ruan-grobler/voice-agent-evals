"""JSON Schema validation for the YAML files people edit by hand."""

from __future__ import annotations

import json
from functools import cache
from importlib import resources
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator


class ConfigError(ValueError):
    """A scenario, business or agent file is missing or invalid."""


@cache
def _validator(name: str) -> Draft202012Validator:
    text = (
        resources.files("voice_agent_evals")
        .joinpath("schemas", f"{name}.schema.json")
        .read_text(encoding="utf-8")
    )
    return Draft202012Validator(
        json.loads(text), format_checker=Draft202012Validator.FORMAT_CHECKER
    )


def validate(data: Any, schema_name: str, source: Path) -> None:
    errors = sorted(_validator(schema_name).iter_errors(data), key=lambda e: list(e.path))
    if errors:
        lines = [f"{source}: invalid {schema_name} file"]
        for err in errors[:10]:
            where = "/".join(str(p) for p in err.path) or "(top level)"
            lines.append(f"  {where}: {err.message}")
        raise ConfigError("\n".join(lines))


def load_yaml(path: Path, schema_name: str) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigError(f"{path}: file not found")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: not valid YAML ({exc})") from exc
    validate(data, schema_name, path)
    if not isinstance(data, dict):  # pragma: no cover - the schemas require an object
        raise ConfigError(f"{path}: expected a mapping at the top level")
    return data
