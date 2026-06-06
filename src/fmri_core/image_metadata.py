"""Read lightweight metadata used for storage and runtime estimates."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import nibabel as nib
from bids import BIDSLayout

from .disk import GB


def load_image_metadata(path: str | Path) -> dict[str, Any]:
    """Load lightweight metadata for one image file.

    Inputs:
        path (str | Path): Filesystem path being inspected or normalized.

    Returns:
        dict[str, Any]: Lightweight metadata extracted from the image file.
    """
    image_path = Path(path)
    image = nib.load(str(image_path))
    shape = [int(value) for value in image.shape]
    zooms = [float(value) for value in image.header.get_zooms()[: len(shape)]]
    data_dtype = image.get_data_dtype()
    bitpix = int(getattr(data_dtype, "itemsize", 0) * 8)
    timepoints = int(shape[3]) if len(shape) >= 4 else 1
    assumptions: list[str] = []

    repetition_time = _read_repetition_time(image_path)
    if repetition_time is None:
        assumptions.append("assumption_missing_sidecar_or_tr")

    return {
        "path": str(image_path),
        "compressed_size_gb": image_path.stat().st_size / GB,
        "shape": shape,
        "zooms": [round(value, 6) for value in zooms],
        "timepoints": timepoints,
        "bitpix": bitpix,
        "dtype": str(data_dtype),
        "repetition_time": repetition_time,
        "assumptions": assumptions,
    }


def _read_repetition_time(image_path: Path) -> float | None:
    """Read the repetition time when it is available.

    Inputs:
        image_path (Path): Image file whose sidecar metadata should be inspected.

    Returns:
        float | None: Resolved floating-point value, or ``None`` when unavailable.
    """
    bids_root = _find_bids_root(image_path)
    if bids_root is None:
        return None
    try:
        payload = _layout_for_root(bids_root).get_metadata(str(image_path))
    except Exception:
        return None
    value = _find_repetition_time(payload)
    if value is None:
        return None
    return _normalize_repetition_time(value)


def _find_bids_root(image_path: Path) -> Path | None:
    """Find the nearest BIDS root for an image path."""
    for candidate in (image_path.parent, *image_path.parents):
        if (candidate / "dataset_description.json").exists():
            return candidate
    return None


@lru_cache(maxsize=16)
def _layout_for_root(bids_root: Path) -> BIDSLayout:
    """Create and cache a PyBIDS layout for one dataset root."""
    return BIDSLayout(str(bids_root), validate=False)


def _find_repetition_time(payload: Any) -> float | int | None:
    """Search nested sidecar payloads for a repetition time value."""
    if isinstance(payload, dict):
        if "RepetitionTime" in payload:
            return payload["RepetitionTime"]
        for value in payload.values():
            found = _find_repetition_time(value)
            if found is not None:
                return found
        return None
    if isinstance(payload, list):
        for value in payload:
            found = _find_repetition_time(value)
            if found is not None:
                return found
    return None


def _normalize_repetition_time(value: float | int) -> float:
    """Normalize repetition time values to seconds."""
    repetition_time = float(value)
    if repetition_time > 100:
        return repetition_time / 1000.0
    return repetition_time
