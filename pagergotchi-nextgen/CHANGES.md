# NextGen Intelligence Layer Changes

This document describes all modifications made to the Pagergotchi codebase
to integrate the NextGen intelligence layer. The original Pagergotchi by
brAinphreAk is preserved -- NextGen is an opt-in enhancement toggled via
`config.conf`.

## New Files

### `pwnagotchi_port/nextgen/` (Intelligence Layer)

All new code lives in this isolated directory. No original files were deleted.

| File | Purpose |
|------|---------|
| `__init__.py` | `NextGenBrain` orchestrator -- initializes all components, manages state persistence, provides clean API for agent integration |
| `channel_bandit.py` | Thompson Sampling multi-armed bandit for WiFi channel selection. Tri-band aware (2.4/5/6 GHz). Three modes: active, passive, assist |
| `tactical_engine.py` | Target scoring, skip logic, attack type routing. `CaptureContext` tracks captured handshakes (.22000 files). `RewardV2` epoch reward function |
| `bayesian_optimizer.py` | Gaussian Process-based Bayesian optimization for timing parameters (recon_time, hop_recon_time, etc.). Pure Python, no dependencies |
| `pineapd_adapter.py` | Translation layer: converts NextGen's abstract attack decisions into pineapd API calls via the existing bettercap.py Client shim |

## Modified Files

### `pwnagotchi_port/agent.py`

- **Added**: `NextGenBrain` initialization in `__init__()` (guarded by config toggle)
- **Added**: NextGen handshake notification in `_on_event()` when a handshake is detected
- **Added**: NextGen skip check at the top of `_should_interact()` -- if NextGen knows a MAC is already captured, skip immediately
- **Added**: NextGen state saving in `_save_recovery_data()` for persistence across restarts

### `pwnagotchi_port/main.py`

- **Refactored**: `do_auto_mode()` split into three functions:
  - `_do_stock_loop()` -- original attack logic, unchanged
  - `_do_nextgen_loop()` -- intelligent attack loop using bandit + tactical engine + optimizer
  - `do_auto_mode()` -- dispatcher that calls stock or nextgen loop based on config
- **Added**: `_do_nextgen_loop()` implements a 6-phase epoch:
  1. Recon scan
  2. Get visible APs
  3. Channel bandit selects channels to focus on
  4. Tactical engine plans prioritized attack list
  5. Execute attacks per channel (lock, attack targets, unlock)
  6. Update bandit rewards and optimizer timing
- **Added**: NextGen config defaults in `load_config()`
- **Added**: `[nextgen]` section parsing from config.conf

### `config.conf`

Added `[nextgen]` section:

```ini
[nextgen]
enabled = true
mode = active
channels_per_epoch = 5
max_targets_per_epoch = 20
optimize_timing = true
bandit_window = 30
bo_initial_epochs = 10
```

### `pwnagotchi_port/ui/view.py`

- **Added**: `mode` text element to display state -- shows current NextGen mode (e.g., "NG:ACT", "NG:PAS", "NG:AST")
- **Added**: `mode` case in theme color assignment

## Post-Deployment Changes

These changes were made after observing the system run on real hardware. They are included in the current codebase.

### Intelligence Improvements (made live on hardware)

1. **Bandit boost from recon** (`channel_bandit.py`, `__init__.py`): Added `boost(channel, weight)` method. `plan_attacks()` feeds client density per channel as a warm prior, solving the cold-start problem. Result: bandit selected productive channels on epoch 1 instead of taking 50+ epochs.

2. **Per-client capture tracking** (`tactical_engine.py`): `CaptureContext` now parses client MACs from pineapd handshake filenames (`timestamp_APMAC_CLIENTMAC_handshake.22000`). Tracks which (AP, client) pairs have been captured via `_captured_clients` dict and `get_new_clients()` method.

3. **Re-attack APs with new clients** (`tactical_engine.py`): `_score_active()` no longer blanket-skips captured APs. If new clients are present that haven't been captured, the AP gets a reduced positive score (up to 8.0 vs 10+ for uncaptured). More client handshakes = more PSK crack attempts.

### Clean Exit Fix

- **`main.py`**: Added signal handler (SIGTERM/SIGINT) with `sys.exit(0)`, `atexit` handler for service restoration, and `_restore_pager_services()` function that restarts pineapd then pineapplepager on exit. Signal handlers are re-registered after each `pager_init()` call because libpagerctl.so overrides Python's signal handlers at the C level.
- **`view.py`**: `cleanup()` now resets display (brightness 100%, clear to black, flip) before calling `pager_cleanup()`.
- **`payload.sh`**: Added `_restore_services()` function with `EXIT` trap as a shell-level safety net.

### Startup Menu Mode Selector

- **`menu.py`**: Added "Mode" menu item to startup menu. Cycles through Active/Passive/Assist when NextGen is enabled. Selection is persisted to `data/settings.json` and applied on startup.

## Design Decisions

1. **Direct integration, not plugin system**: Pagergotchi's plugin system is a no-op stub. Rather than building plugin infrastructure, NextGen integrates directly into agent.py and main.py with a clean config toggle.

2. **Offset-based 6 GHz channel numbering**: WiFi 6E channel numbers (1, 5, 9, ...) overlap with 2.4 GHz. To avoid ambiguity, 6 GHz channels use an offset of +190 internally (ch 1 -> 191, ch 5 -> 195). Helper functions `raw_6g_to_offset()` and `offset_to_raw_6g()` handle conversion.

3. **MAX_OBSERVATIONS = 80**: The pure-Python GP is O(n^3) for Cholesky decomposition. At 80 observations, `suggest()` completes in under 1 second on desktop and ~2-3s on Pager hardware. This is sufficient for convergence with 5 timing parameters.

4. **CSA mapped to broadcast deauth**: pineapd lacks native Channel Switch Announcement support. The tactical engine maps CSA attacks to broadcast deauth as a fallback.

5. **Sequential channel locking**: pineapd can only lock to one channel at a time (via EXAMINE CHANNEL). The NextGen loop locks to each selected channel sequentially, attacks targets on that channel, then moves to the next.

## Backwards Compatibility

- Setting `[nextgen] enabled = false` (the default) restores stock Pagergotchi behavior
- All existing features (themes, privacy, GPS, whitelist/blacklist, menus, buttons, app handoff) are preserved
- The deployment path is identical: `scp -r pagergotchi/ root@172.16.52.1:/root/payloads/user/reconnaissance/`
- Existing `data/settings.json` and `data/recovery.json` are not modified
- NextGen state is stored separately in `data/nextgen_state.json`
