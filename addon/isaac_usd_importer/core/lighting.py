"""Normalise Isaac Sim light intensities to values Blender can actually use.

Omniverse authors light intensity in nits (cd/m^2) and Blender's USD importer
carries the number across almost unchanged: the Digital Twin Warehouse arrives
with eight area lights at ~628,300 W and a sun at ~12,000 W, which blows every
render to white.

A blind multiplier is the wrong control -- the right scale depends on the
source scene, so the user would have to rediscover it every time. Instead
normalise: the brightest non-sun light is pinned to a target wattage and the
rest keep their relative ratios, so a scene with deliberately varied lights
still looks like itself. Suns are handled separately because Blender measures
them as irradiance (W/m^2), not power.
"""

from __future__ import annotations

import logging
from typing import Any

import bpy

log = logging.getLogger(__name__)

# A 600 W area light reads as a bright industrial ceiling fixture in Blender.
DEFAULT_TARGET_WATTS = 600.0
DEFAULT_SUN_IRRADIANCE = 3.0


def normalise_lights(
    lights: list[bpy.types.Object],
    target_watts: float = DEFAULT_TARGET_WATTS,
    sun_irradiance: float = DEFAULT_SUN_IRRADIANCE,
) -> dict[str, Any]:
    """Rescale imported lights in place. Returns a summary for the report."""
    punctual = [o for o in lights if o.data.type != "SUN"]
    suns = [o for o in lights if o.data.type == "SUN"]

    report: dict[str, Any] = {
        "punctual": len(punctual),
        "suns": len(suns),
        "target_watts": target_watts,
        "sun_irradiance": sun_irradiance,
        "peak_before": 0.0,
        "factor": 1.0,
    }

    if punctual:
        peak = max(float(o.data.energy) for o in punctual)
        report["peak_before"] = peak
        if peak > 0.0:
            factor = target_watts / peak
            report["factor"] = factor
            for obj in punctual:
                obj.data.energy = float(obj.data.energy) * factor
            log.info("scaled %d light(s) by %.3g (peak %.4g W -> %.4g W)",
                     len(punctual), factor, peak, target_watts)
        else:
            log.warning("all imported lights have zero energy; left untouched")

    for obj in suns:
        obj.data.energy = sun_irradiance

    return report
