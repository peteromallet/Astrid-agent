from __future__ import annotations

import io
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from astrid.core.contracts.errors import AstridError
from astrid.core.pack.validate import validate_pack
from astrid.packs.pixel.executors import _common as px
from astrid.packs.pixel.executors.cutout import run as cutout_run
from astrid.packs.pixel.executors.snap import run as snap_run

PACK_ROOT = Path(__file__).resolve().parents[3] / "astrid" / "packs" / "pixel"
MAGENTA = (255, 0, 255)
OUTLINE = (0x25, 0x24, 0x1F)
BODY = (0xED, 0x6B, 0x23)
BELLY = (0xF5, 0xDC, 0xC7)
EYE = (0x15, 0x13, 0x11)


def _sprite() -> np.ndarray:
    """40x30 RGBA sprite: magenta background, 1 px dark outline, body, belly, eye."""
    arr = np.zeros((30, 40, 4), dtype=np.uint8)
    arr[..., :3] = MAGENTA
    arr[..., 3] = 255
    arr[5:25, 8:32, :3] = OUTLINE
    arr[6:24, 9:31, :3] = BODY
    arr[14:23, 12:28, :3] = BELLY
    arr[10:12, 14:17, :3] = EYE
    return arr


def _upscale(arr: np.ndarray, factor: int) -> np.ndarray:
    return np.repeat(np.repeat(arr, factor, axis=0), factor, axis=1)


def _jpeg_noisy(arr: np.ndarray) -> np.ndarray:
    """Add JPEG-style artefacts: a small RGB perturbation then a real JPEG round-trip."""
    rng = np.random.default_rng(7)
    rgb = np.clip(arr[..., :3].astype(np.int16) + rng.integers(-8, 9, arr.shape[:2] + (3,)), 0, 255)
    buffer = io.BytesIO()
    Image.fromarray(rgb.astype(np.uint8), mode="RGB").save(buffer, format="JPEG", quality=70)
    buffer.seek(0)
    decoded = np.asarray(Image.open(buffer).convert("RGB"), dtype=np.uint8)
    return np.concatenate([decoded, arr[..., 3:]], axis=2)


def _save(path: Path, arr: np.ndarray) -> Path:
    Image.fromarray(arr, mode="RGBA").save(path, format="PNG")
    return path


def _outline_mask() -> np.ndarray:
    return np.all(_sprite()[..., :3] == OUTLINE, axis=2)


def _nearest_class(rgb: np.ndarray) -> np.ndarray:
    """Index of the sprite colour class nearest to each pixel: 0 magenta, 1 outline, 2 body, 3 belly, 4 eye."""
    classes = np.array([MAGENTA, OUTLINE, BODY, BELLY, EYE], dtype=float)
    return np.sqrt(((rgb[..., None, :3].astype(float) - classes) ** 2).sum(axis=-1)).argmin(axis=-1)


def _truth_classes() -> np.ndarray:
    return _nearest_class(_sprite())


# ---------------------------------------------------------------------------
# pack manifest and discovery
# ---------------------------------------------------------------------------


def test_pack_manifest_validates_statically() -> None:
    errors, _warnings = validate_pack(PACK_ROOT)
    assert errors == []


def test_executors_declare_managed_image_and_artifact_outputs() -> None:
    import yaml

    for name, outputs in (
        ("snap", {"native", "preview", "report"}),
        ("cutout", {"cutout", "report"}),
    ):
        manifest = yaml.safe_load((PACK_ROOT / "executors" / name / "executor.yaml").read_text(encoding="utf-8"))
        assert manifest["kind"] == "built_in"
        image_input = next(port for port in manifest["inputs"] if port["name"] == "image")
        assert image_input["type"] == "file" and image_input["required"] is True
        assert image_input["artifact_type"] == "image"
        assert {port["name"] for port in manifest["outputs"]} == outputs
        assert manifest["metadata"]["output_result_manifest"] is True


def test_discovery_lists_both_capabilities_cold() -> None:
    import astrid.sdk as sdk

    discovered = {capability.id for capability in sdk.discover().executors}
    assert {"pixel.snap", "pixel.cutout"} <= discovered


# ---------------------------------------------------------------------------
# parsing helpers
# ---------------------------------------------------------------------------


def test_parse_crop_accepts_mapping_json_and_xywh_text() -> None:
    expected = {"x": 1, "y": 2, "width": 3, "height": 4}
    assert px.parse_crop({"x": 1, "y": 2, "width": 3, "height": 4}) == expected
    assert px.parse_crop('{"x": 1, "y": 2, "width": 3, "height": 4}') == expected
    assert px.parse_crop("1,2,3,4") == expected
    assert px.parse_crop(None) is None


def test_parse_crop_rejects_non_positive_size() -> None:
    with pytest.raises(AstridError):
        px.parse_crop({"x": 0, "y": 0, "width": 0, "height": 4})


def test_parse_palette_preset_and_custom_and_errors() -> None:
    source, colours, _ = px.parse_palette("astrid")
    assert source == "preset:astrid"
    assert colours.shape[1] == 3 and len(colours) >= 16
    source, colours, _ = px.parse_palette("#000000, #FFFFFF")
    assert source == "custom" and colours.tolist() == [[0, 0, 0], [255, 255, 255]]
    assert px.parse_palette(None) is None
    with pytest.raises(AstridError):
        px.parse_palette("#FFFFFF")


def test_parse_hex_rejects_bad_colour() -> None:
    with pytest.raises(AstridError):
        px.parse_hex("magenta")


# ---------------------------------------------------------------------------
# pixel.snap
# ---------------------------------------------------------------------------


def test_detect_lattice_recovers_four_pixel_blocks_from_noisy_upscale() -> None:
    big = _jpeg_noisy(_upscale(_sprite(), 4))
    horizontal = px.detect_lattice(big, axis=1)
    vertical = px.detect_lattice(big, axis=0)
    assert horizontal["detected"] and vertical["detected"]
    assert horizontal["block"] == 4.0 and horizontal["origin"] == 0.0
    assert vertical["block"] == 4.0 and vertical["origin"] == 0.0


def test_detect_lattice_reports_no_lattice_for_native_pixels() -> None:
    rng = np.random.default_rng(3)
    native = rng.integers(0, 255, (30, 40, 4), dtype=np.uint8)
    native[..., 3] = 255
    assert px.detect_lattice(native, axis=1)["detected"] is False


def test_snap_auto_grid_returns_native_sprite_at_grid_size() -> None:
    big = _jpeg_noisy(_upscale(_sprite(), 4))
    native, preview, report = px.snap_image(big, auto_grid=True, scale=6)
    assert native.shape == (30, 40, 4)
    assert preview.shape == (30 * 6, 40 * 6, 4)
    assert report["grid"]["detected"] is True and report["grid"]["width"] == 40
    # Every logical cell lands in its true colour class despite JPEG-style artefacts.
    assert np.array_equal(_nearest_class(native), _truth_classes())


def test_snap_keeps_one_pixel_outline_as_single_cells() -> None:
    big = _jpeg_noisy(_upscale(_sprite(), 4))
    native, _preview, _report = px.snap_image(big, auto_grid=True, scale=1)
    outline = _outline_mask()
    assert outline.sum() == 84
    # Each outline pixel is one opaque cell of the outline colour class.
    classes = _nearest_class(native)
    assert int(((classes == 1) & (native[..., 3] == 255) & outline).sum()) == 84


def test_snap_alpha_is_hard_and_preview_is_nearest_neighbour() -> None:
    arr = _sprite()
    arr[0:3, 0:3, 3] = 80  # semi-transparent corner must snap to hard alpha
    native, preview, report = px.snap_image(arr, grid_width=40, grid_height=30, fit="none", scale=3)
    assert set(np.unique(native[..., 3]).tolist()) <= {0, 255}
    assert report["alpha_values"] in ([0, 255], [255], [0])
    assert np.array_equal(preview[::3, ::3], native)
    assert np.array_equal(preview[1::3, 2::3], native)


def test_snap_explicit_grid_with_cover_crops_to_aspect() -> None:
    arr = np.zeros((30, 60, 4), dtype=np.uint8)
    arr[..., 3] = 255
    arr[:, 30:] = (255, 255, 255, 255)
    native, _preview, report = px.snap_image(arr, grid_width=8, grid_height=8, fit="cover", scale=1)
    assert native.shape == (8, 8, 4)
    assert report["grid"]["fit_info"]["cropped_x"] == [15, 45]
    # The 15-column crop straddles the black/white boundary, so both colours survive.
    assert native[..., :3].max() == 255 and native[..., :3].min() == 0


def test_snap_palette_maps_to_preset_and_reports_distance() -> None:
    arr = _sprite()
    native, _preview, report = px.snap_image(arr, grid_width=40, grid_height=30, fit="none", palette="astrid", scale=1)
    allowed = {tuple(int(c) for c in px.parse_hex(h)) for h in px.ASTRID_PALETTE_HEX}
    colours = {tuple(int(c) for c in pixel) for pixel in native[native[..., 3] == 255][:, :3]}
    assert colours <= allowed
    assert report["palette"]["source"] == "preset:astrid"
    assert report["palette_distance"]["count"] > 0
    assert report["palette_distance"]["max"] >= report["palette_distance"]["mean"] >= 0


def test_snap_max_colors_quantises_to_at_most_k_colours() -> None:
    rng = np.random.default_rng(11)
    arr = np.zeros((30, 40, 4), dtype=np.uint8)
    arr[..., :3] = rng.integers(0, 256, (30, 40, 3))
    arr[..., 3] = 255
    native, _preview, report = px.snap_image(arr, grid_width=40, grid_height=30, fit="none", max_colors=4, scale=1)
    assert report["palette"]["source"] == "kmeans:4"
    assert len({tuple(pixel) for pixel in native[..., :3].reshape(-1, 3)}) <= 4


def test_snap_rejects_grid_larger_than_source() -> None:
    with pytest.raises(AstridError):
        px.snap_image(_sprite(), grid_width=400, grid_height=300, fit="none")


def test_snap_cli_writes_native_preview_report_and_manifest(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ASTRID_INTERNAL_INVOCATION", "1")
    source = _save(tmp_path / "source.png", _jpeg_noisy(_upscale(_sprite(), 4)))
    out = tmp_path / "out"
    assert snap_run.main(["--image", str(source), "--out", str(out), "--auto-grid", "True", "--scale", "2"]) == 0
    native = np.asarray(Image.open(out / "native.png"))
    preview = np.asarray(Image.open(out / "preview.png"))
    assert native.shape == (30, 40, 4)
    assert preview.shape == (60, 80, 4)
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["grid"]["mode"] == "auto"
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["kind"] == "pixel_snap"
    assert [entry["path"] for entry in manifest["outputs"]] == ["native.png", "preview.png", "report.json"]


def test_snap_cli_crop_is_applied_before_grid(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ASTRID_INTERNAL_INVOCATION", "1")
    source = _save(tmp_path / "source.png", _upscale(_sprite(), 4))
    out = tmp_path / "out"
    crop = json.dumps({"x": 16, "y": 12, "width": 128, "height": 104})
    assert snap_run.main(["--image", str(source), "--out", str(out), "--crop", crop, "--auto-grid", "True"]) == 0
    native = np.asarray(Image.open(out / "native.png"))
    assert native.shape == (26, 32, 4)
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["crop"] == {"x": 16, "y": 12, "width": 128, "height": 104}
    assert report["source_size"] == {"width": 160, "height": 120}


# ---------------------------------------------------------------------------
# pixel.cutout
# ---------------------------------------------------------------------------


def test_cutout_chroma_removes_background_and_keeps_outline() -> None:
    native = _sprite()
    rgba, report = px.cutout_image(native, mode="chroma", key="#FF00FF", tolerance=48, trim=False)
    assert report["alpha_values"] == [0, 255]
    assert report["hard_alpha"] is True
    assert rgba[0, 0, 3] == 0
    outline = _outline_mask()
    assert int(((rgba[..., 3] == 255) & outline).sum()) == int(outline.sum())


def test_cutout_keeps_enclosed_key_coloured_pixels() -> None:
    native = _sprite()
    native[12, 20, :3] = MAGENTA  # magenta enclosed by the body, not connected to the border
    rgba, _report = px.cutout_image(native, mode="chroma", key="#FF00FF", tolerance=48, trim=False)
    assert rgba[12, 20, 3] == 255


def test_cutout_flat_detects_border_colour_and_removes_it() -> None:
    native = _sprite()
    native[..., :3] = np.where(np.all(native[..., :3] == MAGENTA, axis=2, keepdims=True), (0x12, 0xAB, 0x34), native[..., :3])
    rgba, report = px.cutout_image(native, mode="flat", tolerance=40, trim=False)
    assert report["key"] == "#12AB34"
    assert rgba[0, 0, 3] == 0
    assert rgba[15, 20, 3] == 255


def test_cutout_grid_auto_gives_per_logical_pixel_alpha() -> None:
    big = _jpeg_noisy(_upscale(_sprite(), 4))
    rgba, report = px.cutout_image(big, mode="chroma", grid="auto", trim=False)
    assert report["grid"]["width"] == 40 and report["grid"]["height"] == 30
    assert rgba.shape == (30, 40, 4)
    assert report["hard_alpha"] is True


def test_cutout_trim_crops_to_alpha_bbox_with_padding() -> None:
    native = _sprite()
    rgba, report = px.cutout_image(native, mode="chroma", tolerance=48, trim=True, trim_padding=2)
    assert report["bbox"] == {"x": 8, "y": 5, "width": 24, "height": 20}
    assert rgba.shape == (24, 28, 4)
    assert report["trim"]["padding"] == 2


def test_cutout_keep_largest_drops_detached_specks() -> None:
    native = _sprite()
    native[1, 1, :3] = (0, 0, 0)
    rgba, report = px.cutout_image(native, mode="chroma", keep_largest=True, trim=False)
    assert rgba[1, 1, 3] == 0
    assert report["specks_dropped"]["components"] == 1
    rgba_all, _ = px.cutout_image(native, mode="chroma", keep_largest=False, trim=False)
    assert rgba_all[1, 1, 3] == 255


def test_cutout_with_no_foreground_raises() -> None:
    native = np.zeros((4, 4, 4), dtype=np.uint8)
    native[..., :3] = MAGENTA
    native[..., 3] = 255
    with pytest.raises(AstridError):
        px.cutout_image(native, mode="chroma", tolerance=48)


def test_cutout_luma_removes_light_backdrop_and_keeps_dark_outline() -> None:
    native = _sprite()
    native[..., :3] = np.where(np.all(native[..., :3] == MAGENTA, axis=2, keepdims=True), (250, 250, 250), native[..., :3])
    rgba, _report = px.cutout_image(native, mode="luma", tolerance=40, trim=False)
    assert rgba[0, 0, 3] == 0
    outline = _outline_mask()
    assert int(((rgba[..., 3] == 255) & outline).sum()) == int(outline.sum())


def test_cutout_cli_writes_rgba_and_report(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ASTRID_INTERNAL_INVOCATION", "1")
    source = _save(tmp_path / "source.png", _jpeg_noisy(_upscale(_sprite(), 4)))
    out = tmp_path / "out"
    assert cutout_run.main(["--image", str(source), "--out", str(out), "--grid", "auto", "--trim", "True"]) == 0
    rgba = Image.open(out / "cutout.png")
    assert rgba.mode == "RGBA"
    arr = np.asarray(rgba)
    assert set(np.unique(arr[..., 3]).tolist()) <= {0, 255}
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["key"] == "#FF00FF" and report["mode"] == "chroma"
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["kind"] == "pixel_cutout"
