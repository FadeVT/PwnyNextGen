"""
Bayesian Optimization for Pagergotchi Timing Parameters

Ported from Phase 3A pwnagotchi NextGen intelligence layer.
Adapted for Hak5 WiFi Pineapple Pager (256 MB RAM constraint).

Replaces fixed timing parameters with GP-based Bayesian Optimization.
Converges in 50-100 evaluations (not thousands).

Key constraints for Pager:
- Observation history capped at 80 entries (GP kernel matrix = 80x80 = ~51KB)
- Pure-Python GP is O(n^3) -- 80 observations keeps suggest() under 1s on desktop
- No external dependencies (scikit-optimize, GPyOpt, numpy not available)
- All computation is pure Python stdlib
"""

import math
import random
import logging

log = logging.getLogger('pagergotchi.nextgen.bayesian_optimizer')

# Parameter space for Pagergotchi timing
# Adjusted from Pi defaults for Pager's pineapd backend
TIMING_PARAMS = {
    'recon_time': (5.0, 120.0),       # Seconds to scan before attacking
    'hop_recon_time': (2.0, 60.0),     # Wait time after deauth before channel hop
    'min_recon_time': (1.0, 30.0),     # Wait time after assoc before channel hop
    'ap_ttl': (30.0, 600.0),           # AP timeout (for internal tracking)
    'sta_ttl': (30.0, 600.0),          # Station timeout
}

# Maximum observation history (memory + CPU constraint for 256 MB Pager)
# Pure-Python GP is O(n^3) for Cholesky. At n=80, suggest() completes in <1s
# on desktop; ~2-3s on Pager hardware. At n=150, it takes 5+ minutes.
# 80 observations is more than sufficient for 5 parameters to converge.
MAX_OBSERVATIONS = 80


class GaussianProcess:
    """Minimal Gaussian Process with RBF kernel for Bayesian optimization.

    Suitable for optimizing 3-6 parameters with 50-80 observations.
    Memory: O(n^2) where n = observations. At n=80, ~51KB for kernel matrix.
    Compute: O(n^3) for Cholesky. At n=80, <1s on desktop Python.
    """

    def __init__(self, length_scale=1.0, noise=0.1):
        self.length_scale = length_scale
        self.noise = noise
        self.X = []
        self.y = []

    def _rbf_kernel(self, x1, x2):
        """Radial Basis Function (squared exponential) kernel."""
        sq_dist = sum((a - b) ** 2 for a, b in zip(x1, x2))
        return math.exp(-0.5 * sq_dist / (self.length_scale ** 2))

    def _kernel_matrix(self, X):
        """Compute kernel matrix K(X, X) + noise * I."""
        n = len(X)
        K = [[0.0] * n for _ in range(n)]
        for i in range(n):
            for j in range(n):
                K[i][j] = self._rbf_kernel(X[i], X[j])
                if i == j:
                    K[i][j] += self.noise ** 2
        return K

    def _kernel_vector(self, X, x):
        """Compute kernel vector k(X, x)."""
        return [self._rbf_kernel(xi, x) for xi in X]

    @staticmethod
    def _cholesky(A):
        """Cholesky decomposition A = LL^T. Returns L."""
        n = len(A)
        L = [[0.0] * n for _ in range(n)]
        for j in range(n):
            s = sum(L[j][k] ** 2 for k in range(j))
            L[j][j] = math.sqrt(max(A[j][j] - s, 1e-10))
            for i in range(j + 1, n):
                s = sum(L[i][k] * L[j][k] for k in range(j))
                L[i][j] = (A[i][j] - s) / L[j][j]
        return L

    @staticmethod
    def _solve_triangular_lower(L, b):
        """Solve Lx = b where L is lower triangular."""
        n = len(b)
        x = [0.0] * n
        for i in range(n):
            s = sum(L[i][j] * x[j] for j in range(i))
            x[i] = (b[i] - s) / L[i][i]
        return x

    @staticmethod
    def _solve_triangular_upper(U, b):
        """Solve Ux = b where U is upper triangular."""
        n = len(b)
        x = [0.0] * n
        for i in range(n - 1, -1, -1):
            s = sum(U[i][j] * x[j] for j in range(i + 1, n))
            x[i] = (b[i] - s) / U[i][i]
        return x

    def fit(self, X, y):
        """Fit the GP to observed data."""
        self.X = [list(x) for x in X]
        self.y = list(y)

    def predict(self, x):
        """Predict mean and variance at a new point x."""
        if not self.X:
            return 0.0, 1.0

        K = self._kernel_matrix(self.X)
        k = self._kernel_vector(self.X, x)

        try:
            L = self._cholesky(K)
        except (ValueError, ZeroDivisionError):
            return 0.0, 1.0

        alpha = self._solve_triangular_lower(L, self.y)
        alpha = self._solve_triangular_upper(
            [[L[j][i] for j in range(len(L))] for i in range(len(L))],
            alpha
        )

        mean = sum(a * ki for a, ki in zip(alpha, k))

        v = self._solve_triangular_lower(L, k)
        k_star = self._rbf_kernel(x, x) + self.noise ** 2
        variance = k_star - sum(vi ** 2 for vi in v)
        variance = max(variance, 1e-10)

        return mean, variance


class BayesianOptimizer:
    """Bayesian optimizer for Pagergotchi timing parameters.

    Optimizes continuous parameters using GP-based Expected Improvement.
    History capped at MAX_OBSERVATIONS to respect 256 MB RAM constraint.
    """

    def __init__(self, parameters=None, gp_noise=0.1, gp_length_scale=0.5,
                 n_initial=10, n_candidates=200):
        if parameters is None:
            parameters = TIMING_PARAMS

        self.param_names = list(parameters.keys())
        self.bounds = [parameters[name] for name in self.param_names]
        self.n_dims = len(self.param_names)
        self.n_initial = n_initial
        self.n_candidates = n_candidates

        self.gp = GaussianProcess(length_scale=gp_length_scale, noise=gp_noise)

        self.X_history = []
        self.y_history = []
        self.param_history = []
        self.best_reward = float('-inf')
        self.best_params = None

    def _normalize(self, params_dict):
        """Normalize parameters to [0, 1] for GP."""
        x = []
        for name, (lo, hi) in zip(self.param_names, self.bounds):
            val = params_dict[name]
            x.append((val - lo) / (hi - lo) if hi > lo else 0.5)
        return x

    def _denormalize(self, x):
        """Convert normalized vector back to parameter dict."""
        params = {}
        for i, (name, (lo, hi)) in enumerate(zip(self.param_names, self.bounds)):
            params[name] = lo + x[i] * (hi - lo)
        return params

    def _random_params(self):
        """Generate random parameter vector in normalized space."""
        return [random.random() for _ in range(self.n_dims)]

    def _expected_improvement(self, x, xi=0.01):
        """Compute Expected Improvement at point x."""
        mean, var = self.gp.predict(x)
        sigma = math.sqrt(var)

        if sigma < 1e-10:
            return 0.0

        z = (mean - self.best_reward - xi) / sigma

        # Standard normal PDF
        phi = math.exp(-0.5 * z * z) / math.sqrt(2 * math.pi)

        # Standard normal CDF (Abramowitz and Stegun approximation)
        if z > 6:
            Phi = 1.0
        elif z < -6:
            Phi = 0.0
        else:
            t = 1.0 / (1.0 + 0.2316419 * abs(z))
            poly = t * (0.319381530 + t * (-0.356563782 + t * (1.781477937 +
                        t * (-1.821255978 + t * 1.330274429))))
            if z >= 0:
                Phi = 1.0 - phi * poly
            else:
                Phi = phi * poly

        ei = (mean - self.best_reward - xi) * Phi + sigma * phi
        return ei

    def suggest(self):
        """Suggest next parameters to evaluate."""
        # Initial exploration: random sampling
        if len(self.X_history) < self.n_initial:
            x = self._random_params()
            return self._denormalize(x)

        # Fit GP to observed data
        self.gp.fit(self.X_history, self.y_history)

        # Optimize acquisition function via random search
        best_ei = -1.0
        best_x = None

        for _ in range(self.n_candidates):
            x = self._random_params()
            ei = self._expected_improvement(x)
            if ei > best_ei:
                best_ei = ei
                best_x = x

        # If EI is very low everywhere, add random exploration
        if best_ei < 1e-8:
            best_x = self._random_params()

        return self._denormalize(best_x)

    def observe(self, params_dict, reward):
        """Record an observation (parameters -> reward)."""
        x = self._normalize(params_dict)
        self.X_history.append(x)
        self.y_history.append(reward)
        self.param_history.append(dict(params_dict))

        if reward > self.best_reward:
            self.best_reward = reward
            self.best_params = dict(params_dict)

        # Cap history to respect memory constraint
        if len(self.X_history) > MAX_OBSERVATIONS:
            # Keep best observation + most recent ones
            best_idx = self.y_history.index(max(self.y_history))
            if best_idx < len(self.X_history) - MAX_OBSERVATIONS + 1:
                # Best is about to be evicted, move it to front
                self.X_history[0] = self.X_history[best_idx]
                self.y_history[0] = self.y_history[best_idx]
                self.param_history[0] = self.param_history[best_idx]

            # Trim oldest entries (keep most recent MAX_OBSERVATIONS)
            excess = len(self.X_history) - MAX_OBSERVATIONS
            self.X_history = self.X_history[excess:]
            self.y_history = self.y_history[excess:]
            self.param_history = self.param_history[excess:]

    def get_best(self):
        """Return the best parameters found so far."""
        return self.best_params, self.best_reward

    def get_state(self):
        """Serialize state for persistence."""
        return {
            'param_names': self.param_names,
            'bounds': {name: bounds for name, bounds in zip(self.param_names, self.bounds)},
            'X_history': self.X_history,
            'y_history': self.y_history,
            'param_history': self.param_history,
            'best_reward': self.best_reward,
            'best_params': self.best_params,
            'n_initial': self.n_initial,
        }

    def load_state(self, state):
        """Restore state from persistence."""
        self.X_history = state.get('X_history', [])
        self.y_history = state.get('y_history', [])
        self.param_history = state.get('param_history', [])
        self.best_reward = state.get('best_reward', float('-inf'))
        self.best_params = state.get('best_params', None)

    def summary(self):
        """Return optimization summary."""
        return {
            'n_evaluations': len(self.X_history),
            'best_reward': self.best_reward,
            'best_params': self.best_params,
            'param_names': self.param_names,
            'bounds': {name: bounds for name, bounds in zip(self.param_names, self.bounds)},
        }
