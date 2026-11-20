"""Seeded Monte Carlo (Task 3), seed 42."""

import numpy as np
import pytest

from app.modules.calculation.engine import compute_emissions
from app.modules.uncertainty.monte_carlo import make_sampler, simulate_engine
from app.schemas.workspace import UncertaintySpec
from tests.helpers import benchmark_engine_inputs, ev


def test_percentiles_bracket_mean_and_match_deterministic():
    inputs = benchmark_engine_inputs()
    mc = simulate_engine(inputs, iterations=5000, seed=42)
    see = compute_emissions(inputs).see_tco2_per_t
    assert mc.p5 <= mc.mean <= mc.p95
    assert mc.mean == pytest.approx(see, rel=0.01)
    assert mc.iterations == 5000


def test_same_seed_identical_output():
    a = simulate_engine(benchmark_engine_inputs(), iterations=2000, seed=42)
    b = simulate_engine(benchmark_engine_inputs(), iterations=2000, seed=42)
    c = simulate_engine(benchmark_engine_inputs(), iterations=2000, seed=7)
    assert a == b
    assert a != c


def test_fallback_case_has_wider_band():
    measured = simulate_engine(benchmark_engine_inputs(), seed=42)
    fallback = simulate_engine(benchmark_engine_inputs(grid_fallback=True), seed=42)
    assert (fallback.p95 - fallback.p5) > (measured.p95 - measured.p5)


def test_sampler_distributions():
    rng = np.random.default_rng(42)
    sample = make_sampler(rng, 4000)
    tri = sample(ev(1.0, "fraction", uncertainty=UncertaintySpec(kind="triangular", low=0.98, high=1.0)), "of")
    assert tri.min() >= 0.98 and tri.max() <= 1.0
    clipped = sample(ev(1.0, "fraction", uncertainty=UncertaintySpec(kind="relative", rel=0.2)), "of")
    assert clipped.max() <= 1.0
    exact = sample(ev(5.0, "t"), "production")
    assert (exact == 5.0).all()
    zero = sample(ev(0.0, "t"), "fuel")
    assert (zero == 0.0).all()
    rel = sample(ev(100.0, "L"), "fuel")
    assert rel.std() == pytest.approx(100 * 0.05 / 1.96, rel=0.1)
