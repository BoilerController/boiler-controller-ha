"""Logic for translating power readings into Shelly dimmer percentages."""
from __future__ import annotations
import logging

from dataclasses import dataclass, field

from .const import MAX_EXPORT_WATTS

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class Calculator:
    """Encapsulate the dimmer percentage calculation logic."""

    max_power_watts: float = 3000.0
    # List of (watt_threshold, dimmer_percentage) tuples.
    thresholds: list[tuple[float, int]] | None = None
    # holds the source of the currently used thresholds
    # "calibration" if from a calibration profile, "default" if we are using the default curve.
    _threshold_source: str = field(init=False, default="default")
    # Debug trace of the last calculation performed.
    _last_trace: dict[str, float | int | str] | None = field(init=False, default=None)

    # (export_watt, percentage) -- export_watt uses absolute values for readability.
    COARSE_THRESHOLDS = [
        (200, 10),
        (400, 20),
        (600, 30),
        (800, 40),
        (1000, 50),
        (1200, 60),
        (1400, 70),
        (1600, 80),
        (1800, 90),
        (2200, 100),
    ]

    def __post_init__(self):
        if not self.thresholds:
            self.thresholds = list(self.COARSE_THRESHOLDS)
            self._threshold_source = "default"
        else:
            self._threshold_source = "calibration"
        self._last_trace = None

    def calculate(
        self,
        power_value: float,
        min_dimmer: int,
        max_dimmer: int,
        *,
        boiler_consumption: float | None = None,
    ) -> int:
        """Return the dimmer percentage for the given power value."""

        boiler_watts = max(0.0, float(boiler_consumption)) if boiler_consumption else 0.0
        grid_flow_watts = float(power_value)

        # Reconstruct the pre-boiler export by adding Shelly usage back in.
        export_watts = max(0.0, -grid_flow_watts + boiler_watts)

        # Set the clipping flag if we exceed the hard limit.
        # e.g. we don't want to push more than 2.2kW into the boiler.
        # as that could trip breakers or damage equipment.
        clipped = False
        if export_watts > MAX_EXPORT_WATTS:
            _LOGGER.debug(
                "Export %.1f W exceeds hard limit %.1f W; capping to %.1f W",
                export_watts,
                MAX_EXPORT_WATTS,
                MAX_EXPORT_WATTS,
            )
            export_watts = MAX_EXPORT_WATTS
            clipped = True
        if export_watts == 0:
            self._last_trace = {
                "source": "calibration profile" if self._threshold_source == "calibration" else "default curve",
                "grid_flow_watts": grid_flow_watts,
                "boiler_watts": boiler_watts,
                "note": "no_surplus",
            }
            _LOGGER.debug(
                "Insufficient surplus (grid %.1f W import, boiler %.1f W); keeping dimmer at 0%%",
                grid_flow_watts,
                boiler_watts,
            )
            return 0

        # Determine which set of thresholds to use.
        source_label = "calibration profile" if self._threshold_source == "calibration" else "default curve"
        thresholds = self.thresholds or self.COARSE_THRESHOLDS

        # Track the upper bound of the coarse segment we'll fall into.
        base_percentage = 100
        # Keep the lower watt boundary of the current segment.
        lower_limit = 0
        # Keep the lower percentage boundary so we can interpolate.
        lower_percentage = 0
        # The index of the matching threshold. We use this for logging only.
        match_index = len(thresholds) - 1
        # The index of the lower point in the segment. we use this for logging only.
        lower_index = 0

        # Find the coarse segment we fall into.
        for idx, (limit, percentage) in enumerate(thresholds):
            if export_watts <= limit:
                base_percentage = percentage
                match_index = idx
                break
            # Move the lower bound forward until we find the matching segment.
            lower_limit = limit
            lower_percentage = percentage
            lower_index = idx

        # if we exceed the highest threshold, return max dimmer.
        if export_watts > thresholds[-1][0]:
            upper_limit, upper_percentage = thresholds[-1]
            self._last_trace = {
                "source": source_label,
                "export_watts": export_watts,
                "segment_upper_watts": upper_limit,
                "segment_upper_percentage": upper_percentage,
                "note": "hard_cap" if clipped else "above_max",
                "grid_flow_watts": grid_flow_watts,
                "boiler_watts": boiler_watts,
            }
            _LOGGER.debug(
                "Export %.1f W exceeds %s point #%d (%.1f W -> %d%%); forcing 100%%",
                export_watts,
                source_label,
                len(thresholds),
                upper_limit,
                upper_percentage,
            )
            return 100

        # Width of the current watt interval (avoid divide by zero).
        span_watts = max(1, limit - lower_limit)
        # Percentage delta covered by this interval.
        span_percentage = max(1, base_percentage - lower_percentage)
        # How far we are into the interval.
        remaining_watts = export_watts - lower_limit
        # Interpolate the fine-grained percentage within the segment.
        fine_percentage = lower_percentage + (remaining_watts / span_watts) * span_percentage

        # Calculate the index of the lower point in the segment for logging.
        lower_point_index = lower_index + 1 if lower_limit or lower_percentage else 0
        self._last_trace = {
            "source": source_label,
            "export_watts": export_watts,
            "segment_lower_watts": lower_limit,
            "segment_lower_percentage": lower_percentage,
            "segment_upper_watts": limit,
            "segment_upper_percentage": base_percentage,
            "segment_index": match_index + 1,
            "lower_point_index": lower_point_index,
            "grid_flow_watts": grid_flow_watts,
            "boiler_watts": boiler_watts,
            "note": "hard_cap" if clipped else "segment",
        }
        _LOGGER.debug(
            "Export %.1f W (grid %.1f W, boiler %.1f W)%s matched %s point #%d (%.1f W -> %d%%); interpolated %.1f%% between lower %.1f W (%d%%) and upper %.1f W (%d%%)",
            export_watts,
            grid_flow_watts,
            boiler_watts,
            " [capped]" if clipped else "",
            source_label,
            match_index + 1,
            limit,
            base_percentage,
            fine_percentage,
            lower_limit,
            lower_percentage,
            limit,
            base_percentage,
        )
        return max(min_dimmer, min(max_dimmer, round(fine_percentage)))

    def set_thresholds(self, thresholds: list[tuple[float, int]] | None) -> None:
        """Install a new watt-to-percentage table for future calculations."""

        if not thresholds:
            self.thresholds = list(self.COARSE_THRESHOLDS)
            self._threshold_source = "default"
            self._last_trace = None
            return

        sanitized: list[tuple[float, int]] = []
        for watts, percentage in thresholds:
            try:
                clean_watts = max(0.0, float(watts))
                clean_percentage = max(0, min(100, int(percentage)))
            except (TypeError, ValueError):
                continue
            sanitized.append((clean_watts, clean_percentage))

        sanitized.sort(key=lambda item: item[0])
        if sanitized:
            self.thresholds = sanitized
            self._threshold_source = "calibration"
        else:
            self.thresholds = list(self.COARSE_THRESHOLDS)
            self._threshold_source = "default"
        self._last_trace = None
