"""The layer interface, the registry and pack discovery."""
from __future__ import annotations

import importlib.util
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

# What a layer may ask for. "frames" makes the planner capture its frames;
# everything else is read from the timeline document.
NEEDS = ("doc", "frames", "words", "beats", "audio")
VIEWS = ("motion", "contact")


@dataclass
class LayerResult:
    """One panel (``None`` for a findings-only layer) and its text findings."""

    image: Any | None
    findings: list[str] = field(default_factory=list)
    title: str = ""
    page: str = "data"  # "data" (time-aligned page) or "frames" (pixel page)


@dataclass(frozen=True)
class Layer:
    """A registered view.

    ``render(ctx) -> LayerResult``. ``frame_budget`` is the number of extra
    frames the layer asks the planner for (the whole sheet is capped).
    ``experimental`` layers run only when named explicitly.
    """

    name: str
    help: str
    render: Callable[["LayerContext"], LayerResult]
    needs: tuple[str, ...] = ("doc",)
    views: tuple[str, ...] = ("motion",)
    frame_budget: int = 0
    experimental: bool = False
    order: int = 100
    source: str = "built-in"
    default: bool = True  # runs when no --layer is given (experimental layers never do)


_REGISTRY: dict[str, Layer] = {}
_BROKEN: dict[str, str] = {}
_DISCOVERED = False


def register(layer: Layer) -> Layer:
    if not layer.name or not layer.name.replace("-", "").replace("_", "").isalnum():
        raise ValueError(f"layer name {layer.name!r} must be a short word")
    unknown = set(layer.needs) - set(NEEDS)
    if unknown:
        raise ValueError(f"layer {layer.name!r} needs unknown inputs: {sorted(unknown)}")
    _REGISTRY[layer.name] = layer
    return layer


def _packs_root() -> Path:
    return Path(__file__).resolve().parents[4]


def discover(*, packs_root: Path | None = None, refresh: bool = False) -> dict[str, Layer]:
    """Built-in layers plus every ``<pack>/visualize_layers/*.py`` module's ``LAYER``/``LAYERS``."""
    global _DISCOVERED
    if _DISCOVERED and not refresh and packs_root is None:
        return dict(_REGISTRY)
    from . import builtin  # noqa: F401  (registers the built-ins)

    root = packs_root or _packs_root()
    for path in sorted(root.glob("*/visualize_layers/*.py")):
        if path.name.startswith("_"):
            continue
        module_name = f"astrid_visualize_layer_{path.parent.parent.name}_{path.stem}"
        try:
            spec = importlib.util.spec_from_file_location(module_name, path)
            if spec is None or spec.loader is None:
                raise ImportError("not importable")
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
            layers = list(getattr(module, "LAYERS", None) or [])
            if getattr(module, "LAYER", None) is not None:
                layers.append(module.LAYER)
            if not layers:
                raise ValueError("defines no LAYER")
            for layer in layers:
                source = f"{path.parent.parent.name} pack ({path.relative_to(root.parent.parent).as_posix()})"
                register(Layer(**{**layer.__dict__, "source": source}))
        except Exception as exc:  # noqa: BLE001 - a broken pack layer must not break visualize
            _BROKEN[f"{path.parent.parent.name}/{path.stem}"] = f"{type(exc).__name__}: {exc}"
    _DISCOVERED = True
    return dict(_REGISTRY)


def resolve_layers(names: Sequence[str] | None, *, view: str) -> list[Layer]:
    """The layers to run: the named ones (in order), else every non-experimental layer of ``view``."""
    registry = discover()
    if not names:
        chosen = [layer for layer in registry.values()
                  if view in layer.views and layer.default and not layer.experimental]
        return sorted(chosen, key=lambda layer: (layer.order, layer.name))
    chosen = []
    for raw in names:
        name = str(raw).strip()
        if not name:
            continue
        if name == "all":
            chosen.extend(layer for layer in registry.values() if view in layer.views)
            continue
        layer = registry.get(name)
        if layer is None:
            known = ", ".join(sorted(registry))
            raise ValueError(f"unknown layer {name!r}; known layers: {known} (timelines visualize --list-layers)")
        if view not in layer.views:
            raise ValueError(f"layer {name!r} applies to --view {'/'.join(layer.views)}, not {view}")
        chosen.append(layer)
    unique = list({layer.name: layer for layer in chosen}.values())
    return sorted(unique, key=lambda layer: (layer.order, layer.name))


def layer_help() -> str:
    """The ``--list-layers`` text."""
    registry = discover()
    lines = ["Layers (--layer a,b,…; with no --layer every default layer of the view runs):"]
    for layer in sorted(registry.values(), key=lambda layer: (layer.order, layer.name)):
        tags = [f"views {'/'.join(layer.views)}", f"needs {'+'.join(layer.needs)}"]
        if layer.frame_budget:
            tags.append(f"+{layer.frame_budget} frames")
        if layer.experimental:
            tags.append("experimental")
        elif not layer.default:
            tags.append("opt-in")
        if layer.source != "built-in":
            tags.append(f"from {layer.source}")
        lines.append(f"  {layer.name:<9} {layer.help}  [{'; '.join(tags)}]")
    for name, error in sorted(_BROKEN.items()):
        lines.append(f"  (broken) {name}: {error}")
    lines.append("Add one: a module astrid/packs/<pack>/visualize_layers/<name>.py defining LAYER = Layer(...).")
    return "\n".join(lines)


# ---------------------------------------------------------------- context

@dataclass
class LayerContext:
    """Everything a layer may read for one cut window (all times in timeline seconds)."""

    cut: Mapping[str, Any]
    cuts: Sequence[Mapping[str, Any]]
    window: tuple[float, float]
    fps: float
    elements: list[Any]  # motion.model.Element in the window
    all_elements: list[Any]
    words: list[Any]  # motion.model.Word in the window
    beats: Mapping[str, list] = field(default_factory=dict)
    sfx: list[tuple[float, float, str]] = field(default_factory=list)
    events: list[Any] = field(default_factory=list)  # motion.model.Event in the window
    frames: dict[int, Path] = field(default_factory=dict)  # timeline frame -> captured PNG
    frame_size: tuple[int, int] = (480, 270)
    width: int = 1600
    gutter: int = 150
    shared: dict[str, Any] = field(default_factory=dict)  # results layers pass to later layers

    # -- time axis shared by every time-aligned panel
    @property
    def plot_left(self) -> int:
        return self.gutter

    @property
    def plot_right(self) -> int:
        return self.width - 24

    def x_of(self, t: float) -> float:
        """Panel x of timeline second ``t``, clamped to the plot area."""
        start, end = self.window
        span = max(1e-6, end - start)
        x = self.plot_left + (t - start) / span * (self.plot_right - self.plot_left)
        return min(self.plot_right, max(self.plot_left, x))

    def visible(self, start: float, end: float | None = None) -> bool:
        """True when ``[start, end]`` (or the instant ``start``) falls inside the panel window."""
        end = start if end is None else end
        return end >= self.window[0] and start <= self.window[1]

    def frame_time(self, frame: int) -> float:
        return frame / self.fps

    def rel(self, t: float) -> str:
        """``+1.23s`` relative to the cut start."""
        return f"{t - float(self.cut['start']):+.2f}s"

    def panel(self, height: int, *, title: str = "", axis: bool = True):
        """A blank panel with its title in the gutter and the shared time grid."""
        from PIL import Image, ImageDraw

        image = Image.new("RGB", (self.width, height), PALETTE["bg"])
        draw = ImageDraw.Draw(image)
        if title:
            draw_text(draw, (10, 8), title, 15, PALETTE["ink"])
        if axis:
            start, end = self.window
            step = 0.5 if end - start <= 8 else 1.0
            t = math.ceil(start / step) * step
            while t <= end + 1e-6:
                x = self.x_of(t)
                strong = abs(t - round(t)) < 1e-6
                draw.line((x, 0, x, height), fill=PALETTE["grid_strong" if strong else "grid"])
                t += step
            for edge in (float(self.cut["start"]), float(self.cut["end"])):
                x = self.x_of(edge)
                draw.line((x, 0, x, height), fill=PALETTE["cut"], width=2)
        return image

    def frame_image(self, frame: int):
        from PIL import Image

        path = self.frames.get(frame)
        if path is None:
            return None
        with Image.open(path) as source:
            return source.convert("RGB")


PALETTE = {
    "bg": "#111827",
    "panel": "#162130",
    "ink": "#e5e7eb",
    "muted": "#9fb0bf",
    "grid": "#1f2b3a",
    "grid_strong": "#2c3b4f",
    "cut": "#f97316",
    "word": "#2f6f73",
    "word_ink": "#e6fffb",
    "beat": "#6b7a90",
    "downbeat": "#cbd5e1",
    "hit": "#facc15",
    "sfx": "#c084fc",
    "enter": "#4ade80",
    "exit": "#f87171",
    "key": "#60a5fa",
    "warn": "#fbbf24",
    "bad": "#ef4444",
    "good": "#34d399",
}
SERIES = ("#60a5fa", "#f472b6", "#facc15", "#34d399", "#c084fc", "#fb923c", "#22d3ee", "#a3e635")


def font(size: int):
    from ..filmstrip_cards import _png_font

    return _png_font(size)


def draw_text(draw, xy, text: str, size: int, fill) -> None:
    from ..filmstrip_cards import _png_draw_text

    _png_draw_text(draw, xy, text, font(size), fill=fill)


def text_width(draw, text: str, size: int) -> float:
    from ..filmstrip_cards import _png_text_width

    return _png_text_width(draw, text, font(size))
