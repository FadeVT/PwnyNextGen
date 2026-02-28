"""
PineAP Adapter -- Translation Layer for NextGen Intelligence

NEW CODE: This module translates NextGen's abstract attack decisions
(deauth target X, associate with Y, switch to channel Z) into pineapd
API calls through the existing bettercap.py Client shim.

The adapter does NOT call _pineap directly. It goes through the
agent's run() method, which routes to PineAPBackend, which shells out
to _pineap. This preserves the existing command chain and ensures
all existing logging and error handling still works.

Platform differences handled:
- No CSA support in pineapd --> fallback to broadcast deauth
- Single-channel lock only (no multi-channel subset) --> sequential locking
- Association = focus_bssid() (lock to AP for PMKID capture)
- Client tracking via tcpdump (less reliable than bettercap native)
"""

import time
import logging

from pwnagotchi_port.nextgen.tactical_engine import (
    ATTACK_ASSOC_DEAUTH,
    ATTACK_DEAUTH_ONLY,
    ATTACK_BROADCAST_DEAUTH,
    ATTACK_ASSOC_ONLY,
    ATTACK_SKIP,
)

log = logging.getLogger('pagergotchi.nextgen.pineapd_adapter')


class PineapdAdapter:
    """Translates NextGen attack decisions into pineapd operations.

    Uses the agent's existing run() method to issue commands through
    the bettercap.py Client shim, which translates to _pineap calls.
    """

    def __init__(self, agent):
        """
        Args:
            agent: The Pagergotchi Agent instance (has run(), associate(), deauth(), etc.)
        """
        self._agent = agent

    def set_channel(self, channel):
        """Lock pineapd to a specific channel.

        Uses EXAMINE CHANNEL with 300s timeout (pineapd will auto-resume
        hopping if we don't refresh the lock).

        Args:
            channel: channel number (1-233)
        """
        try:
            self._agent.run('wifi.recon.channel %d' % channel)
            log.debug("channel locked to %d", channel)
        except Exception as e:
            log.error("failed to set channel %d: %s", channel, e)

    def clear_channel_lock(self):
        """Resume pineapd's native channel hopping."""
        try:
            self._agent.run('wifi.recon.channel clear')
            log.debug("channel lock cleared, resuming hopping")
        except Exception as e:
            log.error("failed to clear channel lock: %s", e)

    def execute_attack(self, ap, attack_type):
        """Execute an attack against a target AP.

        Routes the NextGen tactical engine's attack decision to the
        appropriate pineapd operation.

        Args:
            ap: AP dict (must have 'mac', may have 'clients', 'hostname')
            attack_type: one of ATTACK_* constants from tactical_engine

        Returns:
            bool: True if attack was executed (not necessarily successful)
        """
        if attack_type == ATTACK_SKIP:
            return False

        mac = ap.get('mac', '')
        if not mac:
            return False

        if attack_type == ATTACK_ASSOC_DEAUTH:
            return self._assoc_then_deauth(ap)
        elif attack_type == ATTACK_DEAUTH_ONLY:
            return self._deauth_clients(ap)
        elif attack_type == ATTACK_BROADCAST_DEAUTH:
            return self._broadcast_deauth(ap)
        elif attack_type == ATTACK_ASSOC_ONLY:
            return self._associate_only(ap)
        else:
            log.warning("unknown attack type: %s", attack_type)
            return False

    def _associate_only(self, ap):
        """Send association frame for PMKID capture only.

        Maps to: wifi.assoc MAC --> PineAPBackend.focus_bssid() -->
        _pineap EXAMINE BSSID MAC 300

        This locks pineapd to the AP's channel and focuses on it,
        allowing automatic PMKID capture.
        """
        try:
            self._agent.associate(ap)
            return True
        except Exception as e:
            log.error("association failed for %s: %s", ap.get('mac', '?'), e)
            return False

    def _deauth_clients(self, ap):
        """Deauth all known clients from an AP.

        Maps to: wifi.deauth AP_MAC STA_MAC --> PineAPBackend.deauth() -->
        _pineap DEAUTH AP_MAC STA_MAC CHANNEL
        """
        clients = ap.get('clients', [])
        if not clients:
            # No known clients, try broadcast deauth
            return self._broadcast_deauth(ap)

        success = False
        for sta in clients:
            try:
                self._agent.deauth(ap, sta)
                success = True
            except Exception as e:
                log.error("deauth failed for %s from %s: %s",
                         sta.get('mac', '?'), ap.get('mac', '?'), e)

        return success

    def _assoc_then_deauth(self, ap):
        """Associate for PMKID, then deauth clients for 4-way handshake.

        This is the standard combined attack: try to get PMKID via
        association, then deauth clients to force 4-way handshake.
        """
        # First: associate for PMKID
        self._associate_only(ap)

        # Then: deauth all clients
        clients = ap.get('clients', [])
        if clients:
            for sta in clients:
                try:
                    self._agent.deauth(ap, sta)
                except Exception as e:
                    log.error("deauth failed for %s from %s: %s",
                             sta.get('mac', '?'), ap.get('mac', '?'), e)
        else:
            # No individual clients known, try broadcast deauth
            self._broadcast_deauth(ap)

        return True

    def _broadcast_deauth(self, ap):
        """Broadcast deauth to disconnect all clients.

        This is the fallback when:
        1. CSA is requested but pineapd doesn't support it
        2. No individual clients are known
        3. Assist mode wants maximum disruption

        Maps to: wifi.deauth AP_MAC FF:FF:FF:FF:FF:FF -->
        PineAPBackend.deauth(bssid, 'FF:FF:FF:FF:FF:FF') -->
        _pineap DEAUTH AP FF:FF:FF:FF:FF:FF CH
        """
        try:
            self._agent.broadcast_deauth(ap)
            return True
        except Exception as e:
            log.error("broadcast deauth failed for %s: %s", ap.get('mac', '?'), e)
            return False

    def get_access_points(self):
        """Get current list of visible APs from pineapd.

        Routes through agent's existing AP retrieval which handles
        whitelist/blacklist filtering.

        Returns:
            list of AP dicts
        """
        try:
            return self._agent.get_access_points()
        except Exception as e:
            log.error("failed to get access points: %s", e)
            return []

    def get_access_points_on_channel(self, channel):
        """Get APs visible on a specific channel.

        Args:
            channel: channel number

        Returns:
            list of AP dicts on that channel
        """
        all_aps = self.get_access_points()
        return [ap for ap in all_aps if ap.get('channel', 0) == channel]
