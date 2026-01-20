"""Logic for translating power readings into Shelly dimmer percentages."""
from __future__ import annotations
import logging

from dataclasses import dataclass

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class Calculator:
    """Encapsulate the dimmer percentage calculation logic."""

    max_power_watts: float = 3000.0

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

    def calculate(
        self,
        power_value: float,
        min_dimmer: int,
        max_dimmer: int,
        *,
        boiler_consumption: float | None = None,
    ) -> int:
        """Return the dimmer percentage for the given power value."""

        # Calculate total export watts (positive value).
        export_watts = max(0.0, -power_value)

        # Only add boiler consumption back when we are already exporting power.
        if export_watts > 0 and boiler_consumption:
            export_watts += max(0.0, float(boiler_consumption))
        if export_watts == 0:
            return 0

        # Track the upper bound of the coarse segment we'll fall into.
        base_percentage = 100
        # Keep the lower watt boundary of the current segment.
        lower_limit = 0
        # Keep the lower percentage boundary so we can interpolate.
        lower_percentage = 0

        for limit, percentage in self.COARSE_THRESHOLDS:
            if export_watts <= limit:
                base_percentage = percentage
                break
            # Move the lower bound forward until we find the matching segment.
            lower_limit = limit
            lower_percentage = percentage

        # if we exceed the highest threshold, return max dimmer. 
        if export_watts > self.COARSE_THRESHOLDS[-1][0]:
            return 100

        # Width of the current watt interval (avoid divide by zero).
        span_watts = max(1, limit - lower_limit)
        # Percentage delta covered by this interval.
        span_percentage = max(1, base_percentage - lower_percentage)
        # How far we are into the interval.
        remaining_watts = export_watts - lower_limit

        fine_percentage = lower_percentage + (remaining_watts / span_watts) * span_percentage
        return max(min_dimmer, min(max_dimmer, round(fine_percentage)))
