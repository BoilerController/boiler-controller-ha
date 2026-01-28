"""Logic for translating power readings into Shelly dimmer percentages."""
from __future__ import annotations
import logging

from dataclasses import dataclass, field

from .const import MAX_EXPORT_WATTS

_LOGGER = logging.getLogger(__name__)

# Default calibration profile from 20% to 100% in 1% steps.
DEFAULT_CALIBRATION_PROFILE: list[tuple[int, float]] = [
    (20, 555.0),
    (21, 554.0),
    (22, 563.0),
    (23, 597.0),
    (24, 547.0),
    (25, 560.0),
    (26, 554.0),
    (27, 586.0),
    (28, 618.0),
    (29, 631.0),
    (30, 677.0),
    (31, 718.0),
    (32, 779.0),
    (33, 797.0),
    (34, 848.0),
    (35, 854.0),
    (36, 945.0),
    (37, 969.0),
    (38, 1031.0),
    (39, 1062.0),
    (40, 1092.0),
    (41, 1155.0),
    (42, 1158.0),
    (43, 1196.0),
    (44, 1255.0),
    (45, 1258.0),
    (46, 1291.0),
    (47, 1325.0),
    (48, 1348.0),
    (49, 1377.0),
    (50, 1428.0),
    (51, 1426.0),
    (52, 1484.0),
    (53, 1489.0),
    (54, 1503.0),
    (55, 1525.0),
    (56, 1519.0),
    (57, 1157.0),
    (58, 1238.0),
    (59, 1285.0),
    (60, 1312.0),
    (61, 1322.0),
    (62, 1327.0),
    (63, 1347.0),
    (64, 1421.0),
    (65, 1448.0),
    (66, 1444.0),
    (67, 1463.0),
    (68, 1520.0),
    (69, 1519.0),
    (70, 1564.0),
    (71, 1560.0),
    (72, 1586.0),
    (73, 1615.0),
    (74, 1613.0),
    (75, 1650.0),
    (76, 1647.0),
    (77, 1641.0),
    (78, 1646.0),
    (79, 1654.0),
    (80, 1644.0),
    (81, 1664.0),
    (82, 1671.0),
    (83, 1676.0),
    (84, 1679.0),
    (85, 1705.0),
    (86, 1687.0),
    (87, 1688.0),
    (88, 1697.0),
    (89, 1701.0),
    (90, 1715.0),
    (91, 1710.0),
    (92, 1712.0),
    (93, 1713.0),
    (94, 1721.0),
    (95, 1719.0),
    (96, 1724.0),
    (97, 1725.0),
    (98, 1717.0),
    (99, 1715.0),
    (100, 1720.0),
]

DEFAULT_PERCENTAGE_PROFILE: dict[int, float] = {
    percentage: watts for percentage, watts in DEFAULT_CALIBRATION_PROFILE
}
DEFAULT_PERCENTAGE_PROFILE[0] = 0.0

DEFAULT_THRESHOLD_TABLE: list[tuple[float, int]] = [(0.0, 0)] + sorted(
    [(watts, percentage) for percentage, watts in DEFAULT_CALIBRATION_PROFILE],
    key=lambda item: item[0],
)

@dataclass(slots=True)
class Calculator:
    """Encapsulate the dimmer percentage calculation logic."""

    # List of (watt_threshold, dimmer_percentage) tuples.
    calibration_profile: list[tuple[float, int]] | None = None
    # holds the source of the currently used calibration profile
    # "calibration" if from a calibration profile, "default" if we are using the default curve.
    _calibration_profile_source: str = field(init=False, default="default")
    # Mapping of percentage -> watts for the active profile (includes 0% -> 0W).
    _percentage_profile: dict[int, float] = field(init=False, default_factory=dict)
    # Debug trace of the last calculation performed.
    _last_trace: dict[str, float | int | str] | None = field(init=False, default=None)

    def __post_init__(self):
        initial_profile = list(self.calibration_profile) if self.calibration_profile else None
        self.set_calibration_profile(initial_profile)

    def calculate(
        self,
        power_value: float,
        min_dimmer: int,
        max_dimmer: int,
        *,
        boiler_consumption: float | None = None,
        current_dimmer: int | None = None,
    ) -> int:
        """Return the dimmer percentage for the given power value."""

        boiler_watts = max(0.0, float(boiler_consumption)) if boiler_consumption else 0.0
        grid_flow_watts = float(power_value)
        current_percentage = self._normalize_percentage(current_dimmer)
        # Determine the expected boiler wattage for the current dimmer setting.
        # It looks up the calibration profile (or default curve) to find the expected wattage
        expected_watts = self._expected_watts_for_percentage(current_percentage)
        # Use either the expected wattage or the actual boiler consumption as the baseline.
        baseline_watts = expected_watts if expected_watts > 0 else boiler_watts

        # Rebuild the available export based on the calibration profile of the current dimmer value.
        target_watts = max(0.0, baseline_watts - grid_flow_watts)

        clipped = False
        if target_watts > MAX_EXPORT_WATTS:
            _LOGGER.info(
                "Target %.1f W exceeds hard limit %.1f W; capping to %.1f W",
                target_watts,
                MAX_EXPORT_WATTS,
                MAX_EXPORT_WATTS,
            )
            target_watts = MAX_EXPORT_WATTS
            clipped = True

        if target_watts <= 0:
            self._last_trace = {
                "source": "calibration profile" if self._calibration_profile_source == "calibration" else "default curve",
                "grid_flow_watts": grid_flow_watts,
                "boiler_watts": boiler_watts,
                "current_dimmer": current_percentage,
                "expected_watts": expected_watts,
                "target_watts": target_watts,
                "note": "no_surplus",
            }
            _LOGGER.info(
                "Insufficient surplus (grid %.1f W, boiler %.1f W); keeping dimmer at 0%%",
                grid_flow_watts,
                boiler_watts,
            )
            return 0

        source_label = "calibration profile" if self._calibration_profile_source == "calibration" else "default curve"
        # Find the matching segment/percentage in the calibration profile.
        thresholds = self.calibration_profile or list(DEFAULT_THRESHOLD_TABLE)
        if not thresholds:
            thresholds = list(DEFAULT_THRESHOLD_TABLE)

        # Walk the thresholds to find the matching segment.
        lower_limit, lower_percentage = thresholds[0]
        upper_limit, upper_percentage = thresholds[-1]
        selected_percentage = upper_percentage
        match_index = len(thresholds) - 1

        for idx in range(1, len(thresholds)):
            limit, percentage = thresholds[idx]
            if target_watts < limit:
                upper_limit = limit
                upper_percentage = percentage
                match_index = idx
                selected_percentage = lower_percentage
                break
            if target_watts == limit:
                lower_limit = limit
                lower_percentage = percentage
                upper_limit = limit
                upper_percentage = percentage
                match_index = idx
                selected_percentage = percentage
                break
            lower_limit = limit
            lower_percentage = percentage
        else:
            lower_limit, lower_percentage = thresholds[-1]
            upper_limit, upper_percentage = lower_limit, lower_percentage
            match_index = len(thresholds) - 1
            selected_percentage = upper_percentage

        # Build the final percentage within min/max limits.
        final_percentage = max(min_dimmer, min(max_dimmer, selected_percentage))

        self._last_trace = {
            "source": source_label,
            "grid_flow_watts": grid_flow_watts,
            "boiler_watts": boiler_watts,
            "current_dimmer": current_percentage,
            "expected_watts": expected_watts,
            "target_watts": target_watts,
            "segment_lower_watts": lower_limit,
            "segment_lower_percentage": lower_percentage,
            "segment_upper_watts": upper_limit,
            "segment_upper_percentage": upper_percentage,
            "segment_index": match_index + 1,
            "selected_percentage": selected_percentage,
            "note": "hard_cap" if clipped else "segment",
        }
        _LOGGER.info(
            "Grid %.1f W, boiler %.1f W, dimmer %s%% -> baseline %.1f W, target %.1f W%s; selected %d%% via %s point #%d (lower %.1f W @ %d%%, upper %.1f W @ %d%%)",
            grid_flow_watts,
            boiler_watts,
            current_percentage,
            expected_watts,
            target_watts,
            " [capped]" if clipped else "",
            final_percentage,
            source_label,
            match_index + 1,
            lower_limit,
            lower_percentage,
            upper_limit,
            upper_percentage,
        )
        return final_percentage

    def set_calibration_profile(self, profile: list[tuple[float, int]] | None) -> None:
        """Install a new watt-to-percentage table for future calculations."""

        if not profile:
            self.calibration_profile = list(DEFAULT_THRESHOLD_TABLE)
            self._percentage_profile = dict(DEFAULT_PERCENTAGE_PROFILE)
            self._calibration_profile_source = "default"
            self._last_trace = None
            return

        sanitized: list[tuple[float, int]] = []
        percentage_profile: dict[int, float] = {}
        for watts, percentage in profile:
            try:
                clean_watts = max(0.0, float(watts))
                clean_percentage = max(0, min(100, int(percentage)))
            except (TypeError, ValueError):
                continue
            sanitized.append((clean_watts, clean_percentage))
            percentage_profile[clean_percentage] = clean_watts

        sanitized.sort(key=lambda item: item[0])
        if not sanitized:
            self.calibration_profile = list(DEFAULT_THRESHOLD_TABLE)
            self._percentage_profile = dict(DEFAULT_PERCENTAGE_PROFILE)
            self._calibration_profile_source = "default"
            self._last_trace = None
            return

        first_watts, first_percentage = sanitized[0]
        if first_watts != 0.0 or first_percentage != 0:
            sanitized.insert(0, (0.0, 0))
        percentage_profile.setdefault(0, 0.0)

        self.calibration_profile = sanitized
        self._percentage_profile = dict(percentage_profile)
        self._calibration_profile_source = "calibration"
        self._last_trace = None

    def _expected_watts_for_percentage(self, percentage: int | None) -> float:
        """Return the expected watts for a given dimmer percentage based on the profile."""

        if percentage is None or percentage <= 0:
            return 0.0

        profile = self._percentage_profile or {}
        if not profile:
            return 0.0

        if percentage in profile:
            return profile[percentage]

        sorted_percentages = sorted(profile.keys())
        lower = None
        upper = None
        for value in sorted_percentages:
            if value < percentage:
                lower = value
                continue
            if value > percentage:
                upper = value
                break

        if lower is None and upper is None:
            return 0.0
        if lower is None:
            return profile[upper]
        if upper is None:
            return profile[lower]

        lower_watts = profile[lower]
        upper_watts = profile[upper]
        span = max(1, upper - lower)
        position = (percentage - lower) / span
        return lower_watts + (upper_watts - lower_watts) * position

    @staticmethod
    def _normalize_percentage(value: int | float | None) -> int:
        """Clamp raw dimmer readings to the valid 0-100% range."""

        if value is None:
            return 0
        try:
            return max(0, min(100, int(value)))
        except (TypeError, ValueError):
            return 0
