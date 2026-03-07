# ripple/__init__.py
# =============================================================================
# Ripple — Agent-Native CAS 社会行为预测引擎。 / Agent-Native CAS social behavior prediction engine.
# =============================================================================

"""Ripple — Agent-Native CAS 社会行为预测引擎。 / Agent-Native CAS social behavior prediction engine."""

from ripple.version import VERSION as __version__
from ripple.api.simulate import simulate

__all__ = ["simulate", "__version__"]
