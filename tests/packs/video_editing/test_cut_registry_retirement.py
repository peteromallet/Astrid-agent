from astrid.core.execution.executor.registry import load_default_registry


def test_cut_registration_has_one_v3_owner_and_preserves_behavioral_metadata():
    registry = load_default_registry()
    definitions = [definition for definition in registry._iter_all() if definition.id == "video_editing.cut"]

    assert len(definitions) == 1
    cut = definitions[0]
    assert cut.metadata["runtime_module"] == "astrid.packs.video_editing.actions.cut.run"
    assert cut.metadata["command_builder"] == "astrid.packs.video_editing.actions.hype.run.build_pool_steps"
    assert cut.metadata["pipeline_step"] == "cut"
    assert cut.metadata["pipeline_step_order"] == 10
    assert cut.pipeline_requirements == ("arrangement", "pool")
    assert cut.conditions[0].input == "brief"
    assert cut.cache.per_brief is True
    assert cut.cache.sentinels == ("hype.timeline.json", "hype.assets.json", "hype.metadata.json")
    assert cut.isolation.network is False
    assert cut.graph.depends_on[-1] == "editorial.arrange"
