from __future__ import annotations

from pathlib import Path

import pytest

from pdo.graph import compile_graph
from pdo.load import index, load_state
from pdo.run import run

ROOT = Path(__file__).resolve().parents[1]
VEHICLE = ROOT / "examples" / "vehicle"


@pytest.fixture
def vehicle():
    return load_state(VEHICLE)


def assign(state, target: str, slot: str, value):
    """A deep copy of `state` with one slot of one object changed."""
    out = state.model_copy(deep=True)
    setattr(index(out)[target], slot, value)
    return out


def evaluate_state(state, base_dir=VEHICLE):
    return run(compile_graph(state, base_dir))
