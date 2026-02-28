"""
NextGen Intelligence Layer for Pagergotchi

Orchestrates the three intelligence components:
1. ChannelBandit -- Thompson Sampling for channel selection
2. TacticalEngine -- Target scoring, skip logic, attack routing
3. BayesianOptimizer -- GP-based timing parameter optimization

Plus the PineapdAdapter that translates decisions to pineapd commands.

This module is the single integration point. Agent.py creates a
NextGenBrain and calls its methods. If NextGen is disabled in config,
NextGenBrain is never instantiated and stock behavior runs.
"""

import json
import os
import time
import logging

from pwnagotchi_port.nextgen.channel_bandit import ChannelBandit, MODE_ACTIVE, MODE_PASSIVE, MODE_ASSIST
from pwnagotchi_port.nextgen.tactical_engine import (
    TacticalEngine, CaptureContext, RewardV2,
    ATTACK_SKIP, VALID_MODES,
)
from pwnagotchi_port.nextgen.bayesian_optimizer import BayesianOptimizer, TIMING_PARAMS
from pwnagotchi_port.nextgen.pineapd_adapter import PineapdAdapter

log = logging.getLogger('pagergotchi.nextgen')

# State file path (inside payload data directory)
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PAYLOAD_DIR = os.path.abspath(os.path.join(_THIS_DIR, '..', '..'))
_DATA_DIR = os.path.join(_PAYLOAD_DIR, 'data')
STATE_FILE = os.path.join(_DATA_DIR, 'nextgen_state.json')


class NextGenBrain:
    """Orchestrates all NextGen intelligence components.

    Created by Agent.__init__ when NextGen is enabled in config.
    Provides clean API for the main loop and agent to call.
    """

    def __init__(self, config, agent):
        """Initialize all intelligence components.

        Args:
            config: full Pagergotchi config dict
            agent: the Agent instance (for pineapd adapter)
        """
        self._config = config
        self._agent = agent

        # Read NextGen config
        ng_cfg = config.get('nextgen', {})
        self._mode = ng_cfg.get('mode', 'active').lower().strip()
        if self._mode not in VALID_MODES:
            log.warning("invalid mode '%s', falling back to 'active'", self._mode)
            self._mode = MODE_ACTIVE

        # Log mode prominently
        mode_labels = {
            MODE_ACTIVE: 'ACTIVE -- full offensive + intelligence',
            MODE_PASSIVE: 'PASSIVE -- monitor only, ZERO transmissions',
            MODE_ASSIST: 'ASSIST -- maximum aggression, flushing for external capture',
        }
        log.info("=" * 60)
        log.info("[NextGen] MODE: %s", mode_labels.get(self._mode, self._mode))
        log.info("=" * 60)

        # Get available channels — filter to bands pineapd is actually scanning
        channels = []
        if hasattr(agent, '_supported_channels') and agent._supported_channels:
            channels = list(agent._supported_channels)
        if not channels:
            # Fallback: 2.4 GHz only
            channels = list(range(1, 12))

        # Deduplicate channels — the device reports each channel twice
        # (once per radio), which inflates the bandit's arm count
        pre_dedup = len(channels)
        channels = sorted(set(channels))
        if len(channels) < pre_dedup:
            log.info("Deduplicated %d channels to %d unique",
                     pre_dedup, len(channels))

        # Remove 6 GHz channels — pineapd doesn't scan them unless explicitly
        # configured with --band 6, and they waste bandit exploration epochs
        pre_filter = len(channels)
        channels = [ch for ch in channels if ch <= 177]
        if len(channels) < pre_filter:
            log.info("Filtered %d 6 GHz channels (not scanned by pineapd), %d remaining",
                     pre_filter - len(channels), len(channels))

        # Initialize channel bandit
        bandit_window = ng_cfg.get('bandit_window', 30)
        self._bandit = ChannelBandit(
            channels=channels,
            window_size=bandit_window,
            mode=self._mode,
        )

        # Initialize capture context
        handshake_dir = config.get('bettercap', {}).get('handshakes', '/root/loot/handshakes')
        # Also check the actual pineapd handshake directory
        pineap_hs_dir = getattr(agent, '_pineap_handshakes_dir', '/root/loot/handshakes')
        self._context = CaptureContext(handshake_dir=pineap_hs_dir)

        # Initialize tactical engine
        max_interactions = config.get('personality', {}).get('max_interactions', 3)
        max_targets = ng_cfg.get('max_targets_per_epoch', 20)
        self._tactical = TacticalEngine(
            context=self._context,
            max_interactions_per_epoch=max_interactions,
            max_targets_per_epoch=max_targets,
            mode=self._mode,
        )

        # Initialize reward function
        self._reward_fn = RewardV2()

        # Initialize Bayesian optimizer (disabled in passive mode)
        self._optimizer = None
        self._current_timing = None
        if ng_cfg.get('optimize_timing', True) and self._mode != MODE_PASSIVE:
            n_initial = ng_cfg.get('bo_initial_epochs', 10)
            self._optimizer = BayesianOptimizer(
                parameters=TIMING_PARAMS,
                gp_noise=0.1,
                gp_length_scale=0.5,
                n_initial=n_initial,
            )
            self._current_timing = self._optimizer.suggest()
            self._apply_timing()

        # Initialize pineapd adapter
        self._adapter = PineapdAdapter(agent)

        # Epoch tracking for reward computation
        self._epoch_start = time.time()
        self._epoch_new_handshakes = 0
        self._epoch_repeat_handshakes = 0
        self._epoch_targets_attacked = 0
        self._epoch_uncaptured_attacked = 0
        self._epoch_channels_scanned = 0
        self._epoch_channels_with_activity = 0
        self._epoch_new_aps = 0
        self._known_ap_macs = set()

        # Try to restore previous state
        self._load_state()

        log.info("[NextGen] initialized: mode=%s, %d channels, %d existing handshakes",
                 self._mode, len(channels), self._context.captured_count)

    @property
    def mode(self):
        return self._mode

    @property
    def mode_short(self):
        """Short mode label for display."""
        return {'active': 'ACT', 'passive': 'PAS', 'assist': 'AST'}.get(self._mode, '???')

    @property
    def context(self):
        return self._context

    @property
    def bandit(self):
        return self._bandit

    def select_channels(self, k=5):
        """Select channels for the next scan period using the bandit.

        Args:
            k: number of channels to select

        Returns:
            list of channel numbers
        """
        # Assist mode: scan more channels for broader coverage
        if self._mode == MODE_ASSIST:
            k = max(k, len(self._bandit.channels) // 2)

        channels = self._bandit.select_channels(k=k)
        self._epoch_channels_scanned += len(channels)
        return channels

    def plan_attacks(self, access_points):
        """Create a prioritized attack plan for visible APs.

        Args:
            access_points: list of AP dicts from agent

        Returns:
            list of (ap, attack_type, score) tuples, sorted by score descending
        """
        # Track new APs
        for ap in access_points:
            mac = ap.get('mac', '').lower()
            if mac and mac not in self._known_ap_macs:
                self._known_ap_macs.add(mac)
                self._epoch_new_aps += 1

        # Track per-channel client activity and feed to bandit
        channel_clients = {}
        for ap in access_points:
            ch = ap.get('channel', 0)
            if ch > 0:
                channel_clients[ch] = channel_clients.get(ch, 0) + len(ap.get('clients', []))
        for ch, count in channel_clients.items():
            self._bandit.record_client_activity(ch, count)
            # Boost bandit for channels with observed client activity.
            # This solves the cold-start problem: recon already knows which
            # channels have live devices, so the bandit shouldn't start from zero.
            if count > 0:
                boost_weight = min(count * 0.1, 0.5)
                self._bandit.boost(ch, boost_weight)

        # Get tactical plan
        plan = self._tactical.plan_epoch(access_points)

        # Track stats
        self._epoch_targets_attacked = len(plan)
        self._epoch_uncaptured_attacked = sum(
            1 for ap, _, _ in plan
            if not self._context.has_handshake(ap.get('mac', ''))
        )

        return plan

    def execute_attack(self, ap, attack_type):
        """Execute a single attack via the pineapd adapter.

        Args:
            ap: AP dict
            attack_type: attack type from tactical engine

        Returns:
            bool: whether attack was executed
        """
        if attack_type == ATTACK_SKIP:
            return False

        # Record interaction
        mac = ap.get('mac', '').lower()
        if mac:
            self._context.record_interaction(mac)

        return self._adapter.execute_attack(ap, attack_type)

    def on_handshake(self, ap_mac, channel=0):
        """Called when a handshake is captured.

        Args:
            ap_mac: AP MAC address
            channel: channel the handshake was captured on (0 if unknown)
        """
        ap_mac = ap_mac.lower()

        is_new = not self._context.has_handshake(ap_mac)
        self._context.record_handshake(ap_mac)

        if is_new:
            self._epoch_new_handshakes += 1
            log.info("[NextGen] NEW handshake: %s (total unique: %d)",
                     ap_mac, self._context.captured_count)
        else:
            self._epoch_repeat_handshakes += 1

        # Update bandit with positive reward for this channel
        if channel > 0:
            self._bandit.update(channel, 1.0)

    def on_channel_scanned(self, channel, had_activity=False):
        """Called after scanning a channel.

        Args:
            channel: channel number
            had_activity: whether any handshake was captured on this channel
        """
        if had_activity:
            self._epoch_channels_with_activity += 1

        # Record failure (no handshake) for channels where we didn't capture
        if not had_activity:
            self._bandit.update(channel, 0.0)

    def on_epoch(self, epoch_num, epoch_data):
        """Called at end of each epoch. Updates all intelligence components.

        Args:
            epoch_num: epoch number
            epoch_data: epoch data dict from Epoch.data()
        """
        now = time.time()

        # Compute reward for Bayesian optimizer (not in passive mode)
        if self._optimizer is not None and self._current_timing is not None:
            epoch_metrics = {
                'duration_secs': now - self._epoch_start,
                'new_unique_handshakes': self._epoch_new_handshakes,
                'repeat_handshakes': self._epoch_repeat_handshakes,
                'targets_attacked': self._epoch_targets_attacked,
                'uncaptured_targets_attacked': self._epoch_uncaptured_attacked,
                'channels_scanned': self._epoch_channels_scanned,
                'channels_with_activity': self._epoch_channels_with_activity,
                'new_aps_discovered': self._epoch_new_aps,
            }
            reward = self._reward_fn(epoch_metrics)
            self._optimizer.observe(self._current_timing, reward)

            # Get next timing parameters
            self._current_timing = self._optimizer.suggest()
            self._apply_timing()

        # Log epoch summary
        if self._mode == MODE_ASSIST:
            log.info("[NextGen][ASSIST] epoch %d: %d targets attacked, %d new hs",
                     epoch_num, self._epoch_targets_attacked, self._epoch_new_handshakes)
        elif self._mode == MODE_PASSIVE:
            log.info("[NextGen][PASSIVE] epoch %d: %d natural hs",
                     epoch_num, self._epoch_new_handshakes)
        else:
            log.info("[NextGen] epoch %d: %d new hs, %d targets, %d skipped, bandit=%s",
                     epoch_num, self._epoch_new_handshakes,
                     self._epoch_targets_attacked,
                     self._epoch_uncaptured_attacked,
                     repr(self._bandit))

        # Reset epoch counters
        self._epoch_start = now
        self._epoch_new_handshakes = 0
        self._epoch_repeat_handshakes = 0
        self._epoch_targets_attacked = 0
        self._epoch_uncaptured_attacked = 0
        self._epoch_channels_scanned = 0
        self._epoch_channels_with_activity = 0
        self._epoch_new_aps = 0

        # Periodically save state
        if epoch_num % 10 == 0:
            self._save_state()

    def has_handshake(self, mac):
        """Check if we already have a handshake for this MAC.

        Used by agent._should_interact() to integrate with stock skip logic.
        """
        return self._context.has_handshake(mac)

    def _apply_timing(self):
        """Apply Bayesian optimizer's suggested timing to config."""
        if self._current_timing is None:
            return

        personality = self._config.get('personality', {})
        for param, value in self._current_timing.items():
            if param in personality:
                if param in ('recon_time', 'hop_recon_time', 'min_recon_time'):
                    personality[param] = round(value)
                elif param in ('ap_ttl', 'sta_ttl'):
                    personality[param] = round(value)
                else:
                    personality[param] = value

    def _save_state(self):
        """Persist intelligence state to disk."""
        try:
            state = {
                'mode': self._mode,
                'bandit': self._bandit.get_state(),
                'optimizer': self._optimizer.get_state() if self._optimizer else None,
                'captured_macs': list(self._context.captured_macs),
                'known_ap_macs': list(self._known_ap_macs),
            }
            os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
            with open(STATE_FILE, 'w') as f:
                json.dump(state, f)
            log.debug("[NextGen] state saved to %s", STATE_FILE)
        except Exception as e:
            log.error("[NextGen] failed to save state: %s", e)

    def _load_state(self):
        """Restore intelligence state from disk."""
        if not os.path.exists(STATE_FILE):
            return

        try:
            with open(STATE_FILE, 'r') as f:
                state = json.load(f)

            if state.get('bandit'):
                self._bandit.load_state(state['bandit'])
                log.info("[NextGen] restored bandit state (%d epochs)",
                         self._bandit._total_epochs)

            if state.get('optimizer') and self._optimizer:
                self._optimizer.load_state(state['optimizer'])
                log.info("[NextGen] restored optimizer state (%d evaluations)",
                         len(self._optimizer.X_history))

            self._known_ap_macs = set(state.get('known_ap_macs', []))

        except Exception as e:
            log.error("[NextGen] failed to load state: %s", e)

    def get_summary(self):
        """Return a summary dict for logging/display."""
        summary = {
            'mode': self._mode,
            'channels': len(self._bandit.channels),
            'captured': self._context.captured_count,
            'known_aps': len(self._known_ap_macs),
            'bandit_epochs': self._bandit._total_epochs,
            'band_stats': self._bandit.get_band_stats(),
        }
        if self._optimizer:
            summary['optimizer'] = self._optimizer.summary()
        return summary
