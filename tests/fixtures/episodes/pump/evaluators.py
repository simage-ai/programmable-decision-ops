"""Evaluator for the episode-test fixture."""


def runtime_model(fuel_l: float, burn_l_per_h: float, duty: float) -> float:
    """Hours of running on one tank: fuel over burn rate, divided by the duty factor."""
    return fuel_l / burn_l_per_h / duty
