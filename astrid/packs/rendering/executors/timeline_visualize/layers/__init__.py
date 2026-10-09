"""Visualize layers: small registered views of one timeline window.

A layer turns the timeline document (and, when it asks for them, a bounded
set of captured frames) into **one PNG panel plus a few lines of text
findings** terse enough to act on without opening the image. ``timelines
visualize --view motion --cut N --layer sync,onion,…`` stacks the chosen
layers into a motion sheet; ``--list-layers`` prints the registry.

Adding a layer or a check (the seam, on purpose the same shape as a pack element;
the full guide is astrid/packs/rendering/skill/references/visualize-extend.md):

    # astrid/packs/<your_pack>/visualize_layers/<name>.py
    from astrid.packs.rendering.executors.timeline_visualize.layers import Layer, LayerResult

    def render(ctx):                       # ctx: LayerContext (cut, elements, words, beats, frames …)
        image = ctx.panel(120)             # a panel on the shared time axis (ctx.x_of(t))
        ...
        return LayerResult(image, ["RHYTHM cut 17 is 6.1 s (target 1.5–4)"])

    LAYER = Layer("rhythm", "cut length vs neighbours", render, needs=("doc",))

Discovery imports every ``astrid/packs/*/visualize_layers/*.py`` next to the
built-ins. That folder convention (not a Python entry point) is the seam
because packs are directories, not installed distributions: a layer ships,
is pinned and is promoted with its pack exactly like an element.
"""
from __future__ import annotations

from .base import (
    Check,
    CheckContext,
    Finding,
    Layer,
    LayerContext,
    LayerResult,
    check_help,
    checks,
    discover,
    layer_help,
    register,
    register_check,
    resolve_layers,
)

__all__ = [
    "Check",
    "CheckContext",
    "Finding",
    "Layer",
    "LayerContext",
    "LayerResult",
    "check_help",
    "checks",
    "discover",
    "layer_help",
    "register",
    "register_check",
    "resolve_layers",
]
