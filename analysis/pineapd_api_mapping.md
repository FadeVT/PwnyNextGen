# pineapd API Mapping: bettercap --> PineAP

## Command Translation Table

| NextGen Intent | bettercap (Pi) | Pagergotchi Shim | pineapd (Pager) | Notes |
|---|---|---|---|---|
| Scan APs | `wifi.recon on` + REST `/api/session` | `Client.session()` | `_pineap RECON APS format=json limit=100` | Polled every 3s by background thread |
| Set channel | `set wifi.recon.channel X` | `Client.run('wifi.recon.channel X')` | `_pineap EXAMINE CHANNEL X 300` | 300s timeout, auto-resumes hopping |
| Set multiple channels | `set wifi.recon.channel 1,6,11` | `Client.run('wifi.recon.channel 1,6,11')` | `_pineap EXAMINE CANCEL` (clear focus) | pineapd can't lock to multiple channels; clears focus and lets it hop |
| Clear channel lock | `set wifi.recon.channel clear` | `Client.run('wifi.recon.channel clear')` | `_pineap EXAMINE CANCEL` | Resume native hopping |
| Deauth (targeted) | `wifi.deauth AP_MAC STA_MAC` | `Client.run('wifi.deauth AP STA')` | `_pineap DEAUTH AP STA CH` | Channel auto-detected from AP data |
| Deauth (broadcast) | `wifi.deauth AP_MAC FF:FF:FF:FF:FF:FF` | `agent.broadcast_deauth(ap)` | `_pineap DEAUTH AP FF:FF:FF:FF:FF:FF CH` | Kicks all clients |
| Associate (PMKID) | `wifi.assoc AP_MAC` | `Client.run('wifi.assoc AP')` | `_pineap EXAMINE BSSID AP 300` | Focuses on AP, pineapd auto-captures PMKID |
| CSA (channel switch) | `wifi.csa AP_MAC CH` | N/A | N/A | **NOT SUPPORTED** by pineapd. Fallback: broadcast deauth |
| Start recon | `wifi.recon on` | `Client.run('wifi.recon on')` | `PineAPBackend.start()` + background threads | pineapd already running from payload.sh |
| Stop recon | `wifi.recon off` | `Client.run('wifi.recon off')` | `PineAPBackend.stop()` | Stops threads, restores pineapd service |
| Clear AP list | `wifi.clear` | `Client.run('wifi.clear')` | `backend.access_points.clear()` | In-memory only |
| Set AP TTL | `set wifi.ap.ttl X` | `Client.run('set wifi.ap.ttl X')` | N/A (ignored) | pineapd manages its own AP aging |
| Set STA TTL | `set wifi.sta.ttl X` | `Client.run('set wifi.sta.ttl X')` | N/A (ignored) | Client tracker uses 300s hardcoded TTL |
| Set min RSSI | `set wifi.rssi.min X` | `Client.run('set wifi.rssi.min X')` | N/A (ignored) | No RSSI filtering in pineapd |
| Handshake path | `set wifi.handshakes.file PATH` | `Client.run('set wifi.handshakes...')` | Updates `_handshakes_dir` variable | pineapd saves to /root/loot/handshakes/ |
| Event stream | WebSocket `ws://host/api` | `Client.start_websocket()` | Event queue polling (PineAPBackend) | Simulated via Queue, not real WebSocket |

## API Gaps

### No CSA Support
pineapd does not support Channel Switch Announcement attacks. The Pi's bettercap could send CSA frames to force PMF-protected clients to switch channels (bypassing deauth protection). On the Pager, the fallback is broadcast deauth, which is less effective against PMF but still disrupts non-PMF clients.

### Limited Channel Control
pineapd can lock to a single channel (EXAMINE CHANNEL) or a single BSSID (EXAMINE BSSID), but cannot be told to hop only across a specific subset of channels. When we want the bandit's selected channels (e.g., [1, 6, 11, 36, 149]), we must either:
- Lock to each channel sequentially (current approach)
- Clear focus and let pineapd hop freely (loses control)

The adapter implements sequential channel locking with per-channel dwell time.

### No Native Client Tracking
bettercap tracks client-to-AP associations natively as part of its WiFi recon. pineapd does not provide client data in its RECON APS output. Pagergotchi solves this with a tcpdump-based client tracker that parses 802.11 data frames from wlan1mon. This is less reliable than bettercap's native tracking but functional.

### Encryption Detection Limited
pineapd's RECON APS JSON does not include encryption type per-AP. The bettercap shim hardcodes 'WPA2' for all APs. This means the tactical engine's WPA3/SAE detection and WEP scoring won't have real data. Mitigation: treat all encrypted APs as WPA2 (the most common case), which doesn't significantly impact target scoring since the primary differentiator is client count and signal strength.

## Handshake File Format
- pineapd saves as `.22000` (hashcat format) and `.pcap`
- Filename pattern: `{MAC}_handshake.22000` or `{MAC}.22000`
- File content: `WPA*02*mic*ap_mac*client_mac*essid_hex*...`
- Location: `/root/loot/handshakes/`
- ESSID extractable from hex field in .22000 file

## Tri-Band Channel Space

### 2.4 GHz (Band 2)
Channels 1-14. Same as Pi pwnagotchi.

### 5 GHz (Band 5)
Channels: 36, 40, 44, 48, 52, 56, 60, 64, 100, 104, 108, 112, 116, 120, 124, 128, 132, 136, 140, 144, 149, 153, 157, 161, 165, 169, 173, 177

DFS channels (52-144) may be restricted. pineapd handles radar detection.

### 6 GHz (Band 6)
Channels: 1, 5, 9, 13, 17, 21, 25, 29, 33, 37, 41, 45, 49, 53, 57, 61, 65, 69, 73, 77, 81, 85, 89, 93 (UNII-5/6/7/8, 20 MHz spacing)

6 GHz adoption is still low. The bandit will naturally learn which bands are productive.

pineapd startup flag: `--band wlan1mon:2,5,6` enables all three bands.
