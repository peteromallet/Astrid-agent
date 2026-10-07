from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from astrid.core import timeline
from astrid.core.timeline import arrangement_compiler


def _dialogue_case(count: int, duration: float) -> tuple[dict, dict]:
    entries = []
    clips = []
    for index in range(count):
        order = index + 1
        start = 100.0 + index * 20.0
        end = start + duration
        entry_id = f"audio-{order}"
        entries.append(
            {
                "id": entry_id,
                "kind": "source",
                "category": "dialogue",
                "src_start": start,
                "src_end": end,
            }
        )
        clips.append(
            {
                "order": order,
                "uuid": f"clip-{order}",
                "audio_source": {"pool_id": entry_id, "trim_sub_range": [start, end]},
                "visual_source": None,
                "text_overlay": {"content": "HOOK", "style_preset": "title"} if order == 2 else None,
                "rationale": f"Beat {order}",
            }
        )
    visual = {
        "id": "visual-overlay",
        "kind": "source",
        "category": "visual",
        "src_start": 300.0,
        "src_end": 308.0,
    }
    entries.append(visual)
    if count > 1:
        clips[1]["visual_source"] = {
            "pool_id": visual["id"],
            "role": "overlay",
            "params": {"fit": "cover"},
        }
    return (
        {"entries": entries},
        {"target_duration_sec": count * duration, "clips": list(reversed(clips))},
    )


def test_public_export_and_core_module_share_one_implementation() -> None:
    assert timeline.compile_arrangement_plan is arrangement_compiler.compile_arrangement_plan
    for name in arrangement_compiler.__all__:
        assert getattr(timeline, name) is getattr(arrangement_compiler, name)

    implementation = Path(inspect.getsourcefile(arrangement_compiler.compile_arrangement_plan) or "")
    assert implementation == Path(arrangement_compiler.__file__)
    assert "def compile_arrangement_plan" in implementation.read_text(encoding="utf-8")
    assert "astrid.packs" not in implementation.read_text(encoding="utf-8")


def test_valid_plan_preserves_ordering_and_exact_output_shape() -> None:
    pool, arrangement = _dialogue_case(count=9, duration=8.0)
    visual = pool["entries"][-1]
    audio_entries = {entry["id"]: entry for entry in pool["entries"] if entry["category"] == "dialogue"}

    expected = []
    for order in range(1, 10):
        start = 100.0 + (order - 1) * 20.0
        expected.append(
            {
                "order": order,
                "uuid": f"clip-{order}",
                "at": (order - 1) * 8.0,
                "duration": 8.0,
                "audio_entry": audio_entries[f"audio-{order}"],
                "audio_trim_start": start,
                "overlay_entry": visual if order == 2 else None,
                "overlay_play_duration": 8.0 if order == 2 else None,
                "visual_params": {"fit": "cover"} if order == 2 else None,
                "role": "overlay" if order == 2 else "primary",
                "text_overlay": {"content": "HOOK", "style_preset": "title"} if order == 2 else None,
                "rationale": f"Beat {order}",
            }
        )

    assert timeline.compile_arrangement_plan(arrangement, pool) == expected


@pytest.mark.parametrize(
    ("count", "duration"),
    [(7, 10.0), (10, 9.5)],
)
def test_compiler_accepts_its_distinct_70_to_95_second_boundaries(count: int, duration: float) -> None:
    pool, arrangement = _dialogue_case(count=count, duration=duration)
    planned = timeline.compile_arrangement_plan(arrangement, pool)
    assert sum(plan["duration"] for plan in planned) == duration * count
    assert timeline.TOTAL_DURATION_BOUNDS == (70.0, 95.0)

    signature = inspect.signature(timeline.validate_arrangement_duration_window)
    assert signature.parameters["min_sec"].default == 75.0
    assert signature.parameters["max_sec"].default == 90.0


def test_invalid_trim_and_total_duration_errors_are_unchanged() -> None:
    pool, arrangement = _dialogue_case(count=9, duration=8.0)
    next(clip for clip in arrangement["clips"] if clip["order"] == 1)["audio_source"]["trim_sub_range"] = [98.0, 109.0]
    with pytest.raises(ValueError) as error:
        timeline.compile_arrangement_plan(arrangement, pool)
    assert str(error.value) == (
        "Arrangement clip 1 trim_sub_range [98.000, 109.000] falls outside "
        "audio pool entry audio-1 [100.000, 108.000]"
    )

    pool, arrangement = _dialogue_case(count=1, duration=8.0)
    with pytest.raises(ValueError) as error:
        timeline.compile_arrangement_plan(arrangement, pool)
    assert str(error.value) == (
        "Arrangement total duration 8.00s undershoots the allowed 70-95s window by 62.00s. "
        "Longest clips: order=1 duration=8.00s role=primary audio=audio-1 overlay=none"
    )

    pool, arrangement = _dialogue_case(count=10, duration=10.0)
    with pytest.raises(ValueError) as error:
        timeline.compile_arrangement_plan(arrangement, pool)
    assert str(error.value) == (
        "Arrangement total duration 100.00s overshoots the allowed 70-95s window by 5.00s. "
        "Longest clips: order=1 duration=10.00s role=primary audio=audio-1 overlay=none, "
        "order=2 duration=10.00s role=overlay audio=audio-2 overlay=visual-overlay, "
        "order=3 duration=10.00s role=primary audio=audio-3 overlay=none"
    )


def test_generative_only_plan_uses_target_duration_slots_without_total_validation() -> None:
    entries = [
        {"id": f"gen-{order}", "kind": "generative", "category": "visual"}
        for order in range(1, 4)
    ]
    arrangement = {
        "target_duration_sec": 75.0,
        "clips": [
            {
                "order": order,
                "uuid": f"gen-clip-{order}",
                "audio_source": None,
                "visual_source": {"pool_id": f"gen-{order}", "role": "stinger", "params": {"seed": order}},
            }
            for order in reversed(range(1, 4))
        ],
    }

    assert timeline.compile_arrangement_plan(arrangement, {"entries": entries}) == [
        {
            "order": order,
            "uuid": f"gen-clip-{order}",
            "at": (order - 1) * 25.0,
            "duration": 25.0,
            "audio_entry": None,
            "audio_trim_start": None,
            "overlay_entry": entries[order - 1],
            "overlay_play_duration": None,
            "visual_params": {"seed": order},
            "role": "stinger",
            "text_overlay": None,
            "rationale": None,
        }
        for order in range(1, 4)
    ]
