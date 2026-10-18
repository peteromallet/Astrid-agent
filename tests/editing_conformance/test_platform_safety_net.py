"""Platform step 0: the safety-net fixtures (production/review/merge/TIMELINE-PLATFORM.md, section 7).

These tests are written to FAIL TODAY where the platform is known to be broken, as strict xfails with
the reason. When a step-1/step-2 fix lands, the strict xfail turns into a failure ("XPASS(strict)"),
which is the signal to remove the marker. Do not weaken the assertions to make them pass.

(a) A CLI-made film keeps all Astrid intent through a simulated App load, a no-op save, one edit, a save
    and a CLI checkout. The App side is simulated from the App's known-key rebuild: the App source
    (astrid-app, Codex's checkout) is not reachable from this worktree, so the simulation mirrors
    `ALLOWED_TOP_LEVEL_KEYS` (timeline-domain.ts:217-226), `withCanonicalClips` (timeline-domain.ts:1241-1250)
    and the `app` replace on save (timeline-patch.ts:1261-1266), as the platform doc describes them.
(b) A track with role "source" is excluded from render (today the Astrid validator rejects `role`).
(c) A preview-vs-render parity harness: structure only, skipped until the App preview is reachable.

Run: python -m pytest -q tests/editing_conformance/test_platform_safety_net.py
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from astrid.core.timeline.banodoco_composer import Timeline
from astrid.sdk import timeline_checkout as tc
from astrid.sdk import timeline_intent as intent
from astrid.sdk.timeline_checkout import Checkout

REPO = Path(__file__).resolve().parents[2]
TINY = REPO / "tests" / "fixtures" / "timeline_editing" / "tiny.json"
PROJECT = "P"
TIMELINE = "t"
RESOLVED = ("p", "t", "rev-0")

# The App's top-level config keys that survive its rebuild on save (timeline-domain.ts:217-226, as the
# platform doc records them). Anything else in parent.config (chapters, slots, cut_notes) is dropped.
APP_KNOWN_CONFIG_KEYS = frozenset(
    {"output", "tracks", "clips", "pinnedShotGroups", "theme", "theme_overrides", "generation_defaults", "app"}
)

M1 = (
    "M1 (TIMELINE-PLATFORM.md): an App save rebuilds parent.config from a fixed key list, so Astrid's "
    "film-level intent (chapters, slots, cut_notes) is dropped; Astrid keeps it outside the shared schema"
)
M2 = (
    "M2 (TIMELINE-PLATFORM.md): Astrid's 0.0.1 validator forbids tracks[].role, so a Source track is "
    "rejected at render or exported instead of excluded"
)


@pytest.fixture
def film(tmp_path, monkeypatch):
    """A CLI film: tiny.json with Astrid intent set through the intent module, checked out as a draft."""
    monkeypatch.setenv("ASTRID_RUNTIME_DATA_ROOT", str(tmp_path / "data"))
    bundle = json.loads(TINY.read_text(encoding="utf-8"))
    monkeypatch.setattr(tc, "resolve_ids", lambda project, timeline, client=None: RESOLVED)
    monkeypatch.setattr(tc, "fetch_bundle", lambda project, timeline, revision_id=None, client=None: copy.deepcopy(bundle))

    tl = Checkout.draft(PROJECT, TIMELINE)
    clips = {c["id"]: c for c in tl.bundle["shots"]["A"]["internal_timeline"]["clips"]}
    intent.set_formula(clips["a-rocket"], "params.x", {"word": "viral", "field": "start"})
    intent.set_sequence(clips["a-rocket"], "seq-1", 0)
    intent.set_sequence(clips["a-plate"], "seq-1", 1)
    intent.set_chapters(tl.bundle, [{"name": "01 OPEN", "from": "c1"}])
    intent.slots(tl.bundle)["hero"] = {"kind": "footage", "spec": {"seconds": 4}}
    intent.set_cut_note(tl.bundle, "c1", why="the opener", hold="deliberate")
    tl.save()
    return tmp_path


def _draft_file() -> Path:
    draft = tc.find_draft(PROJECT, TIMELINE)
    assert draft is not None, "the film has no working copy"
    return Path(draft)


def _app_round_trip(path: Path) -> None:
    """App load, no-op save, one edit (a-rocket x 10 -> 120), save: all on the working-copy file."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    config = doc["parent"]["config"]
    # App load + no-op save: the rebuild keeps only the App's known top-level keys.
    doc["parent"]["config"] = {k: v for k, v in config.items() if k in APP_KNOWN_CONFIG_KEYS}
    # One edit, on a clip the App owns.
    for clip in doc["shots"]["A"]["internal_timeline"]["clips"]:
        if clip["id"] == "a-rocket":
            clip["params"]["x"] = 120
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")


def _clip(bundle: dict, clip_id: str) -> dict:
    return next(c for c in bundle["shots"]["A"]["internal_timeline"]["clips"] if c["id"] == clip_id)


def test_clip_level_intent_survives_app_round_trip(film):
    """Clip-level `app` (formulas, sequence) rides the App's per-clip `app` and survives the round trip."""
    _app_round_trip(_draft_file())
    tl = Checkout.draft(PROJECT, TIMELINE)  # the CLI checkout again
    rocket = _clip(tl.bundle, "a-rocket")
    assert rocket["params"]["x"] == 120, "the one App edit is missing"
    assert intent.formulas(rocket) == {"params.x": {"word": "viral", "field": "start"}}
    assert intent.sequence(rocket) == ("seq-1", 0)
    assert intent.sequence(_clip(tl.bundle, "a-plate")) == ("seq-1", 1)


def test_film_level_intent_survives_app_round_trip(film):
    """Chapters, slots and cut notes (parent.config) survive App save -> one edit -> CLI checkout."""
    _app_round_trip(_draft_file())
    tl = Checkout.draft(PROJECT, TIMELINE)
    assert _clip(tl.bundle, "a-rocket")["params"]["x"] == 120, "the one App edit is missing"
    assert intent.chapters(tl.bundle) == [{"name": "01 OPEN", "from": "c1"}], "chapters lost"
    assert "hero" in intent.slots(tl.bundle), "slots lost"
    assert intent.cut_notes(tl.bundle).get("c1", {}).get("why") == "the opener", "cut notes lost"


def _source_track_config() -> dict:
    """A raw TimelineConfig with an output track and a Source-role track, one clip on each."""
    return {
        "tracks": [
            {"id": "main", "kind": "visual", "label": "Main"},
            {"id": "reference", "kind": "visual", "label": "Reference", "role": "source"},
        ],
        "clips": [
            {"id": "keep", "at": 0.0, "track": "main", "clipType": "text", "hold": 2.0,
             "text": {"content": "OUTPUT"}, "params": {}},
            {"id": "ref", "at": 0.0, "track": "reference", "clipType": "text", "hold": 2.0,
             "text": {"content": "SOURCE"}, "params": {}},
        ],
    }


@pytest.mark.xfail(strict=True, reason=M2)
def test_source_role_track_is_excluded_from_render():
    """A track with role "source" is reference material: the render payload must not contain it."""
    config = _source_track_config()
    payload = Timeline(config).to_json_data()  # the render-side validation must accept the role
    track_ids = {t["id"] for t in payload["tracks"]}
    clip_ids = {c["id"] for c in payload["clips"]}
    assert "reference" not in track_ids, "the Source track is in the render payload"
    assert "ref" not in clip_ids, "a clip on the Source track is in the render payload"
    assert "keep" in clip_ids


@pytest.mark.skip(reason=(
    "step-0 skeleton (c): needs the App preview (timeline-composition, a Codex-owned package not reachable "
    "from this worktree) and a frame capture of `tiny` through the Astrid render; fill in when both are wired"
))
@pytest.mark.parametrize("frame_ms", [0, 1000, 2000, 3500])
def test_preview_matches_render_frame(frame_ms):
    """Parity: the App preview and the Astrid render agree on `tiny` at each sampled frame."""
    preview = None  # TODO: App preview frame at frame_ms (composition evaluator)
    render = None  # TODO: Astrid render frame at frame_ms (same timeline, Remotion)
    assert preview is not None and render is not None
    assert preview == render
