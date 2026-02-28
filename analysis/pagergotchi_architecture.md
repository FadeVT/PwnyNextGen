# Pagergotchi Architecture Analysis

## Overview
Pagergotchi is brAinphreAk's port of jayofelony's pwnagotchi to the Hak5 WiFi Pineapple Pager. It replaces bettercap with PineAP (pineapd) as the attack engine and uses the Pager's native display (480x222 RGB565) via libpagerctl.so instead of Waveshare e-ink.

## Module Map

### Core
- `__init__.py` -- Package init, device name, uptime, CPU/mem/temp, battery
- `agent.py` -- Main agent class (inherits Client + Automata + AsyncAdvertiser)
- `main.py` -- Entry point, main loop (`do_auto_mode()`), button monitor thread
- `automata.py` -- Mood state machine (bored/sad/angry/excited/motivated)
- `bettercap.py` -- PineAPBackend + bettercap-compatible Client shim

### AI (minimal)
- `ai/epoch.py` -- Epoch tracking with observation histograms
- `ai/reward.py` -- Simple reward function (handshakes=10, deauths=1)

### UI
- `ui/view.py` -- Main display renderer (native Pager, not PIL)
- `ui/menu.py` -- Startup menu, pause menu, theme system, settings
- `ui/components.py` -- Text, LabeledValue, Line display components
- `ui/faces.py` -- ASCII face definitions
- `ui/state.py` -- UI state management with change tracking
- `ui/hw/__init__.py` -- Hardware abstraction (empty on Pager)

### Support
- `voice.py` -- Status message generation
- `log.py` -- Logging setup, LastSession tracking
- `plugins.py` -- No-op plugin stub
- `gps.py` -- GPS support (optional USB GPS)
- `ap_logger.py` -- AP logging with WiGLE CSV export
- `mesh/` -- Mesh networking (mostly unused on Pager)
- `utils.py` -- Utility functions

## Data Flow

```
payload.sh
  |
  v
run_pagergotchi.py --> main.py:main()
  |
  +-> load_config() -- reads config.conf (INI format)
  +-> StartupMenu.show_main_menu() -- user presses GREEN to start
  +-> View(config) -- initializes display via libpagerctl.so
  +-> Agent(view, config) -- creates agent
  |     |
  |     +-> Client.__init__() -- sets up PineAP backend connection
  |     +-> Automata.__init__() -- creates Epoch tracker
  |     +-> AsyncAdvertiser.__init__() -- mesh advertising (unused)
  |
  +-> do_auto_mode(agent) -- MAIN LOOP
        |
        +-> start_button_monitor() -- background thread for button input
        +-> agent.start()
        |     |
        |     +-> setup_events() -- configure event silencing
        |     +-> start_monitor_mode() -- verify wlan1mon exists
        |     +-> start_event_polling() -- websocket event consumer thread
        |     +-> start_session_fetcher() -- background stats update thread
        |     +-> GPS start, AP logger start
        |
        +-> LOOP:
              |
              +-> agent.recon() -- scan all channels for recon_time seconds
              |     |
              |     +-> self.run('wifi.recon.channel clear') or specific channels
              |     +-> wait_for(recon_time)
              |
              +-> agent.get_access_points_by_channel() -- get APs grouped by channel
              |     |
              |     +-> self.session() -- PineAPBackend.get_session_data()
              |     +-> filter by whitelist/blacklist
              |     +-> sort by channel population (most populated first)
              |
              +-> For each channel:
              |     |
              |     +-> agent.set_channel(ch) -- lock to channel
              |     |     +-> self.run('wifi.recon.channel %d')
              |     |     +-> PineAPBackend.set_channel() --> _pineap EXAMINE CHANNEL
              |     |
              |     +-> For each AP on channel:
              |           |
              |           +-> agent.associate(ap) -- PMKID capture attempt
              |           |     +-> _should_interact(mac) -- check history/captured
              |           |     +-> self.run('wifi.assoc %s') --> focus_bssid()
              |           |
              |           +-> For each client on AP:
              |                 +-> agent.deauth(ap, sta) -- 4-way handshake capture
              |                       +-> _should_interact(sta_mac)
              |                       +-> self.run('wifi.deauth %s %s')
              |                       +-> PineAPBackend.deauth() --> _pineap DEAUTH
              |
              +-> agent.next_epoch() -- mood update, epoch counters reset
```

## Decision Points in Stock Agent

### Channel Selection
**Location:** `main.py:do_auto_mode()` line ~195
**Logic:** `agent.get_access_points_by_channel()` returns channels sorted by AP count. Sequential iteration. No intelligence.
**NextGen replacement:** Channel bandit (Thompson Sampling) selects which channels to scan.

### Target Selection
**Location:** `main.py:do_auto_mode()` lines ~207-225
**Logic:** Attack every AP on every channel sequentially. Only filter: `_should_interact()`.
**NextGen replacement:** Tactical engine scores targets, selects best ones, skips captured/open/WPA3.

### Attack Type Selection
**Location:** `agent.py:associate()` and `agent.py:deauth()`
**Logic:** Fixed pattern: always associate, then deauth all clients. No PMKID-aware routing.
**NextGen replacement:** Tactical engine selects attack type (deauth, assoc, CSA, skip).

### Timing
**Location:** `config` personality section, hardcoded defaults in `main.py:load_config()`
**Logic:** Fixed timing parameters (recon_time=30, hop_recon_time=10, throttle_d=0.9).
**NextGen replacement:** Bayesian optimizer tunes these parameters based on observed rewards.

### Handshake Detection
**Location:** `agent.py:_check_handshakes_direct()` and `_on_event()` websocket handler
**Logic:** File scanning of /root/loot/handshakes/*.22000 + event queue from PineAPBackend.
**NextGen integration:** CaptureContext wraps this for skip logic awareness.

## PineAPBackend Architecture (bettercap.py)

The PineAPBackend class runs three background threads:
1. **Recon thread** (`_recon_loop`) -- polls `_pineap RECON APS format=json` every 3s
2. **Handshake monitor** (`_handshake_monitor_loop`) -- scans for new .22000 files every 2s
3. **Client tracker** (`_client_tracker_loop`) -- runs tcpdump to track client-AP associations

The Client class wraps PineAPBackend and translates bettercap commands:
- `run('wifi.recon.channel X')` --> `backend.set_channel(X)` --> `_pineap EXAMINE CHANNEL X 300`
- `run('wifi.assoc MAC')` --> `backend.focus_bssid(MAC)` --> `_pineap EXAMINE BSSID MAC 300`
- `run('wifi.deauth AP STA')` --> `backend.deauth(AP, STA)` --> `_pineap DEAUTH AP STA CH`
- `session()` --> `backend.get_session_data()` --> JSON matching bettercap session format

## Resource Constraints
- RAM: 256 MB total (shared with OS + pineapd + tcpdump)
- Storage: 4 GB EMMC
- CPU: MIPS (embedded), limited compute
- Display: 480x222 RGB565, 2 FPS refresh target
- No GPU, no ML framework, no pip packages in payload
