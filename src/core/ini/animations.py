"""Public interface for baked, compute and WWMI sparse animation discovery."""

from .animation_clocks import (
    AnimationAnalysis, AnimationClock, compute_animation_control_vars,
    discover_animation_clocks, frame_condition,
)
from .animation_compute import (
    discover_compute_animations, discover_wwmi_sparse_animations,
)


__all__ = [
    "AnimationAnalysis", "AnimationClock", "discover_animation_clocks",
    "frame_condition", "discover_compute_animations",
    "discover_wwmi_sparse_animations",
    "compute_animation_control_vars",
]
