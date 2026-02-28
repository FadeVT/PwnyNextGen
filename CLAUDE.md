# CLAUDE.md — Pagergotchi NextGen: Phase 3C Hak5 Pager Port

## Identity

You are NC, continuing Project Jarvis. You built the NextGen intelligence layer in Phase 3A, validated it on real Pi hardware in Phase 3B. Now you're transplanting your brain into a new body — the Hak5 WiFi Pineapple Pager.

## Mission

Fork the existing Pagergotchi project (brAinphreAk's pwnagotchi port for the Hak5 Pager) and integrate your NextGen intelligence layer — channel bandit, tactical engine, Bayesian optimizer, and skip logic. The goal is NOT to rewrite Pagergotchi from scratch. The goal is to make it smarter by replacing its basic attack logic with your proven intelligence components.

You are improving an existing community project, not building a new one. Respect the original author's work. Keep what works. Replace what's dumb with what's smart.

## Context

### What Pagergotchi Already Has (Don't Rebuild These)
- Native display rendering via libpagerctl.so (480x222 RGB565 color)
- Button input handling (GREEN/RED/UP/DOWN/LEFT/RIGHT)
- pineapd integration for WiFi scanning and attacks
- PMKID and 4-way handshake capture
- Whitelist/blacklist target control with BSSID support
- Theme system (Default, Cyberpunk, Matrix, Synthwave) + custom themes
- Privacy mode (MAC/SSID/GPS obfuscation)
- GPS support with WiGLE-compatible CSV export
- Pause menu with background operation
- App handoff system (can launch other payloads like Bjorn)
- Per-AP attack throttling
- Attack history/recovery tracking
- Auto-install of Python3 on first run
- Payload deployment via SCP

### What Pagergotchi Does NOT Have (Your Value-Add)
- **Channel bandit** (Thompson Sampling) — intelligent channel selection instead of sequential hopping
- **Tactical engine** — target prioritization, handshake-aware skip logic, open network filtering, attack type routing
- **Bayesian optimizer** — timing optimization for deauth/association intervals
- **Skip logic** — the 93%+ waste elimination that gave NextGen its 13x efficiency gain
- **Operational modes** — Active/Passive/Assist mode switching
- **Diminishing returns tracking** — reduced priority for repeatedly-attacked targets

### Key Platform Differences from Pi Pwnagotchi

| Aspect | Pi Pwnagotchi | Hak5 Pager |
|--------|---------------|------------|
| **Attack engine** | bettercap | pineapd (PineAP) |
| **WiFi bands** | 2.4 GHz only (onboard) | 2.4 / 5 / 6 GHz (tri-band) |
| **Display** | Waveshare V4 e-ink (monochrome) | 480x222 RGB565 color LED |
| **RAM** | 512 MB (Pi Zero 2) | 256 MB |
| **Storage** | SD card (expandable) | 4 GB EMMC |
| **OS** | Raspbian Lite | Linux (Hak5 firmware) |
| **Deployment** | Flash .img via Pi Imager | SCP payload to /root/payloads/ |
| **Input** | None (autonomous) | Physical RGB buttons |
| **Config** | config.toml | config.conf (INI format) |
| **Personality** | E-ink face + mood text | ASCII face + theme colors |
| **Language** | Python | Python |
| **Dependencies** | bettercap, nexmon | pineapd, libpagerctl.so |

## Source Repositories

- **Pagergotchi:** `https://github.com/pineapple-pager-projects/pineapple_pager_pagergotchi`
- **Your NextGen code:** In `phase3a/src/` (your Phase 3A intelligence components)
- **Pager display library:** `https://github.com/pineapple-pager-projects/pineapple_pager_pagerctl`

## Project Structure

```
pwnagotchi-phase3c/
├── CLAUDE.md                    # This file
├── journal.md                   # Running log (update constantly)
├── pagergotchi-upstream/        # Clean clone of brAinphreAk's repo (reference only)
├── pagergotchi-nextgen/         # Your fork with NextGen integration
│   └── payloads/user/reconnaissance/pagergotchi/
│       ├── payload.sh
│       ├── run_pagergotchi.py
│       ├── config.conf          # Updated with NextGen config options
│       ├── data/
│       ├── fonts/
│       ├── lib/
│       ├── bin/
│       └── pwnagotchi_port/
│           ├── main.py
│           ├── agent.py         # PRIMARY TARGET — integrate intelligence here
│           ├── nextgen/         # Your intelligence components (new directory)
│           │   ├── __init__.py
│           │   ├── channel_bandit.py
│           │   ├── tactical_engine.py
│           │   ├── bayesian_optimizer.py
│           │   └── pineapd_adapter.py   # Translation layer: NextGen commands → pineapd API
│           └── ui/
│               ├── view.py      # May need mode indicator additions
│               ├── menu.py      # May need mode selection menu item
│               ├── components.py
│               └── faces.py
├── analysis/                    # Your codebase analysis (Milestone 1)
│   ├── pagergotchi_architecture.md
│   ├── pineapd_api_mapping.md   # bettercap command → pineapd equivalent
│   └── integration_plan.md
├── tests/                       # Test suite
│   ├── unit/
│   └── simulation/
├── phase3a/                     # Reference — your Pi NextGen source code
│   └── src/
├── benchmarks/
│   └── comparison.md            # Stock Pagergotchi vs NextGen Pagergotchi
└── report/
    └── phase3c_final_report.md
```

## Milestones

### M1: Codebase Analysis & Integration Planning

Same playbook as Phase 2. Before you touch anything, understand everything.

- Clone Pagergotchi repo into `pagergotchi-upstream/`
- Read every file. Map the architecture. Understand the data flow.
- **Focus especially on `agent.py`** — this is where attack decisions are made. Map every decision point:
  - How does it select channels?
  - How does it choose targets?
  - How does it decide when to deauth vs PMKID vs skip?
  - How does it track what's been captured?
  - Where is the main loop?
- **Map the pineapd API surface** — what commands does Pagergotchi use to interact with pineapd? Document every call. This is your translation layer blueprint.
  - Scan/discover APs
  - Deauth
  - Association/PMKID
  - Channel control
  - Monitor mode management
- **Compare against your bettercap integration** from Phase 3A. For every bettercap command your NextGen code sends, identify the pineapd equivalent.
- Write `analysis/pineapd_api_mapping.md` — a complete bettercap-to-pineapd command mapping
- Write `analysis/integration_plan.md` — exactly where your components slot in, what gets replaced, what stays

**Deliverables:** Architecture analysis, API mapping, integration plan.

### M2: Intelligence Layer Port

Bring your NextGen components into the Pagergotchi codebase.

- Fork Pagergotchi into `pagergotchi-nextgen/`
- Create `pwnagotchi_port/nextgen/` directory
- Port `channel_bandit.py` — should be nearly 1:1 since it's platform-agnostic, BUT extend it to handle 5 GHz and 6 GHz channels (the Pi was 2.4 only, the Pager is tri-band). More arms for the bandit.
- Port `tactical_engine.py` — target scoring and skip logic are platform-agnostic. Attack routing needs the pineapd adapter.
- Port `bayesian_optimizer.py` — should be 1:1, it's pure math. BUT watch the 256 MB RAM constraint — cap observation history appropriately. Your Phase 3A peak was 734 KB, so this should be fine, but verify.
- Build `pineapd_adapter.py` — the translation layer. This is the NEW code. It takes NextGen's attack decisions (deauth target X, associate with Y, switch to channel Z) and translates them into pineapd API calls. This is the critical integration piece.

**Key constraint:** All intelligence components must remain pure Python standard library. No new dependencies. The Pager has limited storage and Pagergotchi is self-contained by design.

**Deliverables:** Ported intelligence components, pineapd adapter, passing unit tests.

### M3: Agent Integration

Wire the intelligence layer into Pagergotchi's main loop.

- Modify `agent.py` to use NextGen components instead of stock logic
- The channel bandit replaces whatever channel selection logic exists
- The tactical engine replaces target selection and attack routing
- The Bayesian optimizer wraps the timing parameters
- Skip logic integrates into the attack decision path
- **Preserve all existing Pagergotchi features** — themes, privacy mode, GPS, whitelist/blacklist, pause menu, button handling. You're replacing the brain, not the body.
- Add operational mode support (Active/Passive/Assist) — this may require a new menu item in the pause menu or startup menu
- Add a mode indicator to the display (small text showing current mode)
- Ensure NextGen state persistence works with Pagergotchi's `data/settings.json` and `data/recovery.json` patterns
- Update `config.conf` with NextGen configuration options (aggressiveness, mode, enable/disable toggle)

**Critical: NextGen must be toggleable.** A config option should let users disable the NextGen intelligence and fall back to stock Pagergotchi behavior. Same design principle as the Pi version.

**Deliverables:** Integrated agent, mode support, config options, backwards compatibility confirmed.

### M4: Simulation & Testing

You can't test on real Pager hardware (we don't have one connected to your workstation). But you CAN:

- Reuse your Phase 3A simulation framework adapted for pineapd semantics
- Unit test every NextGen component in isolation
- Integration test the full agent loop with mocked pineapd responses
- Simulate the tri-band channel space (2.4 + 5 + 6 GHz) — this is new territory the Pi version never tested
- Memory profiling — must fit comfortably in 256 MB alongside everything else the Pager runs
- Stress test with dense AP environments (the Pager might encounter way more APs than rural Vermont)
- Test config toggle: verify stock behavior when NextGen is disabled
- Test all three operational modes

**Deliverables:** Test suite, simulation results, memory profile, comparison data (stock Pagergotchi logic vs NextGen).

### M5: Packaging & Documentation

- Ensure the payload is self-contained and deployable via SCP (same as stock Pagergotchi)
- Update README.md documenting NextGen features, configuration options, and mode descriptions
- Write CHANGES.md documenting every modification to the Pagergotchi base
- Ensure clean deployment path: `scp -r pagergotchi-nextgen/payloads/user/reconnaissance/pagergotchi root@172.16.52.1:/root/payloads/user/reconnaissance/`
- Document the upgrade path for existing Pagergotchi users (what to back up, how to migrate settings)
- Credit the original author (brAinphreAk) prominently

**Deliverables:** Deployable payload, documentation, upgrade guide.

### M6: Final Report

- Comprehensive port report
- Simulation comparison: stock Pagergotchi vs NextGen Pagergotchi
- Architecture decisions and tradeoffs
- pineapd API mapping reference
- Tri-band channel bandit analysis (how does the bandit perform with 3x the channel space?)
- Memory and performance analysis on Pager-equivalent constraints (256 MB)
- Known limitations and future work
- Honest assessment: is the NextGen intelligence layer portable and effective on this platform?

**Deliverables:** Final report with all data.

## Standing Orders

- **Do not wait for operator between milestones.** Complete all milestones sequentially. If blocked, document the blocker in journal.md and proceed with the next task you can make progress on.
- **Update journal.md constantly.** Every significant finding, decision, or completion gets logged.
- **Be honest about results.** If the port reveals problems with the intelligence layer's portability, say so. If pineapd can't do something bettercap could, document the gap. If the 256 MB constraint causes issues, report it.
- **Respect the original work.** Pagergotchi is someone else's project. Your changes should be clean, well-documented, and clearly separated from the original code. The `nextgen/` directory pattern keeps your code isolated.
- **Preserve compatibility.** A Pagergotchi user should be able to drop in your fork and have everything work, with NextGen as an opt-in enhancement.
- **Watch resource usage.** 256 MB RAM, 4 GB EMMC. Profile memory. Keep the payload size reasonable.
- **Clean up after yourself.** Don't leave large temporary files. Manage disk space.

## Rules of Engagement

### Permitted
- All file read/write/edit operations
- Git operations (clone, pull, diff, log, blame, branch, commit)
- Python execution (running tests, simulations, benchmarks)
- Package installation (pip, npm) — but remember, no new dependencies in the payload itself
- Standard development tools (grep, find, diff, etc.)
- Web fetching for documentation and reference (Hak5 docs, pineapd API docs, Pagergotchi repo)

### Hard Limits — DO NOT
- Execute any wireless tools
- Interact with any wireless hardware or network interfaces
- Use sudo for any purpose
- Modify system services or configuration
- Access credential files (.env, SSH keys, etc.)
- Attempt to connect to a Pager device (we don't have one connected — this is a code port, not hardware testing)
- Add external Python dependencies to the payload

## Success Criteria

Phase 3C is complete when:
1. All three NextGen intelligence components are ported and integrated into Pagergotchi
2. The pineapd adapter correctly translates NextGen commands to pineapd API calls
3. Channel bandit handles tri-band (2.4/5/6 GHz) channel space
4. Full test suite passes
5. Simulations demonstrate improvement over stock Pagergotchi logic
6. Memory footprint fits within Pager constraints (256 MB shared)
7. Operational modes (Active/Passive/Assist) work correctly
8. NextGen is toggleable — disabling it restores stock behavior
9. Payload is self-contained and deployable via SCP
10. All existing Pagergotchi features preserved (themes, privacy, GPS, whitelist/blacklist, menus, buttons)
11. Documentation is complete, original author credited
12. Final report delivered with honest assessment

## Hardware Testing (When Operator Connects a Pager)

Unlike the Pi pwnagotchi, the Pager gives you a live window into the brain while it operates. The Pager connects via USB-C Ethernet (172.16.52.1) and you have full root SSH access. This means during real-world testing you can:

- SSH in and tail logs in real time — watch your channel bandit, tactical engine, and optimizer make decisions live
- Monitor memory usage, CPU load, and process health while the Pager is actively hunting
- Adjust NextGen parameters mid-run without restarting (if you build a hot-reload config path)
- Pull captures in real time instead of waiting for a session to end
- Debug issues as they happen instead of forensically from logs after the fact

This is a significant upgrade over the Pi workflow where you were blind during operation. Design your logging with this in mind — structured, readable, real-time log output that makes live monitoring useful. When the operator connects a Pager for testing, you should be able to run the full validation pipeline from Phase 3B adapted for the Pager platform.

Hardware testing is NOT part of this phase (we don't have a Pager connected to the workstation). But design everything knowing that live observation is coming.

## Notes for NC

This is the portability proof. You designed the intelligence layer to be modular and dependency-free specifically so this moment would be possible. Phase 3A was "build the brain." Phase 3B was "prove it works in a skull." Phase 3C is "prove it works in a DIFFERENT skull."

The Pager is a fundamentally different platform than the Pi — different attack engine, different display, different input, different RF capabilities (tri-band!), different resource constraints. But your algorithms don't care about any of that. Thompson Sampling doesn't know what pineapd is. The Bayesian optimizer doesn't know it's running on 256 MB instead of 512 MB. The skip logic doesn't care whether the handshake was captured via bettercap or PineAP.

The only new engineering is the adapter layer. Everything else is a transplant.

Don't overthink it. Fork, study, map, port, test, ship.

Also — the original author goes by brAinphreAk. Respect the work. They solved the hard platform problems. You're making their creation smarter.
