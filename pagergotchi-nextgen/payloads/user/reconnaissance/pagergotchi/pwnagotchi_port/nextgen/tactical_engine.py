"""
Tactical Target Prioritization Engine

Ported from Phase 3A pwnagotchi NextGen intelligence layer.
Adapted for Hak5 WiFi Pineapple Pager (pineapd backend, .22000 handshake files).

Supports three operational modes:
- ACTIVE: Full offensive + intelligence (default, original Phase 3A behavior)
- PASSIVE: Monitor-only, zero transmissions, captures natural handshakes
- ASSIST: Maximum aggression for flushing clients to a separate capture rig

Key improvements over stock Pagergotchi:
1. Skip already-captured targets (biggest single efficiency gain)
2. Score targets by expected value (encryption, clients, signal, freshness)
3. Select appropriate attack type (deauth, assoc, broadcast_deauth, skip)
4. Track per-AP interaction history to avoid diminishing returns
"""

import os
import re
import time
import logging
from collections import defaultdict

log = logging.getLogger('pagergotchi.nextgen.tactical_engine')

# Operational modes
MODE_ACTIVE = 'active'
MODE_PASSIVE = 'passive'
MODE_ASSIST = 'assist'
VALID_MODES = (MODE_ACTIVE, MODE_PASSIVE, MODE_ASSIST)

# Attack type constants
ATTACK_ASSOC_DEAUTH = 'assoc_then_deauth'
ATTACK_DEAUTH_ONLY = 'deauth_only'
ATTACK_BROADCAST_DEAUTH = 'broadcast_deauth'  # Replaces CSA (pineapd lacks CSA support)
ATTACK_ASSOC_ONLY = 'assoc_only'
ATTACK_SKIP = 'skip'


class CaptureContext:
    """Tracks handshake capture state across sessions.

    Adapted for Pager: scans /root/loot/handshakes/ for .22000 files
    (hashcat format, pineapd output). Maintains an in-memory index
    for fast lookup. This is the single most impactful improvement:
    not re-attacking targets we've already captured.
    """

    def __init__(self, handshake_dir="/root/loot/handshakes"):
        self.handshake_dir = handshake_dir
        self._captured = {}      # mac -> {timestamp, path, type}
        self._pmkids = set()     # macs with PMKID captured
        self._captured_clients = defaultdict(set)   # ap_mac -> set of client_macs
        self._interactions = defaultdict(int)       # mac -> count this session
        self._epoch_interactions = defaultdict(int)  # mac -> count this epoch
        self._scan_existing()

    def _scan_existing(self):
        """Scan handshake directory for existing captures."""
        if not os.path.isdir(self.handshake_dir):
            return

        import glob as glob_mod
        for pattern in ['*.22000', '*.pcap', '*.cap', '*.hccapx']:
            for f in glob_mod.glob(os.path.join(self.handshake_dir, pattern)):
                basename = os.path.basename(f)
                mac = self._extract_mac(basename)
                if mac:
                    self._captured[mac] = {
                        'timestamp': os.path.getmtime(f),
                        'path': f,
                        'type': 'file'
                    }

                # Extract (AP, client) pair from pineapd filename format:
                # timestamp_APMAC_CLIENTMAC_handshake.22000
                ap_mac, client_mac = self._extract_ap_client_pair(basename)
                if ap_mac and client_mac:
                    self._captured_clients[ap_mac].add(client_mac)

                # Also try to extract MAC from .22000 file content
                if f.endswith('.22000'):
                    content_mac = self._extract_mac_from_22000(f)
                    if content_mac and content_mac not in self._captured:
                        self._captured[content_mac] = {
                            'timestamp': os.path.getmtime(f),
                            'path': f,
                            'type': 'file'
                        }

        if self._captured:
            total_clients = sum(len(v) for v in self._captured_clients.values())
            log.info("loaded %d existing handshakes (%d unique AP-client pairs) from %s",
                     len(self._captured), total_clients, self.handshake_dir)

    @staticmethod
    def _extract_mac(filename):
        """Extract MAC address from handshake filename."""
        # Try colon-separated MAC
        match = re.search(r'([0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}', filename)
        if match:
            return match.group(0).lower().replace('-', ':')

        # Try raw hex (pineapd style: AABBCCDDEEFF)
        match = re.search(r'([0-9a-fA-F]{12})', filename)
        if match:
            raw = match.group(0).lower()
            return ':'.join(raw[i:i+2] for i in range(0, 12, 2))

        return None

    @staticmethod
    def _extract_ap_client_pair(filename):
        """Extract (AP MAC, client MAC) from pineapd handshake filename.

        Filename format: timestamp_APMAC_CLIENTMAC_handshake.22000
        e.g.: 1772260468_142103B04721_84F3EBEE271E_handshake.22000
        """
        parts = filename.split('_')
        if len(parts) >= 3:
            raw_ap = parts[1]
            raw_client = parts[2]
            if len(raw_ap) == 12 and len(raw_client) == 12:
                try:
                    # Validate hex
                    int(raw_ap, 16)
                    int(raw_client, 16)
                    ap = ':'.join(raw_ap[i:i+2] for i in range(0, 12, 2)).lower()
                    client = ':'.join(raw_client[i:i+2] for i in range(0, 12, 2)).lower()
                    return ap, client
                except ValueError:
                    pass
        return None, None

    def get_new_clients(self, ap_mac, current_clients):
        """Return client MACs we haven't captured a handshake from yet.

        Args:
            ap_mac: AP MAC address
            current_clients: list of client dicts from AP data

        Returns:
            list of client MAC strings not yet in our capture set
        """
        ap_mac = ap_mac.lower()
        captured = self._captured_clients.get(ap_mac, set())
        new = []
        for client in current_clients:
            client_mac = client.get('mac', '').lower()
            if client_mac and client_mac not in captured:
                new.append(client_mac)
        return new

    @staticmethod
    def _extract_mac_from_22000(filepath):
        """Extract AP MAC from .22000 hashcat file content."""
        try:
            with open(filepath, 'r') as f:
                for line in f:
                    if line.startswith('WPA*'):
                        parts = line.split('*')
                        if len(parts) >= 4:
                            raw_mac = parts[3].lower()
                            if len(raw_mac) == 12:
                                return ':'.join(raw_mac[i:i+2] for i in range(0, 12, 2))
        except Exception:
            pass
        return None

    def has_handshake(self, mac):
        return mac.lower() in self._captured

    def has_pmkid(self, mac):
        return mac.lower() in self._pmkids

    def record_handshake(self, mac, handshake_type='full', client_mac=None):
        mac = mac.lower()
        self._captured[mac] = {
            'timestamp': time.time(),
            'type': handshake_type
        }
        if handshake_type == 'pmkid':
            self._pmkids.add(mac)
        if client_mac:
            self._captured_clients[mac].add(client_mac.lower())

    def get_session_interactions(self, mac):
        return self._interactions.get(mac.lower(), 0)

    def get_epoch_interactions(self, mac):
        return self._epoch_interactions.get(mac.lower(), 0)

    def record_interaction(self, mac):
        mac = mac.lower()
        self._interactions[mac] += 1
        self._epoch_interactions[mac] += 1

    def new_epoch(self):
        """Reset per-epoch counters."""
        self._epoch_interactions.clear()

    @property
    def captured_count(self):
        return len(self._captured)

    @property
    def captured_macs(self):
        return set(self._captured.keys())


class TacticalEngine:
    """Scores and prioritizes attack targets.

    Supports three operational modes:
    - active: Full intelligence-driven targeting (default)
    - passive: No attacks; scores APs by listening value (client activity)
    - assist: Maximum aggression; scores by disruption potential (client count)
    """

    def __init__(self, context, max_interactions_per_epoch=5, max_targets_per_epoch=20,
                 mode=MODE_ACTIVE):
        self.context = context
        self.max_interactions_per_epoch = max_interactions_per_epoch
        self.max_targets_per_epoch = max_targets_per_epoch
        if mode not in VALID_MODES:
            raise ValueError("Invalid mode '%s'. Must be one of: %s" % (mode, VALID_MODES))
        self.mode = mode

    def score_target(self, ap):
        """Compute priority score for a target.

        Returns:
            float score. Higher = higher priority. Negative = skip.
        """
        if self.mode == MODE_PASSIVE:
            return self._score_passive(ap)
        elif self.mode == MODE_ASSIST:
            return self._score_assist(ap)
        return self._score_active(ap)

    def _score_active(self, ap):
        """Active mode scoring: optimize for handshake capture probability."""
        mac = ap.get('mac', '').lower()
        score = 0.0

        # Open networks have no handshake to capture
        encryption = ap.get('encryption', '').upper()
        if encryption in ('', 'OPEN'):
            return -500.0

        clients = ap.get('clients', [])

        # Already captured? Check for new clients worth re-attacking.
        # More client handshakes = more crack attempts = higher PSK recovery odds.
        if self.context.has_handshake(mac):
            new_clients = self.context.get_new_clients(mac, clients)
            if not new_clients:
                return -1000.0  # All clients already captured, skip entirely
            # Reduced score for re-attack — lower priority than uncaptured APs
            # but still worth doing if new clients are present
            score = min(len(new_clients) * 2.0, 8.0)
            # Still apply diminishing returns
            session_attacks = self.context.get_session_interactions(mac)
            score -= session_attacks * 1.5
            epoch_attacks = self.context.get_epoch_interactions(mac)
            if epoch_attacks >= self.max_interactions_per_epoch:
                return -100.0
            return score

        # Encryption type scoring
        if 'WPA3' in encryption or 'SAE' in encryption:
            score += 3.0
        elif 'WPA2' in encryption or 'WPA' in encryption:
            score += 10.0
        elif 'WEP' in encryption:
            score += 1.0

        # Client activity scoring
        num_clients = len(clients)
        score += min(num_clients * 3.0, 15.0)

        # Active clients (seen recently) are worth more
        now = time.time()
        active_clients = sum(
            1 for c in clients
            if c.get('last_seen', c.get('last_seen_at', 0)) > now - 120
        )
        score += active_clients * 2.0

        # Signal strength scoring
        rssi = ap.get('rssi', -100)
        if rssi > -50:
            score += 5.0
        elif rssi > -65:
            score += 3.0
        elif rssi > -75:
            score += 1.5
        elif rssi > -85:
            score += 0.5

        # Freshness scoring
        last_seen = ap.get('last_seen', ap.get('last_seen_at', 0))
        if last_seen and last_seen > now - 60:
            score += 3.0
        elif last_seen and last_seen > now - 300:
            score += 1.0

        # Diminishing returns
        session_attacks = self.context.get_session_interactions(mac)
        score -= session_attacks * 1.0

        epoch_attacks = self.context.get_epoch_interactions(mac)
        if epoch_attacks >= self.max_interactions_per_epoch:
            return -100.0

        return score

    def _score_passive(self, ap):
        """Passive mode scoring: optimize for natural handshake listening."""
        encryption = ap.get('encryption', '').upper()
        if encryption in ('', 'OPEN'):
            return -500.0

        score = 0.0
        mac = ap.get('mac', '').lower()
        if self.context.has_handshake(mac):
            score -= 5.0

        clients = ap.get('clients', [])
        num_clients = len(clients)
        score += num_clients * 5.0

        now = time.time()
        active_clients = sum(
            1 for c in clients
            if c.get('last_seen', c.get('last_seen_at', 0)) > now - 120
        )
        score += active_clients * 4.0

        rssi = ap.get('rssi', -100)
        if rssi > -50:
            score += 3.0
        elif rssi > -65:
            score += 2.0
        elif rssi > -75:
            score += 1.0

        return score

    def _score_assist(self, ap):
        """Assist mode scoring: optimize for client disruption/flush rate."""
        encryption = ap.get('encryption', '').upper()
        if encryption in ('', 'OPEN'):
            return -500.0

        score = 0.0

        clients = ap.get('clients', [])
        num_clients = len(clients)
        score += num_clients * 8.0

        now = time.time()
        active_clients = sum(
            1 for c in clients
            if c.get('last_seen', c.get('last_seen_at', 0)) > now - 120
        )
        score += active_clients * 5.0

        rssi = ap.get('rssi', -100)
        if rssi > -50:
            score += 4.0
        elif rssi > -65:
            score += 3.0
        elif rssi > -75:
            score += 1.5
        elif rssi > -85:
            score += 0.5

        score += 1.0  # Base score so no-client APs still rank > 0

        return score

    def select_attack(self, ap):
        """Choose the best attack type for a given target.

        Mode behavior:
        - active: intelligent selection (PMKID awareness, client-based routing)
        - passive: always SKIP (zero transmissions)
        - assist: most aggressive option available
        """
        if self.mode == MODE_PASSIVE:
            return ATTACK_SKIP

        if self.mode == MODE_ASSIST:
            return self._select_attack_assist(ap)

        return self._select_attack_active(ap)

    def _select_attack_active(self, ap):
        """Active mode: intelligent attack type selection."""
        mac = ap.get('mac', '').lower()
        clients = ap.get('clients', [])

        # Already have PMKID? Just deauth for 4-way handshake
        if self.context.has_pmkid(mac):
            if clients:
                return ATTACK_DEAUTH_ONLY
            return ATTACK_SKIP  # Have PMKID, no clients to deauth

        # No clients? Can only try association for PMKID
        if not clients:
            return ATTACK_ASSOC_ONLY

        # Has clients: associate (for PMKID) then deauth (for 4-way)
        return ATTACK_ASSOC_DEAUTH

    def _select_attack_assist(self, ap):
        """Assist mode: maximum aggression for client flushing."""
        clients = ap.get('clients', [])

        if not clients:
            # No clients to flush, but association may trigger responses
            return ATTACK_ASSOC_ONLY

        # Broadcast deauth for maximum disruption
        return ATTACK_BROADCAST_DEAUTH

    def plan_epoch(self, all_aps):
        """Create a prioritized plan for this epoch.

        Returns:
        - active: attack plan (scored, filtered, capped)
        - passive: empty list (no attacks)
        - assist: aggressive plan (no skip logic, all targets)
        """
        self.context.new_epoch()

        if self.mode == MODE_PASSIVE:
            return []

        scored = []
        for ap in all_aps:
            score = self.score_target(ap)
            if score > 0:
                attack = self.select_attack(ap)
                if attack != ATTACK_SKIP:
                    scored.append((ap, attack, score))

        scored.sort(key=lambda x: x[2], reverse=True)
        return scored[:self.max_targets_per_epoch]


class RewardV2:
    """Improved reward function for NextGen intelligence.

    Properties:
    - No emotional terms (no circular dependency)
    - Temporal locality (only uses current epoch data)
    - Not gameable (all terms measure actual WiFi performance)
    - Handles sparsity (efficiency and exploration provide gradient even without handshakes)
    """

    def __call__(self, epoch_data, context=None):
        duration = max(epoch_data.get('duration_secs', 1.0), 1.0)

        # Core metric: handshake capture rate
        new_unique = epoch_data.get('new_unique_handshakes', 0)
        repeat = epoch_data.get('repeat_handshakes', 0)
        capture_rate = (new_unique * 1.0 + repeat * 0.1) / (duration / 60.0)

        # Efficiency: fraction of attacked targets that were uncaptured
        total_attacked = max(epoch_data.get('targets_attacked', 1), 1)
        uncaptured_attacked = epoch_data.get('uncaptured_targets_attacked', total_attacked)
        efficiency = uncaptured_attacked / total_attacked

        # Exploration: finding new APs is valuable
        new_aps = epoch_data.get('new_aps_discovered', 0)
        exploration = min(new_aps * 0.1, 0.3)

        # Coverage: reward for scanning productive channels
        channels_with_activity = epoch_data.get('channels_with_activity', 0)
        total_channels_scanned = max(epoch_data.get('channels_scanned', 1), 1)
        coverage = channels_with_activity / total_channels_scanned

        reward = capture_rate + 0.3 * efficiency + 0.1 * exploration + 0.1 * coverage
        return reward
