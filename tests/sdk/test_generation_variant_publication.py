from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrid.core.contracts.schema import Output
from astrid.sdk.exceptions import CapabilityPreconditionError, CapabilityValidationError
from astrid.sdk.invocation import (
    _generation_publish_effect,
    _kernel_invoke,
    _generation_capability_modality,
    _resolve_generation_variant,
)
from astrid.sdk.results import Capability


DIGEST = "sha256:" + "a" * 64


class _Result:
    def __init__(self, data):
        self.ok = True
        self.data = data
        self.error = None


class _Generations:
    def __init__(self, *, project_id="project-1", version=4, variants=None):
        self.project_id = project_id
        self.version = version
        self._variants = variants if variants is not None else [
            {
                "variant_id": "variant-source",
                "generation_id": "generation-1",
                "object_id": DIGEST,
            }
        ]
        self.calls = []

    def show(self, project, generation_id):
        self.calls.append(("show", project, generation_id))
        return _Result(
            {
                "generation_id": generation_id,
                "project_id": self.project_id,
                "version": self.version,
            }
        )

    def variants(self, project, generation_id):
        self.calls.append(("variants", project, generation_id))
        return _Result([self._variants, None])


class _Projects:
    def show(self, project):
        return _Result({"project_id": "project-1", "slug": project})


def _client(generations=None):
    return SimpleNamespace(
        generations=generations or _Generations(),
        projects=_Projects(),
    )


def _capability(modality: str) -> Capability:
    port = {
        "image": "generated_images",
        "video": "generated_videos",
        "audio": "generated_audio",
    }[modality]
    artifact = {"image": "image", "video": "video/clip", "audio": "audio"}[modality]
    return Capability(
        id=f"generation.generate_{modality}",
        capability_type="executor",
        native_kind="executor",
        handle=SimpleNamespace(),
        outputs=(Output(name=port, type="file", artifact_type=artifact),),
    )


@pytest.mark.parametrize("modality", ["image", "video", "audio"])
@pytest.mark.parametrize("primary", ["preserve", "promote"])
def test_variant_publication_is_shared_across_generation_modalities(modality, primary):
    context = _resolve_generation_variant(
        _client(),
        project="project-1",
        variant_of={"generation_id": "generation-1", "variant_id": "variant-source"},
        primary=primary,
    )
    effect = _generation_publish_effect(
        _capability(modality),
        project="project-1",
        generation_intent={
            "version": 1,
            "modality": modality,
            "partial_success_policy": "reject",
            "groups": [{
                "group_key": "main",
                "selectors": [{
                    "selector": "main-0",
                    "ordinal": 0,
                    "variant_key": "original",
                }],
            }],
        },
        variant_context=context,
    )
    assert effect["effect_type"] == "generation.variant.append"
    assert effect["target_id"] == "generation-1"
    assert effect["expected_version"] == 4
    assert effect["payload"] == {
        "source_variant_id": "variant-source",
        "source_object_id": DIGEST,
        "variant_type": "edit",
        "output_name": {
            "image": "generated_images",
            "video": "generated_videos",
            "audio": "generated_audio",
        }[modality],
        "output_ordinal": 0,
        "primary_policy": primary,
    }


def test_variant_source_is_admitted_and_publication_controls_stay_out_of_executor_inputs():
    calls = []

    class Tasks:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                ok=True,
                data={"run_id": "run-1", "task_id": "task-1", "attempt_id": "attempt-1"},
            )

    context = _resolve_generation_variant(
        _client(),
        project="project-1",
        variant_of={"generation_id": "generation-1", "variant_id": "variant-source"},
        primary="promote",
    )
    _kernel_invoke(
        _capability("image"),
        kind="executor",
        project="project-1",
        inputs={"prompt": "keep this executor input"},
        outputs={},
        generation_intent={
            "version": 1,
            "modality": "image",
            "partial_success_policy": "reject",
            "groups": [{
                "group_key": "main",
                "selectors": [{
                    "selector": "main-0",
                    "ordinal": 0,
                    "variant_key": "original",
                }],
            }],
        },
        variant_context=context,
        _client=SimpleNamespace(tasks=Tasks()),
    )
    request = calls[0]
    assert request["input_manifest"] == [DIGEST]
    assert "variant_of" not in request["spec"]["inputs"]
    assert "primary" not in request["spec"]["inputs"]
    assert request["settlement_effect"]["effect_type"] == "generation.variant.append"


def test_variant_rejects_primary_promotion_without_variant_source():
    with pytest.raises(CapabilityValidationError, match="requires variant_of"):
        # This is the same public guard used by invoke() before admission.
        from astrid.sdk.invocation import _validate_variant_controls

        _validate_variant_controls(None, "promote", modality="image")


def test_non_generation_capability_cannot_consume_explicit_primary_control():
    from astrid.sdk.invocation import _validate_variant_controls

    with pytest.raises(CapabilityValidationError, match="only accepted for generation"):
        _validate_variant_controls(None, "preserve", modality=None, primary_supplied=True)


def test_variant_rejects_wrong_project_and_raw_media_variant():
    with pytest.raises(CapabilityPreconditionError, match="different project"):
        _resolve_generation_variant(
            _client(_Generations(project_id="project-elsewhere")),
            project="project-1",
            variant_of={"generation_id": "generation-1", "variant_id": "variant-source"},
            primary="preserve",
        )

    raw = _Generations(variants=[{
        "variant_id": "variant-source",
        "generation_id": "generation-1",
        "object_id": None,
    }])
    with pytest.raises(CapabilityPreconditionError, match="managed object"):
        _resolve_generation_variant(
            _client(raw),
            project="project-1",
            variant_of={"generation_id": "generation-1", "variant_id": "variant-source"},
            primary="preserve",
        )


def test_generation_publication_metadata_advertises_opt_in_controls():
    from astrid.sdk.invocation import get_capability

    for modality in ("image", "video", "audio"):
        capability = get_capability(
            f"generation.generate_{modality}", kind="executor", include_elements=False
        )
        publication = capability.definition["metadata"]["generation_publication"]
        assert publication["variant_of"]["type"] == "object"
        assert publication["primary"]["enum"] == ["preserve", "promote"]
        assert publication["primary"]["default"] == "preserve"


def test_new_generation_capability_can_opt_into_shared_publication_contract():
    capability = SimpleNamespace(
        id="custom.generate_storyboard",
        definition={
            "metadata": {
                "generation_publication": {
                    "modality": "image",
                    "variant_of": {"type": "object"},
                    "primary": {"enum": ["preserve", "promote"]},
                }
            }
        },
    )
    assert _generation_capability_modality(capability) == "image"
