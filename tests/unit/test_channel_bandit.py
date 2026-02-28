"""
Unit tests for Channel Bandit (tri-band Thompson Sampling)
"""
import sys
import os
import random

# Add the pagergotchi source to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'pagergotchi-nextgen',
                                'payloads', 'user', 'reconnaissance', 'pagergotchi'))

from pwnagotchi_port.nextgen.channel_bandit import (
    ChannelBandit, channel_to_band, raw_6g_to_offset,
    CHANNELS_2G, CHANNELS_5G, CHANNELS_6G, _6G_OFFSET,
    MODE_ACTIVE, MODE_PASSIVE, MODE_ASSIST,
)


def test_channel_to_band():
    """Test band identification from channel number."""
    assert channel_to_band(1) == '2G'
    assert channel_to_band(6) == '2G'
    assert channel_to_band(14) == '2G'
    assert channel_to_band(36) == '5G'
    assert channel_to_band(149) == '5G'
    assert channel_to_band(177) == '5G'
    # 6 GHz channels use offset form (raw + 190)
    assert channel_to_band(raw_6g_to_offset(1)) == '6G'    # 191
    assert channel_to_band(raw_6g_to_offset(5)) == '6G'    # 195
    assert channel_to_band(raw_6g_to_offset(93)) == '6G'   # 283
    # Verify CHANNELS_6G entries are all classified as 6G
    for ch in CHANNELS_6G:
        assert channel_to_band(ch) == '6G', f"Channel {ch} should be 6G"
    print("PASS: channel_to_band")


def test_init_2g_only():
    """Test initialization with 2.4 GHz channels only."""
    bandit = ChannelBandit(channels=list(range(1, 12)), mode=MODE_ACTIVE)
    assert len(bandit.channels) == 11
    assert bandit._total_epochs == 0
    assert bandit.mode == MODE_ACTIVE
    print("PASS: init_2g_only")


def test_init_triband():
    """Test initialization with full tri-band channel set."""
    all_channels = CHANNELS_2G + CHANNELS_5G + CHANNELS_6G
    bandit = ChannelBandit(channels=all_channels, mode=MODE_ACTIVE)
    assert len(bandit.channels) == len(all_channels)
    bands = bandit.get_band_stats()
    assert '2G' in bands
    assert '5G' in bands
    assert '6G' in bands
    assert bands['2G']['channels'] == len(CHANNELS_2G)
    assert bands['5G']['channels'] == len(CHANNELS_5G)
    assert bands['6G']['channels'] == len(CHANNELS_6G)
    print("PASS: init_triband")


def test_select_channels_basic():
    """Test basic channel selection."""
    bandit = ChannelBandit(channels=list(range(1, 12)), mode=MODE_ACTIVE)
    selected = bandit.select_channels(k=5)
    assert len(selected) == 5
    assert all(ch in bandit.channels for ch in selected)
    print("PASS: select_channels_basic")


def test_select_channels_k_exceeds():
    """Test channel selection when k >= total channels."""
    channels = [1, 6, 11]
    bandit = ChannelBandit(channels=channels, mode=MODE_ACTIVE)
    selected = bandit.select_channels(k=5)
    assert len(selected) == 3
    assert set(selected) == set(channels)
    print("PASS: select_channels_k_exceeds")


def test_update_and_convergence():
    """Test that bandit converges to rewarding channels."""
    random.seed(42)
    channels = list(range(1, 12))
    bandit = ChannelBandit(channels=channels, window_size=20, mode=MODE_ACTIVE)

    # Simulate: channel 6 always rewards, others don't
    for _ in range(50):
        selected = bandit.select_channels(k=3)
        for ch in selected:
            reward = 1.0 if ch == 6 else 0.0
            bandit.update(ch, reward)

    # After 50 epochs, channel 6 should have the highest success rate
    stats = bandit.get_stats()
    ch6_rate = stats[6]['success_rate']
    other_rates = [stats[ch]['success_rate'] for ch in channels if ch != 6 and stats[ch]['scans'] > 0]
    if other_rates:
        assert ch6_rate > max(other_rates), f"Channel 6 rate {ch6_rate} not highest"
    print("PASS: update_and_convergence")


def test_passive_mode():
    """Test passive mode biases toward client activity."""
    random.seed(42)
    channels = list(range(1, 12))
    bandit = ChannelBandit(channels=channels, mode=MODE_PASSIVE)

    # Record high client activity on channel 1
    for _ in range(10):
        bandit.record_client_activity(1, 20)
        bandit.record_client_activity(6, 2)
        bandit.record_client_activity(11, 0)

    # Channel 1 should be selected more often
    selections = {ch: 0 for ch in channels}
    for _ in range(100):
        selected = bandit.select_channels(k=3)
        for ch in selected:
            selections[ch] += 1

    assert selections[1] > selections[11], "Passive mode should favor high-activity channels"
    print("PASS: passive_mode")


def test_assist_mode():
    """Test assist mode maximizes coverage breadth."""
    random.seed(42)
    channels = list(range(1, 12))
    bandit = ChannelBandit(channels=channels, mode=MODE_ASSIST)

    # Select many times and check diversity
    all_selected = set()
    for _ in range(20):
        selected = bandit.select_channels(k=3)
        all_selected.update(selected)
        for ch in selected:
            bandit.update(ch, 0.0)

    # Assist mode should explore broadly
    assert len(all_selected) >= 8, f"Assist should explore broadly, got {len(all_selected)}"
    print("PASS: assist_mode")


def test_state_persistence():
    """Test state serialization/deserialization."""
    random.seed(42)
    channels = list(range(1, 12))
    bandit = ChannelBandit(channels=channels, mode=MODE_ACTIVE)

    # Generate some history
    for _ in range(10):
        selected = bandit.select_channels(k=3)
        for ch in selected:
            bandit.update(ch, random.choice([0.0, 1.0]))

    # Serialize
    state = bandit.get_state()

    # Create new bandit and restore
    bandit2 = ChannelBandit(channels=channels, mode=MODE_ACTIVE)
    bandit2.load_state(state)

    assert bandit2._total_epochs == bandit._total_epochs
    assert dict(bandit2._total_scans) == dict(bandit._total_scans)
    print("PASS: state_persistence")


def test_triband_selection():
    """Test that tri-band bandit can select channels from all bands."""
    random.seed(42)
    all_channels = CHANNELS_2G + CHANNELS_5G[:10] + CHANNELS_6G[:5]
    bandit = ChannelBandit(channels=all_channels, mode=MODE_ACTIVE)

    # Run several selections
    bands_seen = set()
    for _ in range(20):
        selected = bandit.select_channels(k=5)
        for ch in selected:
            bands_seen.add(channel_to_band(ch))
            bandit.update(ch, 0.0)

    assert '2G' in bands_seen, "Should select from 2G band"
    assert '5G' in bands_seen, "Should select from 5G band"
    print("PASS: triband_selection")


def test_band_diversity():
    """Test band diversity enforcement."""
    random.seed(42)
    all_channels = [1, 6, 11, 36, 44, 149]  # Mix of 2G and 5G
    bandit = ChannelBandit(channels=all_channels, mode=MODE_ACTIVE)

    # With k=3 and band diversity, should get at least one from each band
    has_mixed = False
    for _ in range(10):
        selected = bandit.select_channels(k=3)
        bands = set(channel_to_band(ch) for ch in selected)
        if len(bands) >= 2:
            has_mixed = True
            break
        for ch in selected:
            bandit.update(ch, 0.0)

    assert has_mixed, "Band diversity should produce mixed-band selections"
    print("PASS: band_diversity")


def test_band_stats():
    """Test per-band statistics aggregation."""
    random.seed(42)
    channels = [1, 6, 11, 36, 44]
    bandit = ChannelBandit(channels=channels, mode=MODE_ACTIVE)

    # Update with known rewards
    bandit.update(1, 1.0)
    bandit.update(6, 0.0)
    bandit.update(36, 1.0)

    stats = bandit.get_band_stats()
    assert stats['2G']['total_scans'] == 2
    assert stats['5G']['total_scans'] == 1
    print("PASS: band_stats")


def test_memory_usage():
    """Test memory footprint with large channel set."""
    import sys
    all_channels = CHANNELS_2G + CHANNELS_5G + CHANNELS_6G
    bandit = ChannelBandit(channels=all_channels, window_size=30, mode=MODE_ACTIVE)

    # Simulate 100 epochs
    random.seed(42)
    for _ in range(100):
        selected = bandit.select_channels(k=5)
        for ch in selected:
            bandit.update(ch, random.choice([0.0, 0.0, 0.0, 1.0]))

    # Get state size as proxy for memory
    state = bandit.get_state()
    import json
    state_json = json.dumps(state)
    size_kb = len(state_json) / 1024
    print(f"  State size after 100 epochs with {len(all_channels)} channels: {size_kb:.1f} KB")
    assert size_kb < 100, f"State too large: {size_kb:.1f} KB"
    print("PASS: memory_usage")


if __name__ == '__main__':
    test_channel_to_band()
    test_init_2g_only()
    test_init_triband()
    test_select_channels_basic()
    test_select_channels_k_exceeds()
    test_update_and_convergence()
    test_passive_mode()
    test_assist_mode()
    test_state_persistence()
    test_triband_selection()
    test_band_diversity()
    test_band_stats()
    test_memory_usage()
    print("\nAll channel bandit tests passed.")
