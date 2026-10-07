"""Run-scoped API budget and rate tracking for dataset-build."""

from __future__ import annotations

import hashlib
import json
import re
import stat
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

DELEGATION_CEILINGS = {
    "max_children": 4096,
    "max_active_children": 64,
    "max_derived_objects": 1024,
    "max_derived_bytes": 4 * 1024**3,
    "max_child_inputs": 256,
    "max_child_bytes": 256 * 1024**2,
}


def delegation_preflight(config: Mapping[str, Any]) -> dict[str, Any]:
    """Plan finite child counts without admitting tasks or registering objects.

    This is a local preflight report, not a Runtime delegation grant. Derived
    objects/bytes still require exact attempt-scoped metering at invocation.
    The current caller code has no outer retries; idempotent SDK replays are
    one admission. All configured acquisition attempts are counted even when
    filtering or processed-source checks could discard them later.
    """
    extensions = config.get("extensions") or {}
    if isinstance(extensions, Mapping) and extensions.get("fixture_mode") is True:
        return {"required": False, "planned_children": 0, "limits": None}
    review = config.get("review") or {}
    top_up = review.get("top_up") or {}
    extra_rounds = top_up.get("max_rounds", 2)
    if type(extra_rounds) is not int or extra_rounds < 0:
        raise ValueError("review.top_up.max_rounds must be a nonnegative finite integer")
    rounds = 1 + extra_rounds
    youtube_sources = 0
    for source in config.get("sources", []) or []:
        if source.get("provider") != "youtube":
            continue
        provider_config = source.get("config") or {}
        dataset_config = provider_config.get("dataset_config")
        if not isinstance(dataset_config, Mapping):
            dataset_config = config
        # Mirror YouTube's configured-source inventory, including its bucket
        # queries. Do not deduplicate: each configured attempt can incur work.
        from .source_providers.youtube import _configured_sources

        youtube_sources += len(_configured_sources(provider_config, dataset_config))
    filters = config.get("filters") or {}
    model_stages = {
        "bucket_judge_filter",
        "transcript_keyword_filter",
        "semantic_visual_filter",
        "semantic_video_filter",
    }
    for stage in filters.get("stages", []) or []:
        stage_config = stage.get("config") or {}
        gate_config = stage_config.get("bucket_judge") or {}
        if (
            "budget_tracker" in stage_config
            or isinstance(gate_config, Mapping)
            and "budget_tracker" in gate_config
        ):
            raise ValueError("filter configuration cannot replace the run-scoped budget_tracker")
    model_enabled = any(
        stage.get("enabled", True) and stage.get("stage_id") in model_stages
        for stage in filters.get("stages", []) or []
    )
    caption = config.get("caption") or {"provider": "visual_understand"}
    model_enabled = model_enabled or caption.get("provider") in {
        "visual_understand",
        "video_understand",
    }
    budgets = config.get("budgets") or {}
    api_calls = budgets.get("max_api_calls", 0) if model_enabled else 0
    if type(api_calls) is not int or api_calls < 0 or model_enabled and api_calls == 0:
        raise ValueError(
            "budgets.max_api_calls must be positive and finite for model-backed child work"
        )
    review_calls = rounds if review.get("enabled", True) else 0
    acquisition_calls = youtube_sources * rounds
    planned_children = 2 * acquisition_calls + api_calls + review_calls
    if not planned_children:
        return {"required": False, "planned_children": 0, "limits": None}
    requested = budgets.get("delegation")
    if not isinstance(requested, Mapping) or "max_derived_bytes" not in requested:
        raise ValueError(
            "budgets.delegation.max_derived_bytes must be explicitly finite before child work"
        )
    if set(requested) - DELEGATION_CEILINGS.keys():
        raise ValueError("budgets.delegation contains unsupported limit names")
    limits = {
        "max_children": max(1, planned_children),
        "max_active_children": 1,
        "max_derived_objects": 256,
        "max_child_inputs": 256,
        "max_child_bytes": 256 * 1024**2,
        **requested,
    }
    for name, ceiling in DELEGATION_CEILINGS.items():
        value = limits[name]
        if type(value) is not int or not 0 < value <= ceiling:
            raise ValueError(f"budgets.delegation.{name} must be an integer in [1, {ceiling}]")
    if limits["max_children"] < planned_children:
        raise ValueError(
            f"delegation child budget cannot cover configured work: {planned_children} > {limits['max_children']}"
        )
    retained_outputs = 2 * acquisition_calls
    if retained_outputs > limits["max_derived_objects"]:
        raise ValueError(
            "delegation object budget cannot cover configured YouTube/Scenes retention: "
            f"{retained_outputs} > {limits['max_derived_objects']}"
        )
    return {
        "required": True,
        "planned_children": planned_children,
        "limits": limits,
        "components": {
            "rounds": rounds,
            "configured_youtube_sources": youtube_sources,
            "acquisition_calls": acquisition_calls,
            "scene_calls": acquisition_calls,
            "model_call_allowance_across_all_rounds": api_calls,
            "review_calls": review_calls,
            "outer_retry_admissions": 0,
        },
        "derived_accounting": {
            "planned_retained_outputs": retained_outputs,
            "sizes": "verified incrementally before materialization/registration",
        },
    }


class ChildWorkMeter:
    """Lifetime local checks; persisted Runtime policy remains the authority.

    Reserve before crossing a boundary and retain charges on uncertain failure.
    Retention uses association identity. Registration uses the object union and
    requires compatible producer descriptors, even when content hashes repeat.
    """

    OBJECT_MAX_BYTES = 64 * 1024**2

    def __init__(self, limits: Mapping[str, int]) -> None:
        if set(limits) != set(DELEGATION_CEILINGS):
            raise ValueError("child meter requires all six finite delegation limits")
        for name, ceiling in DELEGATION_CEILINGS.items():
            if type(limits[name]) is not int or not 0 < limits[name] <= ceiling:
                raise ValueError(f"invalid finite child meter limit: {name}")
        self.limits = dict(limits)
        self._children: dict[str, str] = {}
        self._retained: dict[str, dict[str, Any]] = {}
        self._registered: dict[str, dict[str, Any]] = {}

    @property
    def retained_bytes(self) -> int:
        return sum(row["size"] for row in self._retained.values())

    @property
    def registered_bytes(self) -> int:
        return sum(row["size"] for row in self._registered.values())

    def as_dict(self) -> dict[str, int]:
        return {
            "children": len(self._children),
            "retained_objects": len(self._retained),
            "retained_bytes": self.retained_bytes,
            "registered_objects": len(self._registered),
            "registered_bytes": self.registered_bytes,
        }

    def check_acquisition(self, child_keys: tuple[str, str]) -> None:
        # Replaying an immutable pair does not demand new admission capacity.
        new = sum(key not in self._children for key in child_keys)
        if len(self._children) + new > self.limits["max_children"]:
            raise RuntimeError("child admission budget exceeded before acquisition")
        if new == 2 and len(self._retained) + 2 > self.limits["max_derived_objects"]:
            raise RuntimeError("retention object budget exceeded before acquisition")

    def admit(self, key: str, capability: str, inputs: Mapping[str, Any]) -> None:
        identity = json.dumps([capability, inputs], sort_keys=True, allow_nan=False)
        previous = self._children.get(key)
        if previous is not None:
            if previous != identity:
                raise ValueError("child replay changed its immutable operation")
            return
        if len(self._children) >= self.limits["max_children"]:
            raise RuntimeError("child admission budget exceeded")
        self._children[key] = identity

    def _object(self, row: Mapping[str, Any]) -> dict[str, Any]:
        size, digest = row.get("size"), row.get("digest")
        if type(size) is not int or not 0 <= size <= self.OBJECT_MAX_BYTES:
            raise ValueError("child object size must be known and within 64 MiB")
        if (
            not isinstance(digest, str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None
            or row.get("object_id") != digest
        ):
            raise ValueError("child object requires matching object ID and digest")
        return dict(row)

    def retain(self, row: Mapping[str, Any]) -> None:
        descriptor = self._object(row)
        association = descriptor.get("association_id")
        if not isinstance(association, str) or not association:
            raise ValueError("retention requires an exact association")
        previous = self._retained.get(association)
        if previous is not None:
            if previous != descriptor:
                raise ValueError("retained association descriptor changed")
            return
        if (
            len(self._retained) + 1 > self.limits["max_derived_objects"]
            or self.retained_bytes + descriptor["size"] > self.limits["max_derived_bytes"]
        ):
            raise RuntimeError("retention object or byte budget exceeded")
        self._retained[association] = descriptor

    def check_registration(self, row: Mapping[str, Any]) -> None:
        descriptor = self._object(row)
        if descriptor["size"] > self.limits["max_child_bytes"]:
            raise RuntimeError("child input byte budget exceeded")
        if descriptor["object_id"] not in self._registered and (
            len(self._registered) + 1 > self.limits["max_derived_objects"]
            or self.registered_bytes + descriptor["size"] > self.limits["max_derived_bytes"]
        ):
            raise RuntimeError("derived registration object or byte budget exceeded")

    def register(self, row: Mapping[str, Any], producer: Mapping[str, str], *, name: str) -> None:
        self.check_registration(row)
        descriptor = self._object(row)
        if (
            set(producer) != {"filename", "media_type", "output_port"}
            or any(not isinstance(value, str) or not value for value in producer.values())
            or any(producer[key] != descriptor[key] for key in ("media_type", "output_port"))
        ):
            raise ValueError("registration requires the unchanged producer descriptor")
        registration = {
            "name": name,
            **producer,
            "object_id": descriptor["object_id"],
            "digest": descriptor["digest"],
            "size": descriptor["size"],
        }
        if (
            1 > self.limits["max_child_inputs"]
            or descriptor["size"] > self.limits["max_child_bytes"]
        ):
            raise RuntimeError("child input count or byte budget exceeded")
        previous = self._registered.get(descriptor["object_id"])
        if previous is not None:
            if previous != registration:
                raise ValueError("derived object has a conflicting producer descriptor")
            return
        if (
            len(self._registered) + 1 > self.limits["max_derived_objects"]
            or self.registered_bytes + descriptor["size"] > self.limits["max_derived_bytes"]
        ):
            raise RuntimeError("derived registration object or byte budget exceeded")
        self._registered[descriptor["object_id"]] = registration

    def register_local_file(
        self, path: Path, producer: Mapping[str, str], *, name: str
    ) -> dict[str, Any]:
        """Charge a confined parent output file before child admission."""
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > self.OBJECT_MAX_BYTES:
            raise ValueError("local child input must be a regular file within 64 MiB")
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                size += len(chunk)
                if size > self.OBJECT_MAX_BYTES:
                    raise ValueError("local child input exceeds 64 MiB")
                digest.update(chunk)
        digest_value = "sha256:" + digest.hexdigest()
        row = {
            "object_id": digest_value,
            "digest": digest_value,
            "size": size,
            "media_type": producer.get("media_type"),
            "output_port": producer.get("output_port"),
        }
        self.register(row, producer, name=name)
        return row


class BudgetTracker:
    """Track API/model calls across all API-backed dataset-build stages."""

    def __init__(
        self,
        *,
        max_api_calls: int | None = None,
        provider_limits: Mapping[str, int] | None = None,
        provider_rate_limits: Mapping[str, int] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.max_api_calls = max_api_calls
        self.provider_limits = dict(provider_limits or {})
        self.provider_rate_limits = dict(provider_rate_limits or {})
        self._clock = clock
        self._sleep = sleep
        self.total_api_calls = 0
        self.provider_calls: dict[str, int] = {}
        self.observed_calls_by_provider: dict[str, int] = {}
        self._last_call_at: dict[str, float] = {}

    @classmethod
    def from_config(
        cls,
        config: Mapping[str, Any],
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> "BudgetTracker":
        budgets = config.get("budgets") or {}
        if not isinstance(budgets, Mapping):
            return cls(clock=clock, sleep=sleep)
        providers = budgets.get("providers") or {}
        provider_limits: dict[str, int] = {}
        provider_rate_limits: dict[str, int] = {}
        if isinstance(providers, Mapping):
            for provider_id, provider_budget in providers.items():
                if not isinstance(provider_budget, Mapping):
                    continue
                if isinstance(provider_budget.get("max_calls"), int):
                    provider_limits[str(provider_id)] = int(provider_budget["max_calls"])
                if isinstance(provider_budget.get("rate_limit_per_minute"), int):
                    provider_rate_limits[str(provider_id)] = int(
                        provider_budget["rate_limit_per_minute"]
                    )
        max_api_calls = budgets.get("max_api_calls")
        return cls(
            max_api_calls=max_api_calls if isinstance(max_api_calls, int) else None,
            provider_limits=provider_limits,
            provider_rate_limits=provider_rate_limits,
            clock=clock,
            sleep=sleep,
        )

    def increment(self, provider_id: str, *, calls: int = 1) -> None:
        if calls < 0:
            raise ValueError("calls must be non-negative")
        next_total = self.total_api_calls + calls
        if self.max_api_calls is not None and next_total > self.max_api_calls:
            raise RuntimeError(
                f"API budget exceeded: total API calls {next_total} > {self.max_api_calls}"
            )
        next_provider = self.provider_calls.get(provider_id, 0) + calls
        provider_limit = self.provider_limits.get(provider_id)
        if provider_limit is not None and next_provider > provider_limit:
            raise RuntimeError(
                f"API budget exceeded for {provider_id}: API calls {next_provider} > {provider_limit}"
            )

        for _ in range(calls):
            self._enforce_rate_limit(provider_id)
            self.total_api_calls += 1
            self.provider_calls[provider_id] = self.provider_calls.get(provider_id, 0) + 1
            self.observed_calls_by_provider[provider_id] = (
                self.observed_calls_by_provider.get(provider_id, 0) + 1
            )

    def as_dict(self) -> dict[str, Any]:
        return {
            "total_api_calls": self.total_api_calls,
            "provider_calls": dict(self.provider_calls),
            "observed_calls_by_provider": dict(self.observed_calls_by_provider),
            "max_api_calls": self.max_api_calls,
            "provider_limits": dict(self.provider_limits),
            "provider_rate_limits": dict(self.provider_rate_limits),
        }

    def _enforce_rate_limit(self, provider_id: str) -> None:
        rate_limit = self.provider_rate_limits.get(provider_id)
        if rate_limit is None:
            return
        if rate_limit <= 0:
            raise RuntimeError(f"API rate limit for {provider_id} must be positive")
        min_interval = 60.0 / float(rate_limit)
        now = float(self._clock())
        last_call_at = self._last_call_at.get(provider_id)
        if last_call_at is not None:
            wait_s = (last_call_at + min_interval) - now
            if wait_s > 0:
                self._sleep(wait_s)
                now = float(self._clock())
        self._last_call_at[provider_id] = now
