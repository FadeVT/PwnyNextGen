"""
Channel Selection via Thompson Sampling Multi-Armed Bandit

Ported from Phase 3A pwnagotchi NextGen intelligence layer.
Extended for Hak5 WiFi Pineapple Pager tri-band support (2.4/5/6 GHz).

Each WiFi channel is an "arm". Pulling an arm = scanning that channel.
Reward = handshakes/activity found on that channel during the scan.

Uses Thompson Sampling with Beta priors and sliding-window statistics
for non-stationary environments (devices moving in/out of range).

Supports three operational modes:
- ACTIVE: Balance dwell time on productive channels with exploration
- PASSIVE: Heavy dwell on channels with most observed client activity
- ASSIST: Rapid channel hopping for maximum coverage breadth
"""

import random
import time
import logging
from collections import defaultdict

log = logging.getLogger('pagergotchi.nextgen.channel_bandit')

# Mode constants
MODE_ACTIVE = 'active'
MODE_PASSIVE = 'passive'
MODE_ASSIST = 'assist'

# Standard channel definitions by band
CHANNELS_2G = list(range(1, 15))  # 1-14
CHANNELS_5G = [
    36, 40, 44, 48, 52, 56, 60, 64,
    100, 104, 108, 112, 116, 120, 124, 128,
    132, 136, 140, 144, 149, 153, 157, 161,
    165, 169, 173, 177
]
# 6 GHz channels use UNII-5 through UNII-8 numbering.
# WiFi 6E channel numbers (1, 5, 9, ...) overlap with 2.4 GHz numbers.
# To avoid ambiguity, we use frequency-offset identifiers: actual_channel + 190.
# This maps 6 GHz ch 1 -> 191, ch 5 -> 195, etc. -- unique integers that
# won't collide with 2.4 GHz (1-14) or 5 GHz (36-177).
# When the bandit receives channels from hardware (utils.iface_channels()),
# 6 GHz channels must be converted to this offset form before being passed in.
_6G_RAW_CHANNELS = [
    1, 5, 9, 13, 17, 21, 25, 29, 33, 37, 41, 45,
    49, 53, 57, 61, 65, 69, 73, 77, 81, 85, 89, 93
]
_6G_OFFSET = 190  # offset to make 6 GHz channel numbers unique
CHANNELS_6G = [ch + _6G_OFFSET for ch in _6G_RAW_CHANNELS]  # 191, 195, 199, ...

# Lookup sets for fast band identification
_CHANNELS_2G_SET = set(CHANNELS_2G)
_CHANNELS_5G_SET = set(CHANNELS_5G)
_CHANNELS_6G_SET = set(CHANNELS_6G)


def channel_to_band(channel):
    """Map channel number to band identifier.

    Uses set membership for unambiguous band classification.
    6 GHz channels must be in offset form (raw_channel + 190).
    """
    if channel in _CHANNELS_6G_SET:
        return '6G'
    elif channel in _CHANNELS_5G_SET:
        return '5G'
    elif channel in _CHANNELS_2G_SET:
        return '2G'
    else:
        # Fallback: use range heuristics for channels not in standard sets
        # (e.g., hardware-reported channels outside standard definitions)
        if channel > 177:
            return '6G'
        elif channel > 14:
            return '5G'
        else:
            return '2G'


def raw_6g_to_offset(raw_channel):
    """Convert a raw 6 GHz channel number (1-233) to offset form.

    Use this when receiving channel numbers from hardware (iw, pineapd)
    that report 6 GHz channels with their native numbering.
    """
    return raw_channel + _6G_OFFSET


def offset_to_raw_6g(offset_channel):
    """Convert an offset 6 GHz channel number back to raw form.

    Use this when sending channel commands to hardware.
    """
    return offset_channel - _6G_OFFSET


class ChannelBandit:
    """Thompson Sampling bandit for WiFi channel selection.

    Each channel maintains a Beta(alpha, beta) posterior:
      alpha = 1 + successes (handshakes/activity found)
      beta  = 1 + failures  (epochs with no handshake on this channel)

    The sliding window ensures old observations age out, adapting
    to non-stationary environments (e.g., user moving to new area).

    Tri-band aware: tracks per-band statistics for logging and analysis.
    The bandit itself treats each channel as an independent arm regardless
    of band -- Thompson Sampling doesn't need to know about frequency.

    Mode behavior:
    - active: standard Thompson Sampling (balance exploitation + exploration)
    - passive: bias toward channels with client activity (dwell for natural captures)
    - assist: maximize coverage breadth (more channels per epoch, less dwell)
    """

    def __init__(self, channels, window_size=30, exploration_bonus=0.1, mode=MODE_ACTIVE):
        """
        Args:
            channels: list of available channel numbers (from iw query or config)
            window_size: sliding window for observation history per channel
            exploration_bonus: minimum probability of exploring any channel
            mode: operational mode ('active', 'passive', 'assist')
        """
        self.channels = list(channels)
        self.window_size = window_size
        self.exploration_bonus = exploration_bonus
        self.mode = mode

        # Per-channel history: list of (timestamp, reward) tuples
        self._history = defaultdict(list)
        # Per-channel scan count (total, not windowed)
        self._total_scans = defaultdict(int)
        # Total epochs across all channels
        self._total_epochs = 0
        # Per-channel client activity observations (for passive mode)
        self._client_activity = defaultdict(list)

        # Band-level channel grouping for stats/logging
        self._bands = {'2G': [], '5G': [], '6G': []}
        for ch in self.channels:
            band = channel_to_band(ch)
            self._bands[band].append(ch)

        log.info("ChannelBandit initialized: %d channels (%d 2G, %d 5G, %d 6G), mode=%s",
                 len(self.channels),
                 len(self._bands['2G']),
                 len(self._bands['5G']),
                 len(self._bands['6G']),
                 self.mode)

    def _get_windowed_stats(self, channel):
        """Get success/failure counts within the sliding window."""
        history = self._history[channel]
        if len(history) > self.window_size:
            history = history[-self.window_size:]
            self._history[channel] = history

        successes = sum(1 for _, r in history if r > 0)
        failures = len(history) - successes
        return successes, failures

    def record_client_activity(self, channel, client_count):
        """Record observed client activity on a channel (used by passive mode).

        Args:
            channel: channel number
            client_count: number of active clients seen on this channel
        """
        history = self._client_activity[channel]
        history.append(client_count)
        if len(history) > self.window_size:
            self._client_activity[channel] = history[-self.window_size:]

    def _get_avg_client_activity(self, channel):
        """Get average client activity for a channel."""
        history = self._client_activity[channel]
        if not history:
            return 0.0
        return sum(history) / len(history)

    def select_channels(self, k=5):
        """Select k channels to scan using Thompson Sampling.

        Mode behavior:
        - active: standard TS (balance exploitation + exploration)
        - passive: bias toward high client activity channels (heavy dwell)
        - assist: maximize coverage breadth (select more diverse channels)

        Returns a list of k channel numbers.
        """
        if k >= len(self.channels):
            return list(self.channels)

        if self.mode == MODE_PASSIVE:
            return self._select_passive(k)
        elif self.mode == MODE_ASSIST:
            return self._select_assist(k)
        return self._select_active(k)

    def _select_active(self, k):
        """Active mode: standard Thompson Sampling."""
        scores = {}
        for ch in self.channels:
            successes, failures = self._get_windowed_stats(ch)
            alpha = 1 + successes
            beta_param = 1 + failures

            # Thompson sample: draw from posterior
            score = random.betavariate(alpha, beta_param)

            # Exploration bonus for unscanned channels
            if self._total_scans[ch] == 0:
                score += self.exploration_bonus

            scores[ch] = score

        # Sort by score, take top k
        ranked = sorted(scores.keys(), key=lambda c: scores[c], reverse=True)
        selected = ranked[:k]

        # Ensure at least one never-scanned channel if available
        unscanned = [c for c in self.channels if self._total_scans[c] == 0]
        if unscanned and not any(c in unscanned for c in selected):
            selected[-1] = random.choice(unscanned)

        # Tri-band diversity: ensure at least one channel from each active band
        # if we have enough selection slots
        if k >= 3:
            selected = self._ensure_band_diversity(selected, scores, k)

        return selected

    def _select_passive(self, k):
        """Passive mode: heavy dwell on channels with most client activity."""
        scores = {}
        for ch in self.channels:
            successes, failures = self._get_windowed_stats(ch)
            alpha = 1 + successes
            beta_param = 1 + failures

            score = random.betavariate(alpha, beta_param)

            # Heavy client activity bias
            activity = self._get_avg_client_activity(ch)
            score += activity * 0.3

            if self._total_scans[ch] == 0:
                score += self.exploration_bonus

            scores[ch] = score

        ranked = sorted(scores.keys(), key=lambda c: scores[c], reverse=True)
        selected = ranked[:k]

        unscanned = [c for c in self.channels if self._total_scans[c] == 0]
        if unscanned and not any(c in unscanned for c in selected):
            selected[-1] = random.choice(unscanned)

        return selected

    def _select_assist(self, k):
        """Assist mode: rapid channel hopping for maximum coverage breadth."""
        scores = {}
        for ch in self.channels:
            successes, failures = self._get_windowed_stats(ch)
            alpha = 1 + successes
            beta_param = 1 + failures

            score = random.betavariate(alpha, beta_param)
            # Add random jitter for diversity
            score += random.random() * 0.3

            # Stronger exploration bonus
            if self._total_scans[ch] == 0:
                score += self.exploration_bonus * 2.0

            scores[ch] = score

        ranked = sorted(scores.keys(), key=lambda c: scores[c], reverse=True)
        selected = ranked[:k]

        unscanned = [c for c in self.channels if self._total_scans[c] == 0]
        if unscanned and not any(c in unscanned for c in selected):
            selected[-1] = random.choice(unscanned)

        # Assist always tries for band diversity
        if k >= 3:
            selected = self._ensure_band_diversity(selected, scores, k)

        return selected

    def _ensure_band_diversity(self, selected, scores, k):
        """Ensure selected channels include representatives from each active band.

        Active bands = bands that have channels with non-zero scan history.
        This prevents the bandit from getting stuck on only 2.4 GHz in early epochs
        when 5/6 GHz haven't been explored yet.
        """
        bands_represented = set()
        for ch in selected:
            bands_represented.add(channel_to_band(ch))

        # Find active bands (have at least one channel in our list)
        active_bands = [b for b, chs in self._bands.items() if chs]

        for band in active_bands:
            if band not in bands_represented and len(selected) > 0:
                # Pick the best-scoring channel from this band
                band_channels = self._bands[band]
                best_ch = max(band_channels, key=lambda c: scores.get(c, 0))
                # Replace the lowest-scoring selected channel
                worst_idx = min(range(len(selected)), key=lambda i: scores.get(selected[i], 0))
                selected[worst_idx] = best_ch
                bands_represented.add(band)

        return selected

    def boost(self, channel, weight=0.3):
        """Add a synthetic positive observation from recon data.

        Unlike update(), this doesn't increment scan counters -- it just
        shifts the Beta posterior to favor this channel based on observed
        client activity during recon. This solves the cold-start problem:
        instead of exploring 73 channels uniformly, the bandit immediately
        learns which channels have live devices.

        Args:
            channel: channel number
            weight: reward weight (0.0-1.0), default 0.3 for recon signal
        """
        if channel in self.channels:
            self._history[channel].append((time.time(), weight))

    def update(self, channel, reward):
        """Record observation for a channel.

        Args:
            channel: channel number
            reward: positive value if handshake/activity found, 0 otherwise
        """
        self._history[channel].append((time.time(), reward))
        self._total_scans[channel] += 1
        self._total_epochs += 1

    def get_stats(self):
        """Return statistics for all channels."""
        stats = {}
        for ch in self.channels:
            successes, failures = self._get_windowed_stats(ch)
            total = successes + failures
            rate = successes / total if total > 0 else 0.0
            stats[ch] = {
                'band': channel_to_band(ch),
                'scans': self._total_scans[ch],
                'successes_windowed': successes,
                'failures_windowed': failures,
                'success_rate': rate,
            }
        return stats

    def get_band_stats(self):
        """Return aggregated statistics per band."""
        band_stats = {}
        for band, channels in self._bands.items():
            if not channels:
                continue
            total_scans = sum(self._total_scans[ch] for ch in channels)
            total_successes = sum(
                self._get_windowed_stats(ch)[0] for ch in channels
            )
            total_failures = sum(
                self._get_windowed_stats(ch)[1] for ch in channels
            )
            total = total_successes + total_failures
            band_stats[band] = {
                'channels': len(channels),
                'total_scans': total_scans,
                'successes': total_successes,
                'failures': total_failures,
                'success_rate': total_successes / total if total > 0 else 0.0,
            }
        return band_stats

    def get_state(self):
        """Serialize state for persistence."""
        return {
            'channels': self.channels,
            'window_size': self.window_size,
            'exploration_bonus': self.exploration_bonus,
            'mode': self.mode,
            'history': {str(ch): [(t, r) for t, r in self._history[ch]]
                        for ch in self._history},
            'total_scans': dict(self._total_scans),
            'total_epochs': self._total_epochs,
            'client_activity': {str(ch): list(counts)
                                for ch, counts in self._client_activity.items()},
        }

    def load_state(self, state):
        """Restore state from persistence."""
        self.window_size = state.get('window_size', self.window_size)
        self.exploration_bonus = state.get('exploration_bonus', self.exploration_bonus)
        for ch_str, hist in state.get('history', {}).items():
            ch = int(ch_str)
            self._history[ch] = [(t, r) for t, r in hist]
        for ch_str, count in state.get('total_scans', {}).items():
            self._total_scans[int(ch_str)] = count
        self._total_epochs = state.get('total_epochs', 0)
        for ch_str, counts in state.get('client_activity', {}).items():
            self._client_activity[int(ch_str)] = list(counts)

    def __repr__(self):
        active = [(ch, self._total_scans[ch]) for ch in self.channels
                  if self._total_scans[ch] > 0]
        active.sort(key=lambda x: x[1], reverse=True)
        top = active[:5]
        bands = self.get_band_stats()
        band_summary = ', '.join(
            f"{b}:{s['total_scans']}" for b, s in bands.items() if s['total_scans'] > 0
        )
        return (f"ChannelBandit({len(self.channels)} ch, {self._total_epochs} epochs, "
                f"bands=[{band_summary}], top={top})")
