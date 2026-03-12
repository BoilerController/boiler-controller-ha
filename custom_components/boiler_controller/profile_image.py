"""Utilities for rendering calibration profile curves as SVG images."""
from __future__ import annotations

import asyncio
import os
from typing import Iterable

from homeassistant.core import HomeAssistant

SVG_WIDTH = 1200
SVG_HEIGHT = 500
SVG_PADDING = 60


class ProfileImageManager:
    """Generate and cache SVG curves for the calibration profile."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._hass = hass
        self._entry_id = entry_id
        self._lock = asyncio.Lock()
        self._latest_svg: bytes | None = None
        self._rel_path = os.path.join("boiler_controller", f"profile_{entry_id}.svg")

    @property
    def local_url(self) -> str:
        """Return the /local/... path that hosts the cached image."""

        return f"/local/{self._rel_path.replace(os.path.sep, '/')}"

    async def async_get_bytes(self) -> bytes | None:
        """Return the most recently rendered SVG bytes."""

        async with self._lock:
            if self._latest_svg is not None:
                return self._latest_svg

        path = self._absolute_path
        if os.path.exists(path):
            data = await self._hass.async_add_executor_job(self._read_file, path)
            async with self._lock:
                self._latest_svg = data
            return data
        return None

    async def async_update(self, profile_points: Iterable[tuple[int, float]]) -> None:
        """Render the provided calibration profile into an SVG image."""

        svg_bytes = await self._hass.async_add_executor_job(
            self._render_svg, list(profile_points)
        )
        async with self._lock:
            self._latest_svg = svg_bytes
        path = self._absolute_path
        await self._hass.async_add_executor_job(self._write_file, path, svg_bytes)

    @property
    def _absolute_path(self) -> str:
        return self._hass.config.path("www", self._rel_path)

    @staticmethod
    def _read_file(path: str) -> bytes:
        with open(path, "rb") as handle:
            return handle.read()

    @staticmethod
    def _write_file(path: str, data: bytes) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(data)

    @staticmethod
    def _render_svg(profile_points: list[tuple[int, float]]) -> bytes:
        if not profile_points:
            profile_points = [(0, 0.0)]

        percentages = [float(point[0]) for point in profile_points]
        watts_values = [float(point[1]) for point in profile_points]

        min_pct = min(percentages)
        max_pct = max(percentages)
        pct_span = max(1.0, max_pct - min_pct)

        max_watts = max(1.0, max(watts_values))
        plot_width = SVG_WIDTH - 2 * SVG_PADDING
        plot_height = SVG_HEIGHT - 2 * SVG_PADDING

        def scale_x(value: float) -> float:
            return SVG_PADDING + ((value - min_pct) / pct_span) * plot_width

        def scale_y(value: float) -> float:
            return SVG_HEIGHT - SVG_PADDING - (value / max_watts) * plot_height

        polyline = " ".join(
            f"{scale_x(pct):.2f},{scale_y(watts):.2f}"
            for pct, watts in zip(percentages, watts_values)
        )

        y_axis = f"{SVG_PADDING},{SVG_PADDING} {SVG_PADDING},{SVG_HEIGHT - SVG_PADDING}"
        x_axis = f"{SVG_PADDING},{SVG_HEIGHT - SVG_PADDING} {SVG_WIDTH - SVG_PADDING},{SVG_HEIGHT - SVG_PADDING}"

        svg = f"""
<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"{SVG_WIDTH}\" height=\"{SVG_HEIGHT}\" viewBox=\"0 0 {SVG_WIDTH} {SVG_HEIGHT}\" role=\"img\">
    <title>Boiler Controller Calibration Curve</title>
    <rect width=\"100%\" height=\"100%\" fill=\"#ffffff\" />
    <polyline fill=\"none\" stroke=\"#d3d3d3\" stroke-width=\"2\" points=\"{y_axis}\" />
    <polyline fill=\"none\" stroke=\"#d3d3d3\" stroke-width=\"2\" points=\"{x_axis}\" />
    <polyline fill=\"none\" stroke=\"#ff7f0e\" stroke-width=\"4\" stroke-linejoin=\"round\" stroke-linecap=\"round\" points=\"{polyline}\" />
    <text x=\"{SVG_WIDTH / 2}\" y=\"{SVG_PADDING / 2}\" text-anchor=\"middle\" font-size=\"24\" font-family=\"sans-serif\">Calibration Curve</text>
    <text x=\"{SVG_WIDTH / 2}\" y=\"{SVG_HEIGHT - SVG_PADDING / 4}\" text-anchor=\"middle\" font-size=\"18\" font-family=\"sans-serif\">Brightness (%)</text>
    <g transform=\"rotate(-90)\">
        <text x=\"{-SVG_HEIGHT / 2}\" y=\"{SVG_PADDING / 2}\" text-anchor=\"middle\" font-size=\"18\" font-family=\"sans-serif\">Watts</text>
    </g>
</svg>
"""
        return svg.encode("utf-8")