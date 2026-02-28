"""
Unit tests for Tactical Engine (target scoring, skip logic, attack routing)
"""
import sys
import os
import time
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'pagergotchi-nextgen',
                                'payloads', 'user', 'reconnaissance', 'pagergotchi'))

from pwnagotchi_port.nextgen.tactical_engine import (
    TacticalEngine, CaptureContext, RewardV2,
    MODE_ACTIVE, MODE_PASSIVE, MODE_ASSIST,
    ATTACK_ASSOC_DEAUTH, ATTACK_DEAUTH_ONLY, ATTACK_BROADCAST_DEAUTH,
    ATTACK_ASSOC_ONLY, ATTACK_SKIP,
)


def _make_ap(mac='aa:bb:cc:dd:ee:ff', hostname='TestAP', channel=6,
             rssi=-60, encryption='WPA2', clients=None):
    """Helper to create AP dict."""
    return {
        'mac': mac,
        'hostname': hostname,
        'channel': channel,
        'rssi': rssi,
        'encryption': encryption,
        'clients': clients or [],
        'last_seen': time.time(),
    }


def _make_client(mac='11:22:33:44:55:66'):
    """Helper to create client dict."""
    return {
        'mac': mac,
        'vendor': 'TestVendor',
        'last_seen': time.time(),
    }


def test_capture_context_empty():
    """Test CaptureContext with empty directory."""
    with tempfile.TemporaryDirectory() as td:
        ctx = CaptureContext(handshake_dir=td)
        assert ctx.captured_count == 0
        assert not ctx.has_handshake('aa:bb:cc:dd:ee:ff')
        print("PASS: capture_context_empty")


def test_capture_context_record():
    """Test recording and querying handshakes."""
    with tempfile.TemporaryDirectory() as td:
        ctx = CaptureContext(handshake_dir=td)
        ctx.record_handshake('aa:bb:cc:dd:ee:ff')
        assert ctx.has_handshake('aa:bb:cc:dd:ee:ff')
        assert ctx.has_handshake('AA:BB:CC:DD:EE:FF')  # case insensitive
        assert not ctx.has_handshake('11:22:33:44:55:66')
        assert ctx.captured_count == 1
        print("PASS: capture_context_record")


def test_capture_context_pmkid():
    """Test PMKID tracking."""
    with tempfile.TemporaryDirectory() as td:
        ctx = CaptureContext(handshake_dir=td)
        ctx.record_handshake('aa:bb:cc:dd:ee:ff', handshake_type='pmkid')
        assert ctx.has_handshake('aa:bb:cc:dd:ee:ff')
        assert ctx.has_pmkid('aa:bb:cc:dd:ee:ff')
        print("PASS: capture_context_pmkid")


def test_capture_context_interactions():
    """Test interaction counting."""
    with tempfile.TemporaryDirectory() as td:
        ctx = CaptureContext(handshake_dir=td)
        ctx.record_interaction('aa:bb:cc:dd:ee:ff')
        ctx.record_interaction('aa:bb:cc:dd:ee:ff')
        assert ctx.get_session_interactions('aa:bb:cc:dd:ee:ff') == 2
        assert ctx.get_epoch_interactions('aa:bb:cc:dd:ee:ff') == 2

        ctx.new_epoch()
        assert ctx.get_epoch_interactions('aa:bb:cc:dd:ee:ff') == 0
        assert ctx.get_session_interactions('aa:bb:cc:dd:ee:ff') == 2
        print("PASS: capture_context_interactions")


def test_active_scoring():
    """Test active mode scoring logic."""
    with tempfile.TemporaryDirectory() as td:
        ctx = CaptureContext(handshake_dir=td)
        engine = TacticalEngine(ctx, mode=MODE_ACTIVE)

        # WPA2 AP with 3 clients, good signal
        ap = _make_ap(rssi=-50, clients=[_make_client(f"c{i}:00:00:00:00:00") for i in range(3)])
        score = engine.score_target(ap)
        assert score > 0, f"WPA2 AP with clients should score positive, got {score}"

        # Open network should score negative
        open_ap = _make_ap(encryption='OPEN')
        assert engine.score_target(open_ap) == -500.0

        # Already captured should score very negative
        ctx.record_handshake('aa:bb:cc:dd:ee:ff')
        captured_ap = _make_ap(mac='aa:bb:cc:dd:ee:ff')
        assert engine.score_target(captured_ap) == -1000.0

        print("PASS: active_scoring")


def test_passive_scoring():
    """Test passive mode scoring prioritizes client activity."""
    with tempfile.TemporaryDirectory() as td:
        ctx = CaptureContext(handshake_dir=td)
        engine = TacticalEngine(ctx, mode=MODE_PASSIVE)

        # AP with many clients should score higher
        busy_ap = _make_ap(mac='11:11:11:11:11:11',
                          clients=[_make_client(f"c{i}:00:00:00:00:00") for i in range(5)])
        quiet_ap = _make_ap(mac='22:22:22:22:22:22', clients=[])

        busy_score = engine.score_target(busy_ap)
        quiet_score = engine.score_target(quiet_ap)
        assert busy_score > quiet_score, "Passive mode should favor busy APs"

        # Open still filtered
        assert engine.score_target(_make_ap(encryption='OPEN')) == -500.0

        print("PASS: passive_scoring")


def test_assist_scoring():
    """Test assist mode scores by disruption potential."""
    with tempfile.TemporaryDirectory() as td:
        ctx = CaptureContext(handshake_dir=td)
        engine = TacticalEngine(ctx, mode=MODE_ASSIST)

        # Already-captured AP with clients should still score positive (no skip logic)
        ctx.record_handshake('aa:bb:cc:dd:ee:ff')
        captured_with_clients = _make_ap(
            mac='aa:bb:cc:dd:ee:ff',
            clients=[_make_client(f"c{i}:00:00:00:00:00") for i in range(3)]
        )
        score = engine.score_target(captured_with_clients)
        assert score > 0, f"Assist mode should still target captured APs with clients, got {score}"

        print("PASS: assist_scoring")


def test_attack_selection_active():
    """Test attack type selection in active mode."""
    with tempfile.TemporaryDirectory() as td:
        ctx = CaptureContext(handshake_dir=td)
        engine = TacticalEngine(ctx, mode=MODE_ACTIVE)

        # AP with clients: assoc + deauth
        with_clients = _make_ap(clients=[_make_client()])
        assert engine.select_attack(with_clients) == ATTACK_ASSOC_DEAUTH

        # AP without clients: assoc only
        no_clients = _make_ap(clients=[])
        assert engine.select_attack(no_clients) == ATTACK_ASSOC_ONLY

        # AP with PMKID already captured + clients: deauth only
        ctx.record_handshake('aa:bb:cc:dd:ee:ff', 'pmkid')
        pmkid_ap = _make_ap(clients=[_make_client()])
        assert engine.select_attack(pmkid_ap) == ATTACK_DEAUTH_ONLY

        print("PASS: attack_selection_active")


def test_attack_selection_passive():
    """Test passive mode always skips."""
    with tempfile.TemporaryDirectory() as td:
        ctx = CaptureContext(handshake_dir=td)
        engine = TacticalEngine(ctx, mode=MODE_PASSIVE)

        ap = _make_ap(clients=[_make_client()])
        assert engine.select_attack(ap) == ATTACK_SKIP
        print("PASS: attack_selection_passive")


def test_attack_selection_assist():
    """Test assist mode maximizes aggression."""
    with tempfile.TemporaryDirectory() as td:
        ctx = CaptureContext(handshake_dir=td)
        engine = TacticalEngine(ctx, mode=MODE_ASSIST)

        # With clients: broadcast deauth
        with_clients = _make_ap(clients=[_make_client()])
        assert engine.select_attack(with_clients) == ATTACK_BROADCAST_DEAUTH

        # Without clients: assoc only
        no_clients = _make_ap(clients=[])
        assert engine.select_attack(no_clients) == ATTACK_ASSOC_ONLY

        print("PASS: attack_selection_assist")


def test_plan_epoch():
    """Test epoch planning produces prioritized plan."""
    with tempfile.TemporaryDirectory() as td:
        ctx = CaptureContext(handshake_dir=td)
        engine = TacticalEngine(ctx, max_targets_per_epoch=5, mode=MODE_ACTIVE)

        # Create 10 APs with varying quality
        aps = []
        for i in range(10):
            clients = [_make_client(f"c{j}:00:00:00:00:{i:02x}") for j in range(i)]
            aps.append(_make_ap(
                mac=f"aa:bb:cc:dd:{i:02x}:ff",
                hostname=f"AP_{i}",
                rssi=-50 - i * 5,
                clients=clients
            ))

        # Mark some as captured
        ctx.record_handshake('aa:bb:cc:dd:00:ff')  # AP_0
        ctx.record_handshake('aa:bb:cc:dd:01:ff')  # AP_1

        plan = engine.plan_epoch(aps)

        # Should have at most max_targets_per_epoch entries
        assert len(plan) <= 5

        # Captured APs should not be in plan
        plan_macs = [ap.get('mac', '').lower() for ap, _, _ in plan]
        assert 'aa:bb:cc:dd:00:ff' not in plan_macs
        assert 'aa:bb:cc:dd:01:ff' not in plan_macs

        # Plan should be sorted by score descending
        scores = [score for _, _, score in plan]
        assert scores == sorted(scores, reverse=True)

        print("PASS: plan_epoch")


def test_plan_epoch_passive():
    """Test passive mode produces empty plan."""
    with tempfile.TemporaryDirectory() as td:
        ctx = CaptureContext(handshake_dir=td)
        engine = TacticalEngine(ctx, mode=MODE_PASSIVE)

        aps = [_make_ap(clients=[_make_client()])]
        plan = engine.plan_epoch(aps)
        assert plan == [], "Passive mode should produce empty plan"
        print("PASS: plan_epoch_passive")


def test_diminishing_returns():
    """Test that repeatedly attacking the same target reduces score."""
    with tempfile.TemporaryDirectory() as td:
        ctx = CaptureContext(handshake_dir=td)
        engine = TacticalEngine(ctx, max_interactions_per_epoch=3, mode=MODE_ACTIVE)

        ap = _make_ap(clients=[_make_client()])

        score1 = engine.score_target(ap)
        ctx.record_interaction(ap['mac'])
        score2 = engine.score_target(ap)
        assert score2 < score1, "Score should decrease with more interactions"

        # After max interactions, should be skipped
        ctx.record_interaction(ap['mac'])
        ctx.record_interaction(ap['mac'])
        score3 = engine.score_target(ap)
        assert score3 == -100.0, "Should be skipped after max epoch interactions"

        print("PASS: diminishing_returns")


def test_reward_v2():
    """Test RewardV2 function."""
    reward_fn = RewardV2()

    # High performance epoch
    high = reward_fn({
        'duration_secs': 60,
        'new_unique_handshakes': 3,
        'repeat_handshakes': 0,
        'targets_attacked': 5,
        'uncaptured_targets_attacked': 5,
        'channels_scanned': 5,
        'channels_with_activity': 3,
        'new_aps_discovered': 2,
    })

    # Low performance epoch
    low = reward_fn({
        'duration_secs': 60,
        'new_unique_handshakes': 0,
        'repeat_handshakes': 0,
        'targets_attacked': 5,
        'uncaptured_targets_attacked': 1,
        'channels_scanned': 5,
        'channels_with_activity': 0,
        'new_aps_discovered': 0,
    })

    assert high > low, f"High-performance epoch should reward more: {high} vs {low}"
    print("PASS: reward_v2")


def test_scan_22000_files():
    """Test CaptureContext scanning of .22000 files."""
    with tempfile.TemporaryDirectory() as td:
        # Create a fake .22000 file
        mac = "AABBCCDDEEFF"
        filepath = os.path.join(td, f"{mac}_handshake.22000")
        with open(filepath, 'w') as f:
            f.write("WPA*02*abcdef*aabbccddeeff*112233445566*54657374415000*\n")

        ctx = CaptureContext(handshake_dir=td)
        assert ctx.captured_count >= 1
        assert ctx.has_handshake('aa:bb:cc:dd:ee:ff')
        print("PASS: scan_22000_files")


if __name__ == '__main__':
    test_capture_context_empty()
    test_capture_context_record()
    test_capture_context_pmkid()
    test_capture_context_interactions()
    test_active_scoring()
    test_passive_scoring()
    test_assist_scoring()
    test_attack_selection_active()
    test_attack_selection_passive()
    test_attack_selection_assist()
    test_plan_epoch()
    test_plan_epoch_passive()
    test_diminishing_returns()
    test_reward_v2()
    test_scan_22000_files()
    print("\nAll tactical engine tests passed.")
