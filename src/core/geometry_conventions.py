"""Known source geometry conventions for the viewer coordinate space."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GeometryConvention:
    """Geometry transforms required before publishing a source mesh."""

    reverse_winding: bool = False


def geometry_convention_for(game):
    """Return the proven geometry convention for a detected game profile."""
    game_id = getattr(game, "game", game)
    return GeometryConvention(
        reverse_winding=str(game_id or "").casefold() == "wuwa")


__all__ = ["GeometryConvention", "geometry_convention_for"]
