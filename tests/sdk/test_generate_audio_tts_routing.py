from __future__ import annotations

import pytest

from astrid.sdk import exceptions as sdk_exceptions
from astrid.sdk import invocation


class _StopAfterResolve(Exception):
    """Raised by the fake capability lookup so invoke() stops before admission."""


def _capture_capability_id(monkeypatch) -> dict:
    seen: dict = {}

    def get_capability(capability_id, **kwargs):
        seen["capability_id"] = capability_id
        raise _StopAfterResolve()

    fake_sdk = type("FakeSdk", (), {
        "_load_registries": staticmethod(lambda **kwargs: None),
        "get_capability": staticmethod(get_capability),
    })
    monkeypatch.setattr(invocation, "_sdk_module", lambda: fake_sdk)
    return seen


def test_audio_tts_mode_resolves_to_generate_speech(monkeypatch) -> None:
    seen = _capture_capability_id(monkeypatch)
    with pytest.raises(_StopAfterResolve):
        invocation.invoke(
            "generation.generate_audio",
            kind="executor",
            inputs={"mode": "tts", "text": "One year ago.", "voice": "en-US-AndrewMultilingualNeural",
                    "rate": "+12%", "model": "ignored", "execution": "cloud"},
        )
    assert seen["capability_id"] == "generation.generate_speech"


def test_audio_music_mode_stays_on_generate_audio(monkeypatch) -> None:
    seen = _capture_capability_id(monkeypatch)
    with pytest.raises(_StopAfterResolve):
        invocation.invoke(
            "generation.generate_audio",
            kind="executor",
            inputs={"mode": "music", "model": "ace-step", "execution": "cloud", "prompt": "lo-fi beat"},
        )
    assert seen["capability_id"] == "generation.generate_audio"


def test_tts_maps_prompt_alias_and_passes_voice_settings() -> None:
    speech = invocation._speech_inputs_from_audio_request(
        {"mode": "tts", "prompt": "Exact words.", "voice": "en-GB-RyanNeural", "rate": "+5%",
         "volume": "+0%", "pitch": "+0Hz", "provider": "edge-tts", "execution": "cloud"}
    )
    assert speech == {"text": "Exact words.", "voice": "en-GB-RyanNeural", "rate": "+5%",
                      "volume": "+0%", "pitch": "+0Hz", "provider": "edge-tts"}


def test_tts_rejects_music_only_inputs() -> None:
    with pytest.raises(sdk_exceptions.CapabilityValidationError, match="unsupported input"):
        invocation._speech_inputs_from_audio_request({"mode": "tts", "text": "Hi.", "count": 2})


def test_tts_requires_non_blank_text() -> None:
    with pytest.raises(sdk_exceptions.CapabilityValidationError, match="requires non-empty text"):
        invocation._speech_inputs_from_audio_request({"mode": "tts", "text": "   "})


def test_tts_rejects_conflicting_text_and_prompt() -> None:
    with pytest.raises(sdk_exceptions.CapabilityValidationError, match="conflicting"):
        invocation._speech_inputs_from_audio_request({"mode": "tts", "text": "A.", "prompt": "B."})
