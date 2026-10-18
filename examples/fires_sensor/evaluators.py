"""Evaluators for the synthetic counterfire radar example. Each receives only its declared inputs and parameters."""


def detection_range(reference_range_km: float, rcs_factor: float, range_exponent: float) -> float:
    """Detection range against the expected threat: the reference range scaled by relative RCS ** exponent."""
    return reference_range_km * rcs_factor ** range_exponent
