"""Bundler aliases cover every pack element root the registry generator imports.

The generator writes ``import ... from '@pack-<id>-elements-<kind>/<el>/component?...'``
for each element it discovers. ``remotion/webpack-alias.mjs`` (used by the
``remotion.config.ts`` render path and by the smoke bundle) must alias each of
those scopes, or the bundle fails on the first pack that is not hard-coded.
These tests run the real Node alias derivation and compare it with the real
generator output.
"""

import json
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
REMOTION = ROOT / "remotion"
ALIAS_MODULE = REMOTION / "webpack-alias.mjs"
IMPORT_SCOPE_RE = re.compile(r"'@(pack-[A-Za-z0-9_-]+?-elements-(?:effects|animations|transitions))/")


def _node_pack_aliases(extra_env: dict[str, str] | None = None) -> dict[str, str]:
    node = shutil.which("node")
    if node is None:
        raise unittest.SkipTest("node is not installed; cannot evaluate remotion/webpack-alias.mjs")
    env = {**os.environ, **(extra_env or {})}
    script = (
        f"import({json.dumps(ALIAS_MODULE.as_uri())})"
        ".then((m) => process.stdout.write(JSON.stringify(m.packElementAliases())))"
    )
    completed = subprocess.run(
        [node, "--input-type=module", "-e", script],
        cwd=str(REMOTION),
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    if completed.returncode != 0:
        raise AssertionError(f"webpack-alias.mjs failed:\n{completed.stderr}")
    return json.loads(completed.stdout)


class RemotionPackElementAliasTest(unittest.TestCase):
    def test_every_generated_pack_import_scope_has_an_alias(self) -> None:
        spec = importlib.util.spec_from_file_location(
            "gen_effect_registry_for_alias_test", ROOT / "scripts" / "gen_effect_registry.py"
        )
        assert spec is not None and spec.loader is not None
        generator = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = generator  # dataclasses resolve the module by name
        spec.loader.exec_module(generator)

        scopes: set[str] = set()
        for kind in ("effects", "animations", "transitions"):
            scopes.update(IMPORT_SCOPE_RE.findall(generator.generate_element_registry(kind)))
        self.assertIn("pack-astrid_motion-elements-effects", scopes)

        aliases = _node_pack_aliases()
        missing = sorted(f"@{scope}" for scope in scopes if f"@{scope}" not in aliases)
        self.assertEqual(missing, [], "generator imports with no bundler alias")
        for scope in scopes:
            target = Path(aliases[f"@{scope}"])
            self.assertTrue(target.is_dir(), f"alias @{scope} points at a missing folder: {target}")

    def test_external_pack_root_from_astrid_packs_path_is_aliased(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            external = Path(tmp) / "external"
            pack = external / "extpack"
            (pack / "parts" / "effects" / "wobble").mkdir(parents=True)
            (pack / "pack.yaml").write_text(
                "schema_version: 2\nid: extpack\nname: External\nversion: 0.1.0\n"
                "capabilities: [elements]\ncontent:\n  elements: parts\n",
                encoding="utf-8",
            )
            aliases = _node_pack_aliases({"ASTRID_PACKS_PATH": str(external)})
            target = Path(aliases["@pack-extpack-elements-effects"])
            self.assertEqual(target.resolve(), (pack / "parts" / "effects").resolve())

    def test_remotion_config_uses_the_shared_alias_module(self) -> None:
        config = (REMOTION / "remotion.config.ts").read_text(encoding="utf-8")
        self.assertIn("applyRemotionPrimitiveAliases", config)
        self.assertNotIn("'@pack-local-elements-effects'", config)
        self.assertNotIn("'@pack-rendering-elements-effects'", config)

    def test_tsconfig_typechecks_every_in_tree_pack_element_root(self) -> None:
        text = (REMOTION / "tsconfig.json").read_text(encoding="utf-8")
        # tsconfig is JSONC: drop whole-line comments before parsing.
        config = json.loads(re.sub(r"^\s*//.*$", "", text, flags=re.MULTILINE))
        include = set(config["include"])
        self.assertIn("../astrid/packs/*/elements/**/*.ts", include)
        self.assertIn("../astrid/packs/*/elements/**/*.tsx", include)
        # Brace globs never match in tsc, so they must not be relied on.
        self.assertFalse(any("{ts,tsx}" in pattern and "/packs/" in pattern for pattern in include))
        self.assertIn("@remotion/*", config["compilerOptions"]["paths"])


if __name__ == "__main__":
    unittest.main()
