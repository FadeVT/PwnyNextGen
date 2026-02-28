"""
Pagergotchi Main Entry Point
Based on original pwnagotchi/cli.py with MINIMAL changes
"""
import logging
import time
import signal
import sys
import os
import atexit
import subprocess

# Add lib directory to path for pagerctl import
_lib_dir = os.path.join(os.path.dirname(__file__), '..', 'lib')
if os.path.abspath(_lib_dir) not in sys.path:
    sys.path.insert(0, os.path.abspath(_lib_dir))

from pagerctl import Pager

import pwnagotchi_port as pwnagotchi
from pwnagotchi_port import utils
from pwnagotchi_port import plugins
from pwnagotchi_port.agent import Agent
from pwnagotchi_port.ui.view import View


# Global exit flag - set by button thread
_exit_requested = False
_agent_ref = None  # Reference to agent for setting its exit flag
_button_monitor_stop = False  # Flag to stop button monitor thread
_button_monitor_thread_ref = None  # Reference to thread for cleanup
_services_restored = False  # Guard against double service restoration


def _restore_pager_services():
    """Restart the Pager's default services so the display and buttons work after exit.

    Without this, the screen stays black and power button is unresponsive
    because pineapplepager (which owns the display and button handling)
    was stopped when the payload launched. pineapd must be started first
    because pineapplepager depends on it.
    """
    global _services_restored
    if _services_restored:
        return
    _services_restored = True
    try:
        if not os.path.exists('/etc/init.d/pineapplepager'):
            return
        # pineapd must be running before pineapplepager (dependency)
        subprocess.Popen(
            ['/etc/init.d/pineapd', 'start'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).wait()
        time.sleep(1)
        subprocess.Popen(
            ['/etc/init.d/pineapplepager', 'start'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


atexit.register(_restore_pager_services)


def _button_monitor_thread(display):
    """Background thread to monitor buttons and handle pause menu.

    Uses thread-safe event queue from pagerctl to reliably detect button presses.
    Handles menu navigation so agent can keep running in background.
    """
    global _exit_requested, _agent_ref, _button_monitor_stop

    logging.info("[BUTTON] Monitor thread started (using event queue)")

    while not _exit_requested and not _button_monitor_stop:
        try:
            # poll_input() reads hardware and populates the event queue
            display.poll_input()

            # Consume events from the thread-safe queue
            event = display.get_input_event()
            if not event:
                time.sleep(0.016)
                continue

            button, event_type, timestamp = event

            # Only react to press events
            if event_type != Pager.EVENT_PRESS:
                continue

            # Reset auto-dim timer; if screen was dimmed, consume this press
            view = _agent_ref._view if _agent_ref and hasattr(_agent_ref, '_view') else None
            if view and view.reset_activity():
                continue  # Screen was dimmed, just wake up without processing button

            # Check if menu is active
            in_menu = _agent_ref and getattr(_agent_ref, '_menu_active', False)

            if in_menu:
                # Handle menu navigation
                view = _agent_ref._view if _agent_ref else None
                if view and hasattr(view, 'handle_menu_input'):
                    result = view.handle_menu_input(button)
                    # Flush stale events that buffered during menu draw
                    display.poll_input()
                    display.clear_input_events()
                    if result == 'exit':
                        _exit_requested = True
                        if _agent_ref:
                            _agent_ref._exit_requested = True
                    elif result in ('main_menu', 'launch'):
                        # Return to main menu or launch another payload
                        # Keep _menu_active = True so pause menu stays visible until loop exits
                        if _agent_ref:
                            _agent_ref._return_to_menu = True
                            _agent_ref._return_target = result
                    elif result == 'resume':
                        if _agent_ref:
                            _agent_ref._menu_active = False
            else:
                # Not in menu - BTN_B opens menu
                if button == Pager.BTN_B:
                    logging.info("[BUTTON] RED button pressed - opening pause menu")
                    if _agent_ref:
                        _agent_ref._menu_active = True
                        # Initialize menu state on view
                        if hasattr(_agent_ref, '_view') and _agent_ref._view:
                            _agent_ref._view.init_pause_menu(_agent_ref)
                            # Flush stale events from menu init draw
                            display.poll_input()
                            display.clear_input_events()

            # No sleep after processing — go right back to polling

        except Exception as e:
            logging.debug("[BUTTON] Event error: %s", e)
            time.sleep(0.016)

    logging.info("[BUTTON] Monitor thread exiting")


def start_button_monitor(view):
    """Start background button monitoring thread"""
    global _button_monitor_stop, _button_monitor_thread_ref
    import threading

    # Reset stop flag
    _button_monitor_stop = False

    if hasattr(view, '_display') and view._display:
        # Clear any stale events from startup menu
        view._display.clear_input_events()
        # Sync button state by doing a poll
        view._display.poll_input()
        view._display.clear_input_events()

        t = threading.Thread(target=_button_monitor_thread, args=(view._display,), daemon=True)
        t.start()
        _button_monitor_thread_ref = t
        return t
    return None


def stop_button_monitor():
    """Stop the button monitor thread"""
    global _button_monitor_stop, _button_monitor_thread_ref

    _button_monitor_stop = True
    if _button_monitor_thread_ref and _button_monitor_thread_ref.is_alive():
        _button_monitor_thread_ref.join(timeout=0.5)
    _button_monitor_thread_ref = None


def should_exit():
    """Check if exit was requested"""
    return _exit_requested


def should_return_to_menu():
    """Check if return to main menu was requested"""
    if _agent_ref and getattr(_agent_ref, '_return_to_menu', False):
        return True
    return False




def _do_stock_loop(agent):
    """Stock Pagergotchi main loop -- sequential channel scanning and brute-force attacks.
    This runs when NextGen is disabled."""
    while not should_exit() and not should_return_to_menu():
        try:
            logging.debug("[LOOP] Starting recon phase...")
            agent.recon()

            if should_exit() or should_return_to_menu():
                break

            channels = agent.get_access_points_by_channel()
            logging.debug("[LOOP] Found %d channels with APs", len(channels))

            for ch, aps in channels:
                if should_exit() or should_return_to_menu():
                    break

                time.sleep(1)
                logging.debug("[LOOP] Setting channel %d (%d APs)", ch, len(aps))
                agent.set_channel(ch)

                if not agent.is_stale() and agent.any_activity():
                    logging.info("%d access points on channel %d" % (len(aps), ch))

                for ap in aps:
                    if should_exit() or should_return_to_menu():
                        break

                    hostname = ap.get('hostname', ap.get('mac', 'unknown'))
                    logging.debug("[ATTACK] Associating with %s", hostname)
                    agent.associate(ap)

                    clients = ap.get('clients', [])
                    if clients:
                        logging.debug("[ATTACK] Deauthing %d clients from %s", len(clients), hostname)
                        for sta in clients:
                            if should_exit() or should_return_to_menu():
                                break
                            agent.deauth(ap, sta)

            if should_exit() or should_return_to_menu():
                break

            logging.debug("[LOOP] Epoch complete, calling next_epoch()")
            agent.next_epoch()

        except Exception as e:
            if str(e).find("wifi.interface not set") > 0:
                logging.exception("main loop exception due to unavailable wifi device (%s)", e)
                logging.info("sleeping 60 seconds then advancing to next epoch")
                time.sleep(60)
                agent.next_epoch()
            else:
                logging.exception("main loop exception (%s)", e)


def _do_nextgen_loop(agent):
    """NextGen intelligence main loop -- bandit channel selection, tactical targeting,
    Bayesian timing optimization."""
    nextgen = agent._nextgen
    ng_cfg = agent._config.get('nextgen', {})
    channels_per_epoch = ng_cfg.get('channels_per_epoch', 5)

    while not should_exit() and not should_return_to_menu():
        try:
            # Phase 1: Recon -- scan broadly to discover APs
            logging.debug("[NG-LOOP] Starting recon phase...")
            agent.recon()

            if should_exit() or should_return_to_menu():
                break

            # Phase 2: Get all visible APs (filtered by whitelist/blacklist)
            all_aps = agent.get_access_points()
            logging.debug("[NG-LOOP] Found %d visible APs", len(all_aps))

            if should_exit() or should_return_to_menu():
                break

            # Phase 3: Channel bandit selects which channels to focus on
            selected_channels = nextgen.select_channels(k=channels_per_epoch)
            logging.info("[NG-LOOP] Bandit selected channels: %s", selected_channels)

            # Phase 4: Tactical engine creates attack plan from visible APs
            plan = nextgen.plan_attacks(all_aps)
            logging.info("[NG-LOOP] Tactical plan: %d targets (of %d visible)",
                         len(plan), len(all_aps))

            # Phase 5: Execute plan, channel by channel
            for ch in selected_channels:
                if should_exit() or should_return_to_menu():
                    break

                time.sleep(0.5)
                logging.debug("[NG-LOOP] Setting channel %d", ch)
                agent.set_channel(ch)

                # Get targets on this channel from the plan
                channel_targets = [
                    (ap, attack, score) for ap, attack, score in plan
                    if ap.get('channel', 0) == ch
                ]

                if not channel_targets:
                    logging.debug("[NG-LOOP] No targets on ch %d, scanning for activity", ch)
                    nextgen.on_channel_scanned(ch, had_activity=False)
                    continue

                if not agent.is_stale() and agent.any_activity():
                    logging.info("[NG-LOOP] %d targets on channel %d", len(channel_targets), ch)

                had_activity = False
                for ap, attack_type, score in channel_targets:
                    if should_exit() or should_return_to_menu():
                        break

                    hostname = ap.get('hostname', ap.get('mac', 'unknown'))
                    logging.debug("[NG-ATTACK] %s on %s (score=%.1f)", attack_type, hostname, score)

                    if nextgen.execute_attack(ap, attack_type):
                        had_activity = True

                nextgen.on_channel_scanned(ch, had_activity=had_activity)

            if should_exit() or should_return_to_menu():
                break

            # Phase 6: End of epoch -- update intelligence, optimize timing
            logging.debug("[NG-LOOP] Epoch complete, updating intelligence...")
            nextgen.on_epoch(agent._epoch.epoch, agent._epoch.data())
            agent.next_epoch()

        except Exception as e:
            if str(e).find("wifi.interface not set") > 0:
                logging.exception("main loop exception due to unavailable wifi device (%s)", e)
                logging.info("sleeping 60 seconds then advancing to next epoch")
                time.sleep(60)
                agent.next_epoch()
            else:
                logging.exception("main loop exception (%s)", e)


def do_auto_mode(agent):
    """
    Main loop - Based on original pwnagotchi/cli.py do_auto_mode()
    Changes: NextGen intelligence integration, exit button check, broadcast deauth for PineAP
    Returns: 'main_menu' to return to startup menu, 'exit' to quit completely
    """
    global _agent_ref, _exit_requested
    logging.info("entering auto mode ...")

    agent.mode = 'auto'
    agent._exit_requested = False
    agent._return_to_menu = False
    agent._return_target = 'main_menu'
    agent._menu_active = False
    _agent_ref = agent

    # Start button monitor thread BEFORE agent.start() so pause works immediately
    start_button_monitor(agent._view)

    agent.start()

    # Update mode indicator on display
    if agent._nextgen:
        mode_label = 'NG:%s' % agent._nextgen.mode_short
        agent._view.set('mode', mode_label)
    else:
        agent._view.set('mode', 'STOCK')

    # Choose loop based on NextGen availability
    if agent._nextgen:
        logging.info("[LOOP] Running NextGen intelligence loop (mode=%s)", agent._nextgen.mode)
        _do_nextgen_loop(agent)
    else:
        logging.info("[LOOP] Running stock Pagergotchi loop")
        _do_stock_loop(agent)

    if should_return_to_menu():
        target = getattr(_agent_ref, '_return_target', 'main_menu') if _agent_ref else 'main_menu'
        logging.info("[LOOP] Return requested, target=%s", target)
        return target
    logging.info("[LOOP] Exit requested, leaving main loop")
    return 'exit'


def load_config(config_path=None):
    """Load configuration - simplified for Pager"""
    # Default configuration matching original pwnagotchi defaults.toml
    config = {
        'main': {
            'name': 'pagergotchi',
            'iface': 'wlan1mon',
            'mon_start_cmd': '',
            'no_restart': True,
            'whitelist': [],
        },
        'personality': {
            # Timing
            'recon_time': 30,
            'max_inactive_scale': 2,
            'recon_inactive_multiplier': 2,
            'hop_recon_time': 10,
            'min_recon_time': 5,
            # Attacks
            'associate': True,
            'deauth': True,
            # Throttling - ORIGINAL VALUES from defaults.toml
            'throttle_a': 0.4,
            'throttle_d': 0.9,
            # Limits
            'ap_ttl': 120,
            'sta_ttl': 300,
            'min_rssi': -200,
            'max_interactions': 3,
            'max_misses_for_recon': 10,
            # Mood (reduced for more dynamic personality)
            'bored_num_epochs': 5,   # ~5 min of inactivity
            'sad_num_epochs': 10,    # ~10 min of inactivity
            # angry triggers at 2x sad = ~20 min
            'excited_num_epochs': 10,
            'bond_encounters_factor': 20000,
            # Channels (empty = all)
            'channels': [],
        },
        'bettercap': {
            'hostname': '127.0.0.1',
            'scheme': 'http',
            'port': 8081,
            'username': 'pwnagotchi',
            'password': 'pwnagotchi',
            'handshakes': '/root/loot/handshakes/pagergotchi',
            'silence': ['wifi.client.probe'],
        },
        'ui': {
            'fps': 2.0,
            'display': {'type': 'pager'},
            'faces': {},
        },
        'nextgen': {
            'enabled': False,
            'mode': 'active',
            'channels_per_epoch': 5,
            'max_targets_per_epoch': 20,
            'optimize_timing': True,
            'bandit_window': 30,
            'bo_initial_epochs': 10,
        }
    }

    # Try to load from config file
    if config_path and os.path.exists(config_path):
        try:
            import configparser
            cp = configparser.ConfigParser()
            cp.read(config_path)

            if 'capture' in cp:
                config['main']['iface'] = cp.get('capture', 'interface', fallback='wlan1mon')
                config['bettercap']['handshakes'] = cp.get('capture', 'output_dir', fallback='/root/loot/handshakes/pagergotchi')

            if 'channels' in cp:
                channels_str = cp.get('channels', 'channels', fallback='')
                if channels_str:
                    config['personality']['channels'] = [int(c.strip()) for c in channels_str.split(',')]

            if 'whitelist' in cp:
                ssids = cp.get('whitelist', 'ssids', fallback='')
                if ssids:
                    config['main']['whitelist'] = [s.strip() for s in ssids.split(',')]

            if 'general' in cp:
                config['main']['debug'] = cp.getboolean('general', 'debug', fallback=False)

            if 'deauth' in cp:
                config['personality']['deauth'] = cp.getboolean('deauth', 'enabled', fallback=True)

            if 'timing' in cp:
                config['personality']['throttle_d'] = cp.getfloat('timing', 'throttle_d', fallback=0.9)
                config['personality']['throttle_a'] = cp.getfloat('timing', 'throttle_a', fallback=0.4)

            # NextGen Intelligence configuration
            if 'nextgen' in cp:
                config['nextgen']['enabled'] = cp.getboolean('nextgen', 'enabled', fallback=False)
                config['nextgen']['mode'] = cp.get('nextgen', 'mode', fallback='active')
                config['nextgen']['channels_per_epoch'] = cp.getint('nextgen', 'channels_per_epoch', fallback=5)
                config['nextgen']['max_targets_per_epoch'] = cp.getint('nextgen', 'max_targets_per_epoch', fallback=20)
                config['nextgen']['optimize_timing'] = cp.getboolean('nextgen', 'optimize_timing', fallback=True)
                config['nextgen']['bandit_window'] = cp.getint('nextgen', 'bandit_window', fallback=30)
                config['nextgen']['bo_initial_epochs'] = cp.getint('nextgen', 'bo_initial_epochs', fallback=10)

            logging.info("Loaded config from %s", config_path)
        except Exception as e:
            logging.warning("Config load error: %s, using defaults", e)

    return config


def main():
    """Main entry point for Pagergotchi"""
    from pwnagotchi_port.log import setup_logging

    # Find config file (relative to this script's location)
    _this_dir = os.path.dirname(os.path.abspath(__file__))
    _payload_dir = os.path.abspath(os.path.join(_this_dir, '..'))
    config_paths = [
        os.path.join(_payload_dir, 'config.conf'),
        './config.conf',
        '../config.conf',
    ]

    config_path = None
    for path in config_paths:
        if os.path.exists(path):
            config_path = path
            break

    # Load config
    config = load_config(config_path)

    # Setup logging (only logs to file if debug=true in config)
    setup_logging(config)

    # Set name
    pwnagotchi.set_name(config['main'].get('name', 'pagergotchi'))

    # Load plugins (stub)
    plugins.load(config)

    # Signal handler — registered once before the main loop.
    # Uses sys.exit(0) so the process actually terminates even when
    # blocking on input (startup menu, etc.). The SystemExit exception
    # propagates through all finally blocks, ensuring cleanup runs
    # exactly once. The atexit handler is a safety net for service restore.
    def signal_handler(sig, frame):
        global _exit_requested
        logging.info("Received signal %d, shutting down...", sig)
        _exit_requested = True
        if _agent_ref:
            _agent_ref._exit_requested = True
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Main loop - allows returning to startup menu
    while True:
        # Show startup menu (Pager-specific addition)
        from pwnagotchi_port.ui.menu import StartupMenu
        startup_menu = StartupMenu(config)
        # pager_init() overrides signal handlers — re-register ours
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        try:
            if not startup_menu.show_main_menu():
                logging.info("User chose to exit from menu")
                startup_menu.cleanup()
                return 0
        finally:
            startup_menu.cleanup()

        # Reload config in case whitelist changed
        config = load_config(config_path)

        # Apply NextGen mode from startup menu settings
        from pwnagotchi_port.ui.menu import load_settings
        _settings = load_settings()
        if 'nextgen_mode' in _settings:
            config['nextgen']['mode'] = _settings['nextgen_mode']

        # Create display/view
        view = View(config)
        # pager_init() overrides signal handlers — re-register ours
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        # Create agent
        agent = Agent(view=view, config=config)

        result = 'exit'
        try:
            result = do_auto_mode(agent)
        except KeyboardInterrupt:
            logging.info("Interrupted by user")
        finally:
            # Clear menu state first to prevent pause menu from being drawn during cleanup
            agent._menu_active = False
            # Stop button monitor to prevent it from accessing cleaned up resources
            stop_button_monitor()
            agent._save_recovery_data()
            agent.stop()  # Stop backend and cleanup tcpdump
            view.on_shutdown()
            time.sleep(0.5)  # Brief pause so user sees shutdown face
            view.cleanup()
            # Restart pineapplepager on final exit only — not when returning
            # to menu (would conflict with new View) or launching another payload
            if result not in ('main_menu', 'launch'):
                _restore_pager_services()

        # Check result
        if result == 'launch':
            global _services_restored
            _services_restored = True  # Don't restore services, another payload takes over
            logging.info("Exiting with code 42 to launch next payload")
            return 42

        if result != 'main_menu':
            break

        logging.info("Returning to main menu...")
        # Reset global flags for next run
        global _exit_requested, _agent_ref
        _exit_requested = False
        _agent_ref = None

    return 0


if __name__ == '__main__':
    sys.exit(main())
