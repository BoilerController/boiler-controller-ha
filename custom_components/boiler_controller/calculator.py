"""Heating percentage calculator for the Boiler Controller."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from .const import DEFAULT_MAX_BOILER_WATTS

_LOGGER = logging.getLogger(__name__)


@dataclass
class CalculatorResult:
    """Result of a single calculator run."""

    target_percentage: int
    new_percentage: int
    available_watts: float
    grid_watts: float
    boiler_watts: float
    current_percentage: int
    max_boiler_watts: float
    capped: bool = False


@dataclass
class Calculator:
    """Translate available surplus power into a heating percentage.

    Logic:
    1.  ``available_watts = current_boiler_watts - grid_net_watts``
        - When the grid value is negative (export/surplus) this grows.
        - When the grid value is positive (import) this shrinks.
    2.  ``target_pct = round(available_watts / max_boiler_watts * 100)``
        clamped to [0, 100].
    3.  The new percentage steps at most ``max_step`` toward the target
        so the boiler ramps up/down gradually.
    """

    max_boiler_watts: float = float(DEFAULT_MAX_BOILER_WATTS)
    max_step: int = 10

    # Last trace — useful for debugging / diagnostics
    last_result: Optional[CalculatorResult] = field(init=False, default=None)

    def calculate(
        self,
        grid_watts: float,
        current_percentage: int,
        boiler_watts: float = 0.0,
    ) -> int:
        """Return the new heating percentage.

        Args:
            grid_watts:         Net grid power in W (positive = importing,
                                negative = exporting surplus).
            current_percentage: Current heating percentage reported by the
                                module (0-100).
            boiler_watts:       Current measured boiler consumption in W.
                                Used to reconstruct the available surplus.
        """
        boiler_w = max(0.0, float(boiler_watts))
        grid_w = float(grid_watts)
        current_pct = max(0, min(100, int(current_percentage)))

        # Available watts the boiler can use without importing from the grid.
        available = max(0.0, boiler_w - grid_w)

        # Cap to boiler maximum capacity.
        capped = available > self.max_boiler_watts
        available_clamped = min(available, self.max_boiler_watts)

        target_pct = int(round(available_clamped / self.max_boiler_watts * 100.0))
        target_pct = max(0, min(100, target_pct))

        # Dynamic step: use at least max_step, but when the gap is large take
        # half the remaining distance so the boiler ramps up/down faster.
        diff = abs(target_pct - current_pct)
        step = min(diff, max(self.max_step, diff // 2))
        if target_pct > current_pct:
            new_pct = current_pct + step
        elif target_pct < current_pct:
            new_pct = current_pct - step
        else:
            new_pct = current_pct

        self.last_result = CalculatorResult(
            target_percentage=target_pct,
            new_percentage=new_pct,
            available_watts=available,
            grid_watts=grid_w,
            boiler_watts=boiler_w,
            current_percentage=current_pct,
            max_boiler_watts=self.max_boiler_watts,
            capped=capped,
        )

        _LOGGER.debug(
            "Calculator: grid=%.1fW  boiler=%.1fW  available=%.1fW  "
            "target=%d%%  current=%d%%  → new=%d%%%s",
            grid_w,
            boiler_w,
            available,
            target_pct,
            current_pct,
            new_pct,
            "  [capped]" if capped else "",
        )

        return new_pct
