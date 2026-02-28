# Stock Pagergotchi vs NextGen Pagergotchi: Benchmark Comparison

## Test Environment

- 50 simulated APs across 25 channels (2.4 GHz + 5 GHz)
- 149 total associated clients
- 13 open networks (filtered by both stock and NextGen)
- Base capture probability: 15% + client count bonus + signal bonus
- 100 epoch simulation
- Random seed: 42 (reproducible)

## Head-to-Head Results

| Metric | Stock | NextGen | Delta |
|--------|-------|---------|-------|
| Unique handshakes captured | 37 | 33 | -11% |
| Total attack attempts | 3,700 | 183 | -95% |
| Wasted attacks (already captured) | 3,357 | 0 | -100% |
| Waste ratio | 90.7% | 0.0% | -90.7 pts |
| Attacks per handshake | 100.0 | 5.5 | -94.5% |
| Epoch of first capture | N/A | N/A | -- |

## Efficiency Analysis

Stock Pagergotchi attacks every AP on every epoch, regardless of whether
it has already captured a handshake. After capturing 37 handshakes across
100 epochs, 90.7% of all attacks were wasted on already-captured targets.

NextGen Pagergotchi uses CaptureContext to skip already-captured targets
and TacticalEngine to prioritize high-value targets. Zero attacks are
wasted. Every attack targets an uncaptured network.

## Memory Profile

| Component | State Size |
|-----------|-----------|
| Channel Bandit (66 channels, 100 epochs) | 7.6 KB |
| Bayesian Optimizer (80 observations at cap) | 14.9 KB |
| Capture Context (200 captured MACs) | 4.1 KB |
| **Total** | **26.6 KB** |
| **% of 256 MB budget** | **0.01%** |

## Tri-Band Bandit Convergence

Productive channels: {6 (2G), 36 (5G), 149 (5G)}

After 100 epochs:
- Productive channel avg success rate: 0.67
- Non-productive channel avg success rate: 0.00
- Band distribution: 248 scans on 2G, 252 scans on 5G (balanced)

The bandit correctly identifies productive channels across multiple bands
and allocates scan time accordingly.

## Mode Comparison (20 epochs, 30 APs)

| Mode | Total Attacks | Handshakes Captured |
|------|--------------|---------------------|
| Active | 175 | 19 |
| Passive | 0 | 0 |
| Assist | 400 | 19 |

- Passive mode produces zero attacks (listen-only, as designed)
- Assist mode is 2.3x more aggressive than Active
- Both Active and Assist capture the same number of handshakes

## Bayesian Optimizer Convergence

Test function: reward peaks at recon_time=60, hop_recon_time=30.

After 50 evaluations (10 random + 40 GP-guided):
- Best recon_time: 60.4 (target: 60.0, error: 0.7%)
- Best hop_recon_time: 29.8 (target: 30.0, error: 0.7%)

The optimizer converges to near-optimal timing parameters within 50 epochs.

## Computational Performance

| Operation | Desktop Python 3.x |
|-----------|-------------------|
| ChannelBandit.select_channels(k=5) | <1ms |
| TacticalEngine.plan_epoch(50 APs) | <1ms |
| BayesianOptimizer.suggest() at 20 obs | <10ms |
| BayesianOptimizer.suggest() at 40 obs | ~100ms |
| BayesianOptimizer.suggest() at 60 obs | ~500ms |
| BayesianOptimizer.suggest() at 80 obs | ~1s |

The Bayesian optimizer is the computational bottleneck (O(n^3) Cholesky
decomposition in pure Python). At MAX_OBSERVATIONS=80, suggest() completes
in under 1 second on desktop. On Pager hardware, estimated 2-3 seconds.
This runs once per epoch (~30+ seconds), so the overhead is acceptable.
