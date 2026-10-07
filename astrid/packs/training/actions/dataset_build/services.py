"""Run-scoped service dataclasses for ``training.dataset_build``."""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .budget import BudgetTracker, ChildWorkMeter, delegation_preflight
from .filter_stages import get_filter_stage


@dataclass
class DatasetRunServices:
    """Run-scoped services shared across dataset-build phases."""

    budget_tracker: BudgetTracker
    child_work_meter: ChildWorkMeter | None = None
    attempt_output_root: Path | None = None
    filter_stage_factory: Callable[..., Any] = get_filter_stage
    filter_stage_runners: Mapping[str, Any] = field(default_factory=dict)
    caption_runner: Any | None = None
    human_review_runner: Any = subprocess.run
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    artifact_helpers: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_config(
        cls,
        config: Mapping[str, Any],
        *,
        caption_runner: Any | None = None,
        human_review_runner: Any = subprocess.run,
        filter_stage_factory: Callable[..., Any] = get_filter_stage,
        filter_stage_runners: Mapping[str, Any] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        artifact_helpers: Mapping[str, Any] | None = None,
        attempt_output_root: Path | None = None,
    ) -> "DatasetRunServices":
        preflight = delegation_preflight(config)
        return cls(
            budget_tracker=BudgetTracker.from_config(config, clock=clock, sleep=sleep),
            child_work_meter=ChildWorkMeter(preflight["limits"]) if preflight["required"] else None,
            attempt_output_root=attempt_output_root.resolve() if attempt_output_root is not None else None,
            filter_stage_factory=filter_stage_factory,
            filter_stage_runners=dict(filter_stage_runners or {}),
            caption_runner=caption_runner,
            human_review_runner=human_review_runner,
            clock=clock,
            sleep=sleep,
            artifact_helpers=dict(artifact_helpers or {}),
        )


@dataclass
class RoundExecutionResult:
    """Artifacts and item state produced by one dataset-build round."""

    round_index: int
    all_items: list[dict[str, Any]]
    captioned: list[dict[str, Any]]
    review_data_path: Path
    filter_stats: dict[str, Any]
