"""Utilities for persisting and applying per-boiler calibration data."""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import CALIBRATION_STORAGE_VERSION, DOMAIN

CalibrationProfile = Dict[str, Any]
CalibrationPoints = List[Dict[str, float | int]]


def _storage_key(entry_id: str) -> str:
    return f"{DOMAIN}_calibration_{entry_id}"


def sanitize_points(points: List[Dict[str, Any]]) -> CalibrationPoints:
    """Normalize raw calibration point input before storage or use."""
    sanitized: CalibrationPoints = []
    for entry in points or []:
        try:
            watts = float(entry.get("watts"))
            percentage = int(entry.get("percentage"))
        except (TypeError, ValueError):
            continue

        watts = max(0.0, round(watts, 3))
        percentage = max(0, min(100, percentage))
        sanitized.append({"watts": watts, "percentage": percentage})

    sanitized.sort(key=lambda item: (item["watts"], item["percentage"]))

    deduped: CalibrationPoints = []
    seen_watts: set[float] = set()
    for item in sanitized:
        watts = item["watts"]
        if watts in seen_watts:
            continue
        seen_watts.add(watts)
        deduped.append(item)

    return deduped


def points_to_thresholds(points: CalibrationPoints) -> List[Tuple[float, int]]:
    """Convert sanitized points into calculator thresholds."""
    sanitized = sanitize_points(points)
    thresholds: List[Tuple[float, int]] = []
    for item in sanitized:
        thresholds.append((item["watts"], item["percentage"]))
    return thresholds


class CalibrationStore:
    """Wrapper around Home Assistant storage for calibration profiles."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store = Store(hass, CALIBRATION_STORAGE_VERSION, _storage_key(entry_id))

    async def async_load_profile(self) -> CalibrationProfile | None:
        data = await self._store.async_load()
        if not data:
            return None

        points = sanitize_points(data.get("points", []))
        return {
            "created": data.get("created"),
            "points": points,
        }

    async def async_save_points(self, points: CalibrationPoints) -> CalibrationProfile:
        sanitized = sanitize_points(points)
        payload: CalibrationProfile = {
            "created": dt_util.utcnow().isoformat(),
            "points": sanitized,
        }
        await self._store.async_save(payload)
        return payload
