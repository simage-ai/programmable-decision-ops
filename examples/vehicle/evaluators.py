"""Evaluators for the vehicle example. Each receives only its declared inputs and parameters."""


def range_model(tank_gal: float, mpg: float, terrain_factor: float) -> float:
    """Operating range in miles: usable fuel times fuel economy, derated for terrain."""
    return tank_gal * mpg * terrain_factor
