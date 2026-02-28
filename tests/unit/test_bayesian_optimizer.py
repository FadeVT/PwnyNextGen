"""
Unit tests for Bayesian Optimizer (GP-based timing optimization)
"""
import sys
import os
import random
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'pagergotchi-nextgen',
                                'payloads', 'user', 'reconnaissance', 'pagergotchi'))

from pwnagotchi_port.nextgen.bayesian_optimizer import (
    BayesianOptimizer, GaussianProcess, TIMING_PARAMS, MAX_OBSERVATIONS,
)


def test_gp_predict_empty():
    """Test GP prediction with no data."""
    gp = GaussianProcess()
    mean, var = gp.predict([0.5, 0.5])
    assert mean == 0.0
    assert var == 1.0
    print("PASS: gp_predict_empty")


def test_gp_predict_with_data():
    """Test GP prediction with some observations."""
    gp = GaussianProcess(length_scale=1.0, noise=0.1)
    X = [[0.2, 0.3], [0.8, 0.7], [0.5, 0.5]]
    y = [1.0, 2.0, 1.5]
    gp.fit(X, y)

    mean, var = gp.predict([0.5, 0.5])
    # Mean should be close to the observed value at that point
    assert abs(mean - 1.5) < 0.5, f"Mean {mean} too far from expected 1.5"
    assert var > 0, "Variance should be positive"
    print("PASS: gp_predict_with_data")


def test_optimizer_suggest_initial():
    """Test initial suggestions are random."""
    random.seed(42)
    opt = BayesianOptimizer(n_initial=5)

    params = opt.suggest()
    assert isinstance(params, dict)
    assert all(name in params for name in TIMING_PARAMS)

    # All parameters should be within bounds
    for name, (lo, hi) in TIMING_PARAMS.items():
        assert lo <= params[name] <= hi, f"{name}={params[name]} out of [{lo}, {hi}]"

    print("PASS: optimizer_suggest_initial")


def test_optimizer_observe_and_track_best():
    """Test observation recording and best tracking."""
    random.seed(42)
    opt = BayesianOptimizer(n_initial=3)

    # Record several observations
    for i in range(5):
        params = opt.suggest()
        reward = float(i)  # Increasing rewards
        opt.observe(params, reward)

    best_params, best_reward = opt.get_best()
    assert best_reward == 4.0
    assert best_params is not None
    print("PASS: optimizer_observe_and_track_best")


def test_optimizer_convergence():
    """Test optimizer converges toward good parameters."""
    random.seed(42)

    # Simple test function: reward peaks at recon_time=60, hop_recon_time=30
    def reward_fn(params):
        r = -abs(params['recon_time'] - 60) / 60.0
        r -= abs(params['hop_recon_time'] - 30) / 30.0
        return r + 2.0  # Offset to keep positive

    opt = BayesianOptimizer(
        parameters={'recon_time': (5.0, 120.0), 'hop_recon_time': (2.0, 60.0)},
        n_initial=10,
        n_candidates=100,
    )

    for _ in range(50):
        params = opt.suggest()
        reward = reward_fn(params)
        opt.observe(params, reward)

    best_params, best_reward = opt.get_best()
    # Should be reasonably close to optimal
    assert abs(best_params['recon_time'] - 60) < 40, f"recon_time={best_params['recon_time']}"
    assert abs(best_params['hop_recon_time'] - 30) < 25, f"hop_recon_time={best_params['hop_recon_time']}"
    print("PASS: optimizer_convergence")


def test_optimizer_history_cap():
    """Test that observation history is capped at MAX_OBSERVATIONS."""
    random.seed(42)
    opt = BayesianOptimizer(n_initial=5)

    for i in range(MAX_OBSERVATIONS + 50):
        params = opt.suggest()
        opt.observe(params, float(i % 10))

    assert len(opt.X_history) <= MAX_OBSERVATIONS
    assert len(opt.y_history) <= MAX_OBSERVATIONS
    assert len(opt.param_history) <= MAX_OBSERVATIONS
    print("PASS: optimizer_history_cap")


def test_optimizer_state_persistence():
    """Test state serialization/deserialization."""
    random.seed(42)
    opt = BayesianOptimizer(n_initial=3)

    for _ in range(5):
        params = opt.suggest()
        opt.observe(params, random.random())

    state = opt.get_state()

    # Verify state is JSON-serializable
    json_str = json.dumps(state)
    assert len(json_str) > 0

    # Restore into new optimizer
    opt2 = BayesianOptimizer(n_initial=3)
    opt2.load_state(state)

    assert len(opt2.X_history) == len(opt.X_history)
    assert opt2.best_reward == opt.best_reward
    assert opt2.best_params == opt.best_params
    print("PASS: optimizer_state_persistence")


def test_optimizer_memory_usage():
    """Test memory usage stays reasonable."""
    random.seed(42)
    opt = BayesianOptimizer(n_initial=10)

    # Fill to capacity
    for i in range(MAX_OBSERVATIONS):
        params = opt.suggest()
        opt.observe(params, random.random())

    state = opt.get_state()
    state_json = json.dumps(state)
    size_kb = len(state_json) / 1024
    print(f"  Optimizer state at {MAX_OBSERVATIONS} observations: {size_kb:.1f} KB")
    assert size_kb < 200, f"State too large: {size_kb:.1f} KB"
    print("PASS: optimizer_memory_usage")


def test_optimizer_summary():
    """Test summary generation."""
    random.seed(42)
    opt = BayesianOptimizer(n_initial=3)

    for _ in range(5):
        params = opt.suggest()
        opt.observe(params, random.random())

    summary = opt.summary()
    assert summary['n_evaluations'] == 5
    assert 'best_reward' in summary
    assert 'best_params' in summary
    assert 'param_names' in summary
    print("PASS: optimizer_summary")


def test_cholesky():
    """Test Cholesky decomposition."""
    gp = GaussianProcess()

    # Simple 2x2 positive definite matrix
    A = [[4.0, 2.0], [2.0, 3.0]]
    L = gp._cholesky(A)

    # Verify L * L^T = A
    n = len(A)
    for i in range(n):
        for j in range(n):
            val = sum(L[i][k] * L[j][k] for k in range(n))
            assert abs(val - A[i][j]) < 1e-10, f"Cholesky failed at [{i}][{j}]: {val} != {A[i][j]}"

    print("PASS: cholesky")


if __name__ == '__main__':
    test_gp_predict_empty()
    test_gp_predict_with_data()
    test_optimizer_suggest_initial()
    test_optimizer_observe_and_track_best()
    test_optimizer_convergence()
    test_optimizer_history_cap()
    test_optimizer_state_persistence()
    test_optimizer_memory_usage()
    test_optimizer_summary()
    test_cholesky()
    print("\nAll Bayesian optimizer tests passed.")
