from astrid.sdk.timeline_editing import add_authoring_shot, add_shot


def test_new_sdk_shots_are_silent_and_write_the_canonical_payload_name() -> None:
    simple = add_shot({}, shot_id="shot-simple")
    assert simple["id"] == "shot-simple"
    assert simple["timeline"] == {"tracks": [], "clips": []}
    assert "audio" not in simple

    bundle = {
        "timeline_id": "parent-timeline",
        "shots": {},
        "placements": [],
        "source_mapping": {"shots": {}, "placements": {}},
    }
    authored = add_authoring_shot(bundle, shot_id="shot-silent", name="Canonical title")

    assert authored["payload"]["name"] == "Canonical title"
    assert "audio" not in authored["payload"]
    assert authored["payload"]["audio_bindings"] == []
    assert authored["internal_timeline"]["clips"] == []


def test_new_sdk_shot_extension_fields_survive_authoring_copy() -> None:
    bundle = {
        "timeline_id": "parent-timeline",
        "shots": {
            "template": {
                "shot_id": "template",
                "payload": {
                    "name": "Template",
                    "audio_bindings": [{"binding_id": "voice", "asset": "voice-a"}],
                    "app": {"future": {"value": 1}},
                },
                "internal_timeline": {"tracks": [], "clips": []},
            },
        },
        "placements": [],
        "source_mapping": {
            "shots": {"template": {"shot_id": "template", "revision_id": "rev-1"}},
            "placements": {},
        },
    }
    duplicate = add_authoring_shot(bundle, template_shot_id="template", shot_id="copy")

    assert duplicate["payload"]["name"] == "Template"
    assert duplicate["payload"]["audio_bindings"] == [{"binding_id": "voice", "asset": "voice-a"}]
    assert duplicate["payload"]["app"] == {"future": {"value": 1}}
