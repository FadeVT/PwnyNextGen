"""
Simulation: Stock Pagergotchi vs NextGen Pagergotchi

Simulates a dense WiFi environment with tri-band APs and compares:
1. Stock behavior (attack everything sequentially)
2. NextGen behavior (bandit channels, tactical targeting, optimizer timing)

Measures:
- Epochs to first handshake
- Total unique handshakes per N epochs
- Wasted attacks (re-attacking captured targets)
- Channel coverage efficiency
- Memory footprint
"""
import sys
import os
import random
import time
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'pagergotchi-nextgen',
                                'payloads', 'user', 'reconnaissance', 'pagergotchi'))

from pwnagotchi_port.nextgen.channel_bandit import ChannelBandit, CHANNELS_2G, CHANNELS_5G
from pwnagotchi_port.nextgen.tactical_engine import (
    TacticalEngine, CaptureContext, RewardV2,
    MODE_ACTIVE, ATTACK_SKIP,
)
from pwnagotchi_port.nextgen.bayesian_optimizer import BayesianOptimizer


class SimulatedEnvironment:
    """Simulates a WiFi environment with APs across multiple bands."""

    def __init__(self, n_aps=50, seed=42):
        random.seed(seed)
        self.aps = []
        channels = CHANNELS_2G + CHANNELS_5G[:15]  # 2.4 + common 5 GHz

        for i in range(n_aps):
            channel = random.choice(channels)
            n_clients = random.choice([0, 0, 1, 2, 3, 5, 8])
            rssi = random.randint(-90, -30)

            clients = []
            for j in range(n_clients):
                clients.append({
                    'mac': f'{i:02x}:{j:02x}:cc:dd:ee:ff',
                    'vendor': 'SimVendor',
                    'last_seen': time.time(),
                })

            self.aps.append({
                'mac': f'aa:bb:cc:dd:{i:02x}:ff',
                'hostname': f'SimAP_{i}',
                'channel': channel,
                'rssi': rssi,
                'encryption': random.choice(['WPA2', 'WPA2', 'WPA2', 'WPA3', 'OPEN']),
                'clients': clients,
                'last_seen': time.time(),
            })

        # Probability of capturing a handshake when attacking
        self.capture_probability = 0.15

    def get_aps_on_channel(self, channel):
        return [ap for ap in self.aps if ap['channel'] == channel]

    def attempt_capture(self, ap):
        """Simulate attacking an AP. Returns True if handshake captured."""
        if ap['encryption'] in ('', 'OPEN'):
            return False
        if not ap['clients']:
            return random.random() < 0.05  # Small PMKID chance
        # More clients = higher capture chance
        base_prob = self.capture_probability
        client_bonus = min(len(ap['clients']) * 0.03, 0.15)
        signal_bonus = 0.05 if ap['rssi'] > -60 else 0.0
        return random.random() < (base_prob + client_bonus + signal_bonus)


def simulate_stock(env, n_epochs=100):
    """Simulate stock Pagergotchi behavior."""
    random.seed(42)
    captured = set()
    attacks = 0
    wasted_attacks = 0
    epoch_first_capture = None

    for epoch in range(n_epochs):
        # Stock: iterate all APs in channel order
        channels_used = set()
        for ap in env.aps:
            if ap['encryption'] in ('', 'OPEN'):
                continue

            attacks += 1
            if ap['mac'] in captured:
                wasted_attacks += 1
                continue

            channels_used.add(ap['channel'])
            if env.attempt_capture(ap):
                captured.add(ap['mac'])
                if epoch_first_capture is None:
                    epoch_first_capture = epoch

    return {
        'captured': len(captured),
        'attacks': attacks,
        'wasted_attacks': wasted_attacks,
        'waste_ratio': wasted_attacks / max(attacks, 1),
        'epoch_first_capture': epoch_first_capture,
    }


def simulate_nextgen(env, n_epochs=100):
    """Simulate NextGen Pagergotchi behavior."""
    random.seed(42)
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        channels = sorted(set(ap['channel'] for ap in env.aps))
        bandit = ChannelBandit(channels=channels, window_size=30, mode=MODE_ACTIVE)
        context = CaptureContext(handshake_dir=td)
        engine = TacticalEngine(context, max_interactions_per_epoch=5,
                               max_targets_per_epoch=20, mode=MODE_ACTIVE)
        optimizer = BayesianOptimizer(n_initial=10)
        reward_fn = RewardV2()

        attacks = 0
        wasted_attacks = 0
        epoch_first_capture = None
        new_aps_seen = set()

        for epoch in range(n_epochs):
            # Bandit selects channels
            selected_channels = bandit.select_channels(k=5)

            # Get APs on selected channels
            visible_aps = []
            for ch in selected_channels:
                visible_aps.extend(env.get_aps_on_channel(ch))

            # Track new APs
            epoch_new_aps = 0
            for ap in visible_aps:
                if ap['mac'] not in new_aps_seen:
                    new_aps_seen.add(ap['mac'])
                    epoch_new_aps += 1

            # Tactical engine plans attacks
            plan = engine.plan_epoch(visible_aps)

            epoch_new_hs = 0
            epoch_targets = 0
            epoch_uncaptured = 0

            for ap, attack_type, score in plan:
                if attack_type == ATTACK_SKIP:
                    continue

                attacks += 1
                epoch_targets += 1
                mac = ap['mac'].lower()

                if context.has_handshake(mac):
                    wasted_attacks += 1
                    continue

                epoch_uncaptured += 1
                context.record_interaction(mac)

                if env.attempt_capture(ap):
                    context.record_handshake(mac)
                    epoch_new_hs += 1
                    bandit.update(ap['channel'], 1.0)
                    if epoch_first_capture is None:
                        epoch_first_capture = epoch

            # Update bandit for channels without captures
            for ch in selected_channels:
                if not any(ap['channel'] == ch and context.has_handshake(ap['mac'].lower())
                          for ap, _, _ in plan):
                    bandit.update(ch, 0.0)

            # Update optimizer
            epoch_metrics = {
                'duration_secs': 30.0,
                'new_unique_handshakes': epoch_new_hs,
                'repeat_handshakes': 0,
                'targets_attacked': epoch_targets,
                'uncaptured_targets_attacked': epoch_uncaptured,
                'channels_scanned': len(selected_channels),
                'channels_with_activity': sum(1 for ch in selected_channels
                                              if any(ap['channel'] == ch for ap in visible_aps)),
                'new_aps_discovered': epoch_new_aps,
            }
            reward = reward_fn(epoch_metrics)
            timing = optimizer.suggest()
            optimizer.observe(timing, reward)

        return {
            'captured': context.captured_count,
            'attacks': attacks,
            'wasted_attacks': wasted_attacks,
            'waste_ratio': wasted_attacks / max(attacks, 1),
            'epoch_first_capture': epoch_first_capture,
        }


def test_simulation():
    """Run comparison simulation."""
    print("=" * 60)
    print("SIMULATION: Stock vs NextGen Pagergotchi")
    print("=" * 60)

    env = SimulatedEnvironment(n_aps=50, seed=42)
    n_epochs = 100

    print(f"\nEnvironment: {len(env.aps)} APs across {len(set(ap['channel'] for ap in env.aps))} channels")
    open_count = sum(1 for ap in env.aps if ap['encryption'] == 'OPEN')
    client_count = sum(len(ap['clients']) for ap in env.aps)
    print(f"  Open networks: {open_count}")
    print(f"  Total clients: {client_count}")
    print(f"  Capture probability: {env.capture_probability}")
    print(f"  Epochs: {n_epochs}")

    stock = simulate_stock(env, n_epochs)
    nextgen = simulate_nextgen(env, n_epochs)

    print(f"\n{'Metric':<30} {'Stock':>10} {'NextGen':>10} {'Improvement':>15}")
    print("-" * 70)
    print(f"{'Unique handshakes':<30} {stock['captured']:>10} {nextgen['captured']:>10} "
          f"{'%+.0f%%' % ((nextgen['captured']/max(stock['captured'],1)-1)*100):>15}")
    print(f"{'Total attacks':<30} {stock['attacks']:>10} {nextgen['attacks']:>10} "
          f"{'%+.0f%%' % ((nextgen['attacks']/max(stock['attacks'],1)-1)*100):>15}")
    print(f"{'Wasted attacks':<30} {stock['wasted_attacks']:>10} {nextgen['wasted_attacks']:>10} "
          f"{'%+.0f%%' % ((nextgen['wasted_attacks']/max(stock['wasted_attacks'],1)-1)*100):>15}")
    print(f"{'Waste ratio':<30} {stock['waste_ratio']:>9.1%} {nextgen['waste_ratio']:>9.1%} "
          f"{'%+.1f%%pts' % ((nextgen['waste_ratio']-stock['waste_ratio'])*100):>15}")
    print(f"{'Epoch of first capture':<30} {stock['epoch_first_capture'] or 'N/A':>10} "
          f"{nextgen['epoch_first_capture'] or 'N/A':>10}")

    # Verify NextGen improvement
    assert nextgen['waste_ratio'] < stock['waste_ratio'], \
        "NextGen should waste fewer attacks than stock"

    print("\nSIMULATION PASS: NextGen shows improvement over stock behavior")


def test_memory_profile():
    """Profile memory usage of all NextGen components."""
    print("\n" + "=" * 60)
    print("MEMORY PROFILE: NextGen Components")
    print("=" * 60)

    import tempfile
    with tempfile.TemporaryDirectory() as td:
        # Full tri-band channel set
        channels = CHANNELS_2G + CHANNELS_5G
        bandit = ChannelBandit(channels=channels, window_size=30, mode=MODE_ACTIVE)
        context = CaptureContext(handshake_dir=td)
        engine = TacticalEngine(context, mode=MODE_ACTIVE)
        optimizer = BayesianOptimizer(n_initial=10)

        # Simulate some usage
        random.seed(42)
        for _ in range(50):
            selected = bandit.select_channels(k=5)
            for ch in selected:
                bandit.update(ch, random.choice([0.0, 0.0, 1.0]))
            params = optimizer.suggest()
            optimizer.observe(params, random.random())

        # Record 200 captured MACs
        for i in range(200):
            context.record_handshake(f'aa:bb:cc:dd:{i//256:02x}:{i%256:02x}')

        # Measure state sizes
        bandit_state = json.dumps(bandit.get_state())
        optimizer_state = json.dumps(optimizer.get_state())
        context_macs = json.dumps(list(context.captured_macs))

        total_kb = (len(bandit_state) + len(optimizer_state) + len(context_macs)) / 1024

        print(f"\n{'Component':<25} {'Size (KB)':>12}")
        print("-" * 40)
        print(f"{'Channel Bandit':<25} {len(bandit_state)/1024:>11.1f}")
        print(f"{'Bayesian Optimizer':<25} {len(optimizer_state)/1024:>11.1f}")
        print(f"{'Capture Context':<25} {len(context_macs)/1024:>11.1f}")
        print(f"{'TOTAL':<25} {total_kb:>11.1f}")
        print(f"\n256 MB budget: {total_kb:.1f} KB / 262144 KB = {total_kb/262144*100:.4f}%")

        assert total_kb < 500, f"Total state too large: {total_kb:.1f} KB"
        print("\nMEMORY PROFILE PASS: Well within 256 MB budget")


def test_triband_bandit_convergence():
    """Test bandit convergence with tri-band channel space."""
    print("\n" + "=" * 60)
    print("TRI-BAND BANDIT CONVERGENCE")
    print("=" * 60)

    random.seed(42)
    channels = CHANNELS_2G + CHANNELS_5G[:15]

    # Simulate: channels 6, 36, 149 are productive
    productive = {6, 36, 149}

    bandit = ChannelBandit(channels=channels, window_size=30, mode=MODE_ACTIVE)

    for epoch in range(100):
        selected = bandit.select_channels(k=5)
        for ch in selected:
            reward = 1.0 if ch in productive else 0.0
            bandit.update(ch, reward)

    stats = bandit.get_stats()
    productive_rate = sum(stats[ch]['success_rate'] for ch in productive if ch in stats) / len(productive)
    other_channels = [ch for ch in channels if ch not in productive and stats[ch]['scans'] > 0]
    if other_channels:
        other_rate = sum(stats[ch]['success_rate'] for ch in other_channels) / len(other_channels)
    else:
        other_rate = 0.0

    band_stats = bandit.get_band_stats()

    print(f"\nProductive channels: {productive}")
    print(f"Productive avg success rate: {productive_rate:.2f}")
    print(f"Other channels avg success rate: {other_rate:.2f}")
    print(f"\nBand statistics:")
    for band, stats in band_stats.items():
        print(f"  {band}: {stats['total_scans']} scans, {stats['success_rate']:.2f} success rate")

    assert productive_rate > other_rate, "Productive channels should have higher success rate"
    print("\nTRI-BAND CONVERGENCE PASS")


def test_all_modes():
    """Test all three operational modes produce different behavior."""
    print("\n" + "=" * 60)
    print("MODE COMPARISON: Active vs Passive vs Assist")
    print("=" * 60)

    import tempfile
    env = SimulatedEnvironment(n_aps=30, seed=42)

    results = {}
    for mode in ['active', 'passive', 'assist']:
        with tempfile.TemporaryDirectory() as td:
            channels = sorted(set(ap['channel'] for ap in env.aps))
            bandit = ChannelBandit(channels=channels, mode=mode)
            context = CaptureContext(handshake_dir=td)
            engine = TacticalEngine(context, mode=mode)

            attacks = 0
            for epoch in range(20):
                plan = engine.plan_epoch(env.aps)
                attacks += len(plan)

                for ap, attack_type, score in plan:
                    if attack_type != ATTACK_SKIP and env.attempt_capture(ap):
                        context.record_handshake(ap['mac'])

            results[mode] = {
                'attacks': attacks,
                'captured': context.captured_count,
            }

    print(f"\n{'Mode':<15} {'Attacks':>10} {'Captured':>10}")
    print("-" * 40)
    for mode, r in results.items():
        print(f"{mode:<15} {r['attacks']:>10} {r['captured']:>10}")

    # Passive should have 0 attacks
    assert results['passive']['attacks'] == 0, "Passive mode should not attack"
    # Assist should have most attacks
    assert results['assist']['attacks'] >= results['active']['attacks'], \
        "Assist mode should be most aggressive"

    print("\nMODE COMPARISON PASS")


if __name__ == '__main__':
    test_simulation()
    test_memory_profile()
    test_triband_bandit_convergence()
    test_all_modes()
    print("\n" + "=" * 60)
    print("ALL SIMULATION TESTS PASSED")
    print("=" * 60)
