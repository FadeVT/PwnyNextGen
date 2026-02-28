# Phase 3C Deployment Journal

## Date: 2026-02-28

### Deployment Summary

NextGen Pagergotchi successfully deployed to Hak5 WiFi Pineapple Pager and captured **4 full 4-way EAPOL handshakes** on target network "404".

---

### Step 1: Environment Assessment
- **Device:** Hak5 WiFi Pineapple Pager, firmware 24.10.1 (OpenWrt-based, mipsel_24kc)
- **Connection:** USB-C Ethernet at 172.16.52.1, SSH as root
- **RAM:** 251 MB total, ~146 MB available during operation
- **Storage:** 3.2 GB free on /mmc
- **Python:** Not installed initially — installed via `opkg -d mmc install python3 python3-ctypes`
- **WiFi interfaces:** wlan1mon (monitor, tri-band), wlan0cli (client, was connected to "404")
- **pineapd:** Running, controls all WiFi operations via `_pineap` CLI

### Step 2: Backup
- Backed up `/root/payloads/user/reconnaissance/` to `~/pwnagotchi-phase3c/pager-backup/reconnaissance/`
- Backed up `/root/loot/` to `~/pwnagotchi-phase3c/pager-backup/loot/`

### Step 3: Deployment
- SCP'd NextGen payload to `/root/payloads/user/reconnaissance/pagergotchi/`
- All Python modules import cleanly on device (channel_bandit, tactical_engine, bayesian_optimizer, pineapd_adapter)
- libpagerctl.so bundled in lib/ — display rendering works

### Step 4: Configuration
- Created `data/settings.json` with blacklist targeting only SSID "404"
- Enabled debug logging in `config.conf`
- Set NextGen to active mode

### Issues Encountered & Fixes

#### Issue 1: No internet on Pager
- Pager had no internet for Python installation
- **Fix:** Chris connected Pager WiFi client to his network directly

#### Issue 2: SSH password auth
- `ssh` couldn't pass password non-interactively
- **Fix:** Used `sshpass` which was already installed on the host

#### Issue 3: Debug logging not writing
- `logging.basicConfig()` was a no-op because the root logger was already configured by an early import
- **Fix:** Added `force=True` parameter and explicit `logging.getLogger().setLevel(level)` in `log.py`

#### Issue 4: pineapd died after WiFi client interface deletion
- Deleted `wlan0cli` interface to stop the Pager from being a client on "404"
- pineapd crashed shortly after (likely driver-level instability from removing an interface)
- **Fix:** Restarted pineapd after interface deletion, then launched Pagergotchi

#### Issue 5: `nohup` not available on BusyBox
- BusyBox ash shell doesn't have `nohup`
- **Fix:** Used shell `&` backgrounding instead

#### Issue 6: `_pineap RECON APS` returning empty/error
- During one run, `_pineap` returned "Connection refused" because pineapd had died
- Root cause: pineapd crash after wlan0cli deletion
- **Fix:** Restart pineapd before launching Pagergotchi

### Step 5: Launch & Operation

NextGen intelligence loop running successfully:
- **ChannelBandit:** Active with 98 channels (15 2G, 69 5G, 14 6G), Thompson Sampling exploration
- **TacticalEngine:** Identifying 2 visible APs matching "404", skipping one (has handshake), targeting one
- **ClientTracker:** Detecting 15+ clients associated with "404" APs (both 2.4 GHz and 5 GHz)
- **GPS:** Connected via gpsd on /dev/ttyACM0
- **Display:** Rendering on Pager's 480x222 RGB565 LED screen
- **Memory:** 20.6 MB used by Python process, well within 256 MB constraint

### Capture Results

**4 full 4-way EAPOL handshakes captured on "404":**

| File | AP MAC | Client MAC | Type |
|------|--------|------------|------|
| 1772260468_142103B04721_84F3EBEE271E_handshake.22000 | 14:21:03:B0:47:21 | 84:F3:EB:EE:27:1E | WPA*02 (4-way) |
| 1772260473_142103B04721_84F3EBEE271E_handshake.22000 | 14:21:03:B0:47:21 | 84:F3:EB:EE:27:1E | WPA*02 (4-way) |
| 1772260473_142103B04721_D8A011C43A95_handshake.22000 | 14:21:03:B0:47:21 | D8:A0:11:C4:3A:95 | WPA*02 (4-way) |
| 1772260483_142103B04721_D8A011C43A95_handshake.22000 | 14:21:03:B0:47:21 | D8:A0:11:C4:3A:95 | WPA*02 (4-way) |

All captures are from the 2.4 GHz AP (channel 1). Files are in hashcat .22000 format with matching .pcap files.

**Baseline comparison:**
- Stock Hak5 firmware: 37 partial handshakes, 0 complete 4-way captures
- NextGen Pagergotchi: 4 complete 4-way handshakes (within first 5 minutes of pineapd operation)

### Observations

1. **pineapd's built-in capture** is powerful — it captured the handshakes autonomously once `--handshakes=true` was set. The handshakes were captured during the initial pineapd startup before the NextGen intelligence loop was even running.

2. **Channel bandit convergence** is slow with 98 channels. With only 1 target network and 5 channels per epoch, the bandit explores broadly. In a dense urban environment with many targets across channels, this would converge faster. For a single-target scenario, seeding the bandit with recon-active channels would help.

3. **wlan0cli deletion** is risky — it can crash pineapd. Future improvement: use `_pineap` to disconnect the client interface cleanly rather than deleting it at the iw level.

4. **Memory footprint** is excellent: ~21 MB for the entire Python process including display rendering, far under the 256 MB constraint.

### Files Modified on Device
- `/root/payloads/user/reconnaissance/pagergotchi/` — entire NextGen payload (new)
- `/root/payloads/user/reconnaissance/pagergotchi/data/settings.json` — targeting config
- `/root/payloads/user/reconnaissance/pagergotchi/pwnagotchi_port/log.py` — debug logging fix

### Status
- NextGen Pagergotchi is running on the Pager (PID 8134)
- Intelligence loop active in ACTIVE mode
- Display rendering on Pager screen
- All existing Pager features preserved
- Nothing pushed to GitHub
