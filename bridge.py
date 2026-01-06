#!/usr/bin/env python3
"""TotalMix OSC Bridge - HTTP server for Squig EQ integration."""

import argparse
import datetime
import json
import math
import os
import subprocess
import sys
import tempfile
import threading
import time
import winreg
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

from pythonosc import udp_client, dispatcher, osc_server

# Try to import pystray for system tray support
try:
    import pystray
    from pystray import MenuItem as item
    TRAY_AVAILABLE = True
except ImportError:
    TRAY_AVAILABLE = False

# For creating icon
try:
    from PIL import Image, ImageDraw
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False


# Logging setup - log to file in AppData
def get_log_path():
    """Get path to log file in user's app data."""
    appdata = os.environ.get('APPDATA', os.path.expanduser('~'))
    log_dir = os.path.join(appdata, 'TotalMixOSCBridge')
    os.makedirs(log_dir, exist_ok=True)
    return os.path.join(log_dir, 'bridge.log')

def setup_logging():
    """Setup logging to file, clearing previous content."""
    log_path = get_log_path()
    # Clear log file for this run
    with open(log_path, 'w', encoding='utf-8') as f:
        f.write(f"=== TotalMix OSC Bridge started at {datetime.datetime.now()} ===\n\n")
    return log_path

class LogWriter:
    """Redirect stdout/stderr to log file."""
    def __init__(self, log_path):
        self.log_path = log_path
        self.terminal = sys.__stdout__  # Keep reference to original

    def write(self, message):
        if message.strip():  # Skip empty lines
            try:
                with open(self.log_path, 'a', encoding='utf-8') as f:
                    f.write(message)
                    if not message.endswith('\n'):
                        f.write('\n')
            except:
                pass

    def flush(self):
        pass

def open_log_file():
    """Open log file in default text editor."""
    log_path = get_log_path()
    if os.path.exists(log_path):
        subprocess.Popen(['notepad.exe', log_path])

# App info
APP_NAME = "TotalMix OSC Bridge"
APP_VERSION = "1.0.0"

# Default configuration (uses Remote Controller 3 to avoid conflict with StreamDock)
DEFAULT_HTTP_PORT = 8765
DEFAULT_TOTALMIX_IP = "127.0.0.1"
DEFAULT_TOTALMIX_PORT = 7003      # Send commands to TotalMix (RC3)
DEFAULT_LISTEN_PORT = 9003        # Receive responses from TotalMix (RC3)

# Runtime configuration (set by main)
HTTP_PORT = DEFAULT_HTTP_PORT
TOTALMIX_IP = DEFAULT_TOTALMIX_IP
TOTALMIX_PORT = DEFAULT_TOTALMIX_PORT
LISTEN_PORT = DEFAULT_LISTEN_PORT
DEBUG = False

# OSC state cache (populated by listener)
osc_cache = {}
cache_lock = threading.Lock()

# OSC client (initialized in main)
osc_client = None

# Config file path
def get_config_path():
    """Get path to config file in user's app data."""
    appdata = os.environ.get('APPDATA', os.path.expanduser('~'))
    config_dir = os.path.join(appdata, 'TotalMixOSCBridge')
    os.makedirs(config_dir, exist_ok=True)
    return os.path.join(config_dir, 'config.json')

def load_config():
    """Load configuration from file."""
    config_path = get_config_path()
    default_config = {
        'run_at_startup': False
    }
    try:
        if os.path.exists(config_path):
            with open(config_path, 'r') as f:
                config = json.load(f)
                # Merge with defaults
                for key in default_config:
                    if key not in config:
                        config[key] = default_config[key]
                return config
    except Exception:
        pass
    return default_config

def save_config(config):
    """Save configuration to file."""
    config_path = get_config_path()
    try:
        with open(config_path, 'w') as f:
            json.dump(config, f, indent=2)
    except Exception as e:
        print(f"[ERROR] Failed to save config: {e}")

def get_exe_path():
    """Get path to the executable."""
    if getattr(sys, 'frozen', False):
        return sys.executable
    else:
        return os.path.abspath(sys.argv[0])

def is_startup_enabled():
    """Check if app is set to run at Windows startup."""
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0, winreg.KEY_READ
        )
        try:
            winreg.QueryValueEx(key, APP_NAME)
            return True
        except WindowsError:
            return False
        finally:
            winreg.CloseKey(key)
    except Exception:
        return False

def set_startup_enabled(enabled):
    """Enable or disable running at Windows startup."""
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0, winreg.KEY_SET_VALUE
        )
        try:
            if enabled:
                exe_path = get_exe_path()
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, f'"{exe_path}"')
            else:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                except WindowsError:
                    pass
        finally:
            winreg.CloseKey(key)
        return True
    except Exception as e:
        print(f"[ERROR] Failed to modify startup: {e}")
        return False




def create_tray_icon_file():
    """Create a tray icon .ico file and return its path."""
    if not PIL_AVAILABLE:
        return None

    size = 64
    image = Image.new('RGBA', (size, size), color=(0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    # Draw a simple "O" shape to represent OSC
    margin = 8
    draw.ellipse([margin, margin, size-margin, size-margin],
                 outline=(100, 200, 100), width=6)
    # Draw center dot
    center = size // 2
    dot_size = 8
    draw.ellipse([center-dot_size, center-dot_size, center+dot_size, center+dot_size],
                 fill=(100, 200, 100))

    # Save as .ico file
    ico_path = os.path.join(tempfile.gettempdir(), 'totalmix_bridge_icon.ico')
    image.save(ico_path, format='ICO', sizes=[(64, 64)])
    return ico_path


def osc_handler(address, *args):
    """Handle incoming OSC messages from TotalMix."""
    # Skip heartbeat
    if address == "/":
        return

    value = args[0] if args else None

    with cache_lock:
        osc_cache[address] = value

    if DEBUG:
        # Show tracknames and EQ-related addresses
        if 'trackname' in address.lower() or 'eq' in address.lower() or 'req' in address.lower():
            print(f"[OSC] {address} = {value}")


# Value conversion functions
def freq_to_osc(hz):
    """Convert Hz (20-20000) to OSC (0.0-1.0) logarithmic."""
    log_min, log_max = math.log10(20), math.log10(20000)
    log_freq = math.log10(max(20, min(20000, hz)))
    return (log_freq - log_min) / (log_max - log_min)

def osc_to_freq(osc):
    """Convert OSC (0.0-1.0) to Hz (20-20000) logarithmic."""
    log_min, log_max = math.log10(20), math.log10(20000)
    return 10 ** (log_min + osc * (log_max - log_min))

def gain_to_osc(db):
    """Convert dB (-20 to +20) to OSC (0.0-1.0)."""
    return (max(-20, min(20, db)) + 20) / 40

def osc_to_gain(osc):
    """Convert OSC (0.0-1.0) to dB (-20 to +20)."""
    return osc * 40 - 20

def q_to_osc(q):
    """Convert Q (0.4-9.9) to OSC (0.0-1.0)."""
    return (max(0.4, min(9.9, q)) - 0.4) / 9.5

def osc_to_q(osc):
    """Convert OSC (0.0-1.0) to Q (0.4-9.9)."""
    return 0.4 + osc * 9.5

def osc_to_filter_type(osc, band, eq_type='req'):
    """Convert OSC type value to Squig filter type string.

    TotalMix only has Bell (0.0) and Shelf (0.333).
    Shelf type is determined by band position:
    - REQ band 1: Low Shelf
    - REQ bands 8,9: High Shelf
    - PEQ band 1: Low Shelf
    - PEQ band 3: High Shelf
    """
    if osc is None or osc < 0.2:
        return 'PK'  # Bell/Peak

    # Shelf - determine low/high based on band position
    if eq_type == 'req':
        if band == 1:
            return 'LSQ'
        elif band in (8, 9):
            return 'HSQ'
    elif eq_type == 'peq':
        if band == 1:
            return 'LSQ'
        elif band == 3:
            return 'HSQ'

    return 'PK'  # Fallback (shouldn't happen)

def filter_type_to_osc(ftype):
    """Convert Squig filter type to OSC value.

    TotalMix only has Bell (0.0) and Shelf (0.333).
    """
    ftype_upper = ftype.upper() if ftype else 'PK'
    if ftype_upper in ('LSQ', 'LS', 'LSC', 'HSQ', 'HS', 'HSC'):
        return 0.333333  # Shelf
    else:
        return 0.0  # Bell/Peak (also for HP, LP which aren't supported)


class BridgeHandler(BaseHTTPRequestHandler):
    """HTTP request handler."""

    def send_json(self, data, status=200):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def do_OPTIONS(self):
        """Handle CORS preflight."""
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self):
        global osc_client
        path = urlparse(self.path).path

        if path == '/api/status':
            self.send_json({'status': 'ok', 'port': HTTP_PORT})

        elif path == '/api/channels':
            # Request channel names from TotalMix
            # TotalMix sends 8 track names at a time based on current bank
            osc_client.send_message('/1/busOutput', 1.0)
            time.sleep(0.05)

            channels = []
            seen_names = set()

            # Iterate through banks until we get duplicates or empty
            for bank in [0, 8, 16, 24]:
                osc_client.send_message('/setBankStart', float(bank))
                time.sleep(0.15)  # Wait for TotalMix to send track names

                bank_has_new = False
                with cache_lock:
                    for i in range(1, 9):  # trackname1 through trackname8
                        name = osc_cache.get(f'/1/trackname{i}', '')
                        if not name or name == 'n.a.':
                            continue
                        # Check for duplicate (means we've looped back)
                        key = f"{name}_{i}"
                        if bank > 0 and key in seen_names:
                            continue
                        seen_names.add(key)
                        channel_num = bank + i
                        channels.append({'index': channel_num, 'name': name})
                        bank_has_new = True

                # Stop if this bank had no new channels
                if not bank_has_new:
                    break

            self.send_json({'channels': channels})

        elif path.startswith('/api/channel/') and path.endswith('/eq'):
            # GET /api/channel/1/eq - Read EQ from channel
            try:
                channel = int(path.split('/')[3])
            except (IndexError, ValueError):
                self.send_json({'error': 'Invalid channel'}, 400)
                return

            # Select channel on Page 4 (Room EQ)
            osc_client.send_message('/4/busOutput', 1.0)
            time.sleep(0.03)
            bank = (channel - 1) // 8
            offset = (channel - 1) % 8
            osc_client.send_message('/setBankStart', float(bank * 8))
            time.sleep(0.03)
            osc_client.send_message('/setOffsetInBank', float(offset))
            time.sleep(0.15)  # Wait for TotalMix to send state

            # Read Room EQ (9 bands)
            filters = []
            with cache_lock:
                for i in range(1, 10):
                    freq_osc = osc_cache.get(f'/4/reqFreq{i}', 0.5)
                    gain = osc_cache.get(f'/4/reqGain{i}', 0.5)
                    q = osc_cache.get(f'/4/reqQ{i}', 0.5)
                    ftype = osc_cache.get(f'/4/reqType{i}', 0.0)

                    freq_hz = osc_to_freq(freq_osc)
                    gain_db = osc_to_gain(gain)
                    if abs(gain_db) > 0.1:  # Skip zero-gain bands
                        filters.append({
                            'type': osc_to_filter_type(ftype, i, 'req'),
                            'freq': round(freq_hz),
                            'gain': round(gain_db, 1),
                            'q': round(osc_to_q(q), 2)
                        })

            # Select channel on Page 2 (PEQ)
            osc_client.send_message('/2/busOutput', 1.0)
            time.sleep(0.03)
            osc_client.send_message('/setBankStart', float(bank * 8))
            time.sleep(0.03)
            osc_client.send_message('/setOffsetInBank', float(offset))
            time.sleep(0.15)

            # Read PEQ (3 bands)
            with cache_lock:
                for i in range(1, 4):
                    freq_osc = osc_cache.get(f'/2/eqFreq{i}', 0.5)
                    gain = osc_cache.get(f'/2/eqGain{i}', 0.5)
                    q = osc_cache.get(f'/2/eqQ{i}', 0.5)
                    ftype = osc_cache.get(f'/2/eqType{i}', 0.0)

                    freq_hz = osc_to_freq(freq_osc)
                    gain_db = osc_to_gain(gain)
                    if abs(gain_db) > 0.1:
                        filters.append({
                            'type': osc_to_filter_type(ftype, i, 'peq'),
                            'freq': round(freq_hz),
                            'gain': round(gain_db, 1),
                            'q': round(osc_to_q(q), 2)
                        })

            self.send_json({'filters': filters})

        else:
            self.send_json({'error': 'Not found'}, 404)

    def do_POST(self):
        global osc_client
        path = urlparse(self.path).path

        if path.startswith('/api/channel/') and path.endswith('/eq'):
            # POST /api/channel/1/eq - Write EQ to channel
            try:
                channel = int(path.split('/')[3])
            except (IndexError, ValueError):
                self.send_json({'error': 'Invalid channel'}, 400)
                return

            # Read request body
            content_len = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_len)
            data = json.loads(body)
            filters = data.get('filters', [])

            # Limit to 12 filters
            filters = filters[:12]
            room_eq = filters[:9]
            peq = filters[9:12]

            # Select channel on Page 4 (Room EQ)
            osc_client.send_message('/4/busOutput', 1.0)
            time.sleep(0.03)
            bank = (channel - 1) // 8
            offset = (channel - 1) % 8
            osc_client.send_message('/setBankStart', float(bank * 8))
            time.sleep(0.03)
            osc_client.send_message('/setOffsetInBank', float(offset))
            time.sleep(0.05)

            # Send Room EQ bands
            for i in range(1, 10):
                if i <= len(room_eq):
                    f = room_eq[i - 1]
                    osc_client.send_message(f'/4/reqType{i}', filter_type_to_osc(f.get('type', 'PK')))
                    osc_client.send_message(f'/4/reqFreq{i}', freq_to_osc(f['freq']))
                    osc_client.send_message(f'/4/reqGain{i}', gain_to_osc(f['gain']))
                    osc_client.send_message(f'/4/reqQ{i}', q_to_osc(f.get('q', 1.0)))
                else:
                    # Clear unused bands (set gain to 0)
                    osc_client.send_message(f'/4/reqGain{i}', 0.5)
                time.sleep(0.01)

            # Enable Room EQ only if not already enabled
            time.sleep(0.05)
            with cache_lock:
                req_enabled = osc_cache.get('/4/reqEnable', 0.0)
            if req_enabled < 0.5:
                osc_client.send_message('/4/reqEnable', 1.0)

            # Handle PEQ
            osc_client.send_message('/2/busOutput', 1.0)
            time.sleep(0.03)
            osc_client.send_message('/setBankStart', float(bank * 8))
            time.sleep(0.03)
            osc_client.send_message('/setOffsetInBank', float(offset))
            time.sleep(0.05)

            # Check if PEQ has any actual filters (non-zero gain)
            peq_has_filters = any(abs(f.get('gain', 0)) > 0.1 for f in peq)

            if peq_has_filters:
                # Send PEQ bands
                for i in range(1, 4):
                    if i <= len(peq):
                        f = peq[i - 1]
                        osc_client.send_message(f'/2/eqType{i}', filter_type_to_osc(f.get('type', 'PK')))
                        osc_client.send_message(f'/2/eqFreq{i}', freq_to_osc(f['freq']))
                        osc_client.send_message(f'/2/eqGain{i}', gain_to_osc(f['gain']))
                        osc_client.send_message(f'/2/eqQ{i}', q_to_osc(f.get('q', 1.0)))
                    else:
                        osc_client.send_message(f'/2/eqGain{i}', 0.5)
                    time.sleep(0.01)

                # Enable PEQ only if not already enabled
                time.sleep(0.05)
                with cache_lock:
                    peq_enabled = osc_cache.get('/2/eqEnable', 0.0)
                if peq_enabled < 0.5:
                    osc_client.send_message('/2/eqEnable', 1.0)
            else:
                # No PEQ overflow - clear and disable PEQ
                for i in range(1, 4):
                    osc_client.send_message(f'/2/eqGain{i}', 0.5)
                    time.sleep(0.01)

                # Disable PEQ only if currently enabled (send 1.0 to toggle off)
                time.sleep(0.05)
                with cache_lock:
                    peq_enabled = osc_cache.get('/2/eqEnable', 0.0)
                if peq_enabled >= 0.5:
                    osc_client.send_message('/2/eqEnable', 1.0)

            # Count actual filters (non-zero gain)
            room_eq_count = sum(1 for f in room_eq if abs(f.get('gain', 0)) > 0.1)
            peq_count = sum(1 for f in peq if abs(f.get('gain', 0)) > 0.1)

            self.send_json({
                'success': True,
                'roomEQ': room_eq_count,
                'peq': peq_count
            })

        else:
            self.send_json({'error': 'Not found'}, 404)

    def log_message(self, format, *args):
        if not getattr(self, '_quiet', False):
            print(f"[{self.log_date_time_string()}] {args[0]}")


class TrayApp:
    """System tray application wrapper using pystray."""

    def __init__(self, http_server, osc_srv):
        self.http_server = http_server
        self.osc_srv = osc_srv
        self.icon = None
        self.config = load_config()
        self.running = True

    def _create_icon_image(self):
        """Create tray icon image."""
        size = 64
        image = Image.new('RGBA', (size, size), color=(0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        margin = 8
        draw.ellipse([margin, margin, size-margin, size-margin],
                     outline=(100, 200, 100), width=6)
        center = size // 2
        dot_size = 8
        draw.ellipse([center-dot_size, center-dot_size, center+dot_size, center+dot_size],
                     fill=(100, 200, 100))
        return image

    def open_log(self, icon=None, item=None):
        """Open log file in notepad."""
        open_log_file()

    def toggle_startup(self, icon=None, item=None):
        """Toggle Windows startup."""
        current = is_startup_enabled()
        set_startup_enabled(not current)

    def quit_app(self, icon=None, item=None):
        """Quit the application."""
        self.running = False
        self.icon.stop()
        self.http_server.shutdown()
        self.osc_srv.shutdown()

    def _get_menu(self):
        """Build menu for pystray."""
        return pystray.Menu(
            item('Open Log File', self.open_log),
            item('Start with Windows', self.toggle_startup, checked=lambda item: is_startup_enabled()),
            item('Quit', self.quit_app)
        )

    def run(self):
        """Run the tray application."""
        self.icon = pystray.Icon(
            APP_NAME,
            self._create_icon_image(),
            APP_NAME,
            self._get_menu()
        )
        self.icon.run()


def main():
    global HTTP_PORT, TOTALMIX_IP, TOTALMIX_PORT, LISTEN_PORT, DEBUG, osc_client

    # Setup logging to file (clears previous log)
    log_path = setup_logging()
    log_writer = LogWriter(log_path)
    sys.stdout = log_writer
    sys.stderr = log_writer

    parser = argparse.ArgumentParser(description='TotalMix OSC Bridge for Squig')
    parser.add_argument('--http-port', type=int, default=DEFAULT_HTTP_PORT,
                        help=f'HTTP server port (default: {DEFAULT_HTTP_PORT})')
    parser.add_argument('--osc-send', type=int, default=DEFAULT_TOTALMIX_PORT,
                        help=f'TotalMix OSC incoming port (default: {DEFAULT_TOTALMIX_PORT})')
    parser.add_argument('--osc-listen', type=int, default=DEFAULT_LISTEN_PORT,
                        help=f'OSC listen port (default: {DEFAULT_LISTEN_PORT})')
    parser.add_argument('--debug', action='store_true',
                        help='Enable debug output for OSC messages')
    args = parser.parse_args()

    HTTP_PORT = args.http_port
    TOTALMIX_PORT = args.osc_send
    LISTEN_PORT = args.osc_listen
    DEBUG = args.debug

    # Build and store startup message for redisplay
    startup_msg = []
    startup_msg.append("=" * 50)
    startup_msg.append(f"  {APP_NAME} v{APP_VERSION}")
    startup_msg.append("=" * 50)
    startup_msg.append(f"  HTTP server: http://127.0.0.1:{HTTP_PORT}")
    startup_msg.append(f"  TotalMix:    {TOTALMIX_IP}:{TOTALMIX_PORT}")
    startup_msg.append(f"  Listening:   port {LISTEN_PORT}")
    startup_msg.append("")
    startup_msg.append("  TotalMix OSC setup (Options > Settings > OSC):")
    startup_msg.append("    Remote Controller 3: In Use = checked")
    startup_msg.append(f"    Port incoming: {TOTALMIX_PORT}")
    startup_msg.append(f"    Port outgoing: {LISTEN_PORT}")
    startup_msg.append("=" * 50)
    startup_msg.append("")

    for line in startup_msg:
        print(line)

    # Create OSC client
    osc_client = udp_client.SimpleUDPClient(TOTALMIX_IP, TOTALMIX_PORT)
    print(f"[OK] OSC client ready (sending to {TOTALMIX_IP}:{TOTALMIX_PORT})")

    # Create OSC dispatcher and server
    disp = dispatcher.Dispatcher()
    disp.set_default_handler(osc_handler)

    try:
        osc_srv = osc_server.ThreadingOSCUDPServer(
            ('0.0.0.0', LISTEN_PORT), disp
        )
        osc_thread = threading.Thread(target=osc_srv.serve_forever, daemon=True)
        osc_thread.start()
        print(f"[OK] OSC server started on port {LISTEN_PORT}")
    except PermissionError:
        print(f"[ERROR] Cannot bind to port {LISTEN_PORT}")
        print()
        print("  Possible causes:")
        print("    1. Another instance of totalmix-bridge is already running")
        print("    2. Another application is using the port")
        print("    3. Try running as Administrator")
        print()
        input("Press Enter to exit...")
        return
    except OSError as e:
        print(f"[ERROR] Failed to start OSC server: {e}")
        input("Press Enter to exit...")
        return

    # Start HTTP server
    http_server = HTTPServer(('0.0.0.0', HTTP_PORT), BridgeHandler)
    http_thread = threading.Thread(target=http_server.serve_forever, daemon=True)
    http_thread.start()
    print(f"[OK] HTTP server started on port {HTTP_PORT}")
    print()

    # Run with system tray if available
    if TRAY_AVAILABLE:
        print("Bridge ready. Running in system tray.")
        print("Right-click tray icon for options.")
        print()

        tray_app = TrayApp(http_server, osc_srv)
        tray_app.run()  # Blocking - runs until quit
        print("Goodbye!")
    else:
        print("Bridge ready. Press Ctrl+C to stop.")
        print("(Install pystray and pillow for system tray support)")
        print()

        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nShutting down...")
            http_server.shutdown()
            osc_srv.shutdown()
            print("Goodbye!")


if __name__ == '__main__':
    main()
