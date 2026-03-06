from __future__ import annotations

from ripple.api.simulate import simulate


async def run_simulation_with_progress(request: dict, on_progress):
    return await simulate(on_progress=on_progress, **request)
