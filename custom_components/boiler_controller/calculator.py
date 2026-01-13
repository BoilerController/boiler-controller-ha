"""Logic for translating power readings into Shelly dimmer percentages."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class Calculator:
    """Encapsulate the dimmer percentage calculation logic."""

    max_power_watts: float = 3000.0

    def calculate(self, power_value: float, min_dimmer: int, max_dimmer: int) -> int:
        """Return the dimmer percentage for the given power value."""
        return 0
        # if power_value <= 0:
        #     return 0

        # # Ensure bounds are valid before interpolating
        # min_dimmer = max(0, min(100, int(min_dimmer)))
        # max_dimmer = max(min_dimmer, min(100, int(max_dimmer)))

        # if power_value >= self.max_power_watts:
        #     return max_dimmer

        # scale = max(0.0, min(1.0, power_value / self.max_power_watts))
        # percentage = int(min_dimmer + (max_dimmer - min_dimmer) * scale)
        # return max(min_dimmer, min(max_dimmer, percentage))
