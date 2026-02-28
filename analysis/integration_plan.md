# Integration Plan: NextGen Intelligence into Pagergotchi

## Strategy
Direct integration into agent.py and main.py. No plugin system (Pagergotchi's is a no-op stub). NextGen components live in `pwnagotchi_port/nextgen/` as a clean, isolated module. Toggleable via config.

## File Modifications

### New Files (nextgen/ directory)
| File | Purpose | Source |
|------|---------|--------|
| `nextgen/__init__.py` | NextGenBrain orchestrator class | NEW |
| `nextgen/channel_bandit.py` | Thompson Sampling channel selection | Port from Phase 3A, extended for tri-band |
| `nextgen/tactical_engine.py` | Target scoring, skip logic, CaptureContext | Port from Phase 3A, adapted for .22000 files |
| `nextgen/bayesian_optimizer.py` | GP-based timing optimization | Port from Phase 3A, capped history |
| `nextgen/pineapd_adapter.py` | NextGen commands --> pineapd API calls | NEW |

### Modified Files
| File | Changes |
|------|---------|
| `agent.py` | Import NextGenBrain, initialize in __init__, use in attack flow |
| `main.py` | Update do_auto_mode() with NextGen-aware main loop, add to load_config() |
| `config.conf` | Add [nextgen] section |
| `ui/view.py` | Add mode indicator element |

### Untouched Files
- `automata.py` -- Mood system preserved exactly
- `bettercap.py` -- PineAPBackend unchanged, NextGen talks through existing Client shim
- `ui/menu.py` -- May add mode toggle to pause menu (minimal change)
- `ui/components.py`, `ui/faces.py`, `ui/state.py` -- No changes
- `voice.py`, `log.py`, `gps.py`, `ap_logger.py` -- No changes
- `plugins.py` -- No changes
- `payload.sh`, `run_pagergotchi.py` -- No changes
- `mesh/` -- No changes

## Integration Points

### 1. Agent Initialization (agent.py __init__)
```python
# After existing init code:
from pwnagotchi_port.nextgen import NextGenBrain
self._nextgen = NextGenBrain(config, self)  # May be None if disabled
```

### 2. Main Loop (main.py do_auto_mode())
```
IF NextGen enabled:
    1. Recon phase (same as stock)
    2. Get all visible APs (same as stock)
    3. Channel bandit selects channels to focus on
    4. For each bandit-selected channel:
       a. Set channel via pineapd adapter
       b. Tactical engine scores and ranks targets
       c. For each target in plan:
          - Check attack type (deauth/assoc/CSA/skip)
          - Execute via pineapd adapter
       d. Bandit records reward for channel
    5. Bayesian optimizer observes epoch reward, suggests new timing
    6. next_epoch()
ELSE:
    Stock behavior (unchanged)
```

### 3. Handshake Events (agent.py _on_event)
```python
# After existing handshake recording:
if self._nextgen:
    self._nextgen.on_handshake(ap_mac, channel)
```

### 4. Epoch Transition (automata.py next_epoch via agent)
```python
# After stock epoch transition:
if self._nextgen:
    self._nextgen.on_epoch(self._epoch.epoch, self._epoch.data())
```

### 5. Config Toggle
```ini
[nextgen]
enabled = true
mode = active     # active, passive, assist
aggressiveness = 0.5
channels_per_epoch = 5
max_targets_per_epoch = 20
optimize_timing = true
```

### 6. Display Mode Indicator
Add small text element showing current operational mode in the top bar area.
Format: "NG:ACT" / "NG:PAS" / "NG:AST" / "STOCK"

## Tri-Band Channel Bandit Extension

Phase 3A channel bandit only knew 14 channels (2.4 GHz). The Pager supports 2.4 + 5 + 6 GHz.

Approach: Use the actual supported channels list from `utils.iface_channels()` (which queries `iw` for real hardware capabilities). The bandit treats each channel as an independent arm regardless of band. This is correct because Thompson Sampling doesn't care about frequency -- it only cares about reward per channel.

Additional feature: Band-level statistics rollup for logging/display.

Channel count estimate:
- 2.4 GHz: ~14 channels
- 5 GHz: ~28 channels (varies by regulatory domain)
- 6 GHz: ~24 channels (if supported)
- Total: ~66 arms for the bandit

With k=5 channels per epoch (configurable), the bandit samples 5 of 66 channels per epoch. Thompson Sampling converges well even with large arm counts -- it naturally focuses on productive channels within 20-30 epochs.

## Memory Budget

Target: < 2 MB for all NextGen state (out of 256 MB total)

| Component | Estimated Memory |
|-----------|-----------------|
| Channel Bandit (66 channels, window=30) | ~50 KB |
| Tactical Engine (CaptureContext, 500 APs max) | ~200 KB |
| Bayesian Optimizer (150 observations, 5 params) | ~300 KB |
| pineapd Adapter (stateless) | ~5 KB |
| NextGenBrain orchestrator | ~10 KB |
| **Total** | **~565 KB** |

Well within the 2 MB budget. Phase 3A peak was 734 KB on the Pi with more channels.

## Backward Compatibility

When `[nextgen] enabled = false`:
- NextGenBrain is not initialized
- agent.py uses stock _should_interact() logic
- main.py do_auto_mode() runs stock sequential attack loop
- No NextGen state files created
- No mode indicator on display
- Behavior is identical to unmodified Pagergotchi

This is verified by the test suite with a dedicated "stock mode" test case.
