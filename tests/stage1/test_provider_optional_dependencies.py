from __future__ import annotations

import ast
from pathlib import Path
import tomllib


ROOT = Path(__file__).resolve().parents[2]
PROVIDER_REQUIREMENTS = {
    "openai": "openai>=1.55,<3",
    "anthropic": "anthropic>=0.40.0",
    "google-genai": "google-genai>=1.15.0",
    "fal-client": "fal-client>=0.7.0",
}


def _name(requirement: str) -> str:
    return requirement.split("[", 1)[0].split("<", 1)[0].split(">", 1)[0].split("=", 1)[0].strip().lower()


def test_provider_sdks_are_opt_in_metadata() -> None:
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    base = metadata["project"]["dependencies"]
    providers = metadata["project"]["optional-dependencies"]["providers"]

    assert {_name(item) for item in base}.isdisjoint(PROVIDER_REQUIREMENTS)
    assert providers == list(PROVIDER_REQUIREMENTS.values())


def test_provider_sdk_imports_are_feature_gated() -> None:
    provider_modules = {"openai", "anthropic", "fal_client", "google", "google.genai"}
    allowed_top_level = {
        # This module is itself the FAL-backed executor. It is not imported by
        # base setup or discovery; importing the provider feature requires the
        # explicit ``providers`` extra.
        "astrid/packs/media/executors/speech_repair_lavasr/run.py",
    }
    observed: set[str] = set()

    for path in (ROOT / "astrid").rglob("*.py"):
        relative = path.relative_to(ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            imported: set[str] = set()
            if isinstance(node, ast.Import):
                imported = {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported = {node.module}
            if not imported.intersection(provider_modules):
                continue
            observed.add(relative)

    # Check the module body explicitly; nested provider imports are lazy.
    for path in (ROOT / "astrid").rglob("*.py"):
        relative = path.relative_to(ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            imported: set[str] = set()
            if isinstance(node, ast.Import):
                imported = {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported = {node.module}
            if imported.intersection(provider_modules):
                assert relative in allowed_top_level

    assert allowed_top_level <= observed
