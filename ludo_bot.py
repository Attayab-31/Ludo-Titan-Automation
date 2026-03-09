import time
import random
import subprocess
import platform
import hashlib
import os
import shutil
import re
import string
import warnings
import contextlib
import io
import json
import sys
import atexit
import threading
import traceback
from datetime import datetime
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError
cv2 = None
np = None
easyocr = None
AdbClient = None
import argparse

# --- Supported config: user must set LDPlayer to match this ---
LDPLAYER_RESOLUTION = (1080, 1920)  # Portrait: width x height
LDPLAYER_ADB_DEFAULT = "127.0.0.1:5555"  # Default ADB address for single LDPlayer instance

# --- SELLER: Replace with your license server and payment info ---
LICENSE_CHECK_URL = "https://ludo-bot-1zwp.onrender.com/validate"  # GET ?key=XXX&machine_id=YYY -> 200 = valid
LICENSE_BASE_URL = LICENSE_CHECK_URL.rsplit("/", 1)[0]

# Banner header (fallback if server header not available)
DEFAULT_BANNER_LINES = [
    "==========  Ludo Titan Bot  ==========",
    "Made by Kaptaan  |  Instagram: kaptan_31",
    "----------------------------------------",
]

# Disclaimer
DISCLAIMER_VERSION = "1"
DEFAULT_DISCLAIMER_TEXT = """
IMPORTANT NOTICE & TERMS OF USE
--------------------------------
1) This software is provided by Kaptaan for authorized users only.
2) The creator may pause, stop, or revoke access at any time, with or without notice.
3) The creator may add or remove any machine ID at any time, for any reason.
4) This software is provided "as is" with no guarantees of performance or availability.
5) You are responsible for how you use this tool and for complying with all laws and platform rules.
6) If you do not agree to these terms, do not use this software.
"""

# Master key is only on the server (MASTER_LICENSE_KEY env). Not in this file – one copy for everyone.

# Payment instructions are now served by the license server (/payment_info).

# Logging / UX
VERBOSE = False
ADB_BIN = None
ADB_SOURCE = None
ADB_LOGGED = False
ADB_MISSING_LOGGED = False
LDCONSOLE_BIN = None
LDCONSOLE_SOURCE = None
LDCONSOLE_LOGGED = False
AUTO_SCAN_LOGGED = False

SCAN_EXCLUDE_DIRS = {
    "windows",
    "programdata",
    "perfLogs".lower(),
    "system volume information",
    "$recycle.bin",
    "recovery",
    "msocache",
    "intel",
    "amd",
}

SCAN_CACHE = {}

# License polling (seconds). Smaller = faster pause propagation.
LICENSE_POLL_SECONDS = 3

# Suppress noisy torch warnings about pin_memory with no accelerator.
warnings.filterwarnings(
    "ignore",
    message=".*pin_memory.*no accelerator.*",
    category=UserWarning,
)


def ensure_cv2():
    global cv2, np
    if cv2 is None:
        import cv2 as _cv2
        cv2 = _cv2
    if np is None:
        import numpy as _np
        np = _np


def ensure_easyocr():
    global easyocr
    if easyocr is None:
        import easyocr as _easyocr
        easyocr = _easyocr


def ensure_adb_client():
    global AdbClient
    if AdbClient is None:
        from ppadb.client import Client as _AdbClient
        AdbClient = _AdbClient


def available_drives():
    drives = []
    for letter in string.ascii_uppercase:
        drive = f"{letter}:\\"
        if os.path.exists(drive):
            drives.append(drive)
    return drives


def scan_for_exe(filename, max_depth=6):
    """Best-effort scan to locate an executable anywhere on user drives."""
    global AUTO_SCAN_LOGGED
    key = filename.lower()
    if key in SCAN_CACHE:
        return SCAN_CACHE[key]

    if not AUTO_SCAN_LOGGED:
        debug("Searching system for emulator tools (one-time scan)...")
        AUTO_SCAN_LOGGED = True

    filename_lower = filename.lower()
    for drive in available_drives():
        root_depth = drive.rstrip("\\").count(os.sep)
        for dirpath, dirnames, filenames in os.walk(drive, onerror=lambda _: None):
            depth = dirpath.count(os.sep) - root_depth
            if depth > max_depth:
                dirnames[:] = []
                continue
            dirnames[:] = [d for d in dirnames if d.lower() not in SCAN_EXCLUDE_DIRS]
            for f in filenames:
                if f.lower() == filename_lower:
                    found = os.path.join(dirpath, f)
                    SCAN_CACHE[key] = found
                    return found

    SCAN_CACHE[key] = None
    return None


def resolve_adb():
    """Resolve adb executable path from PATH, env, or common LDPlayer/SDK locations."""
    global ADB_BIN, ADB_SOURCE
    if ADB_BIN:
        return ADB_BIN

    env_path = os.environ.get("ADB_PATH") or os.environ.get("ADB_BIN")
    if env_path:
        if os.path.isdir(env_path):
            candidate = os.path.join(env_path, "adb.exe")
            if os.path.isfile(candidate):
                ADB_BIN = candidate
                ADB_SOURCE = "ADB_PATH"
                return ADB_BIN
        elif os.path.isfile(env_path):
            ADB_BIN = env_path
            ADB_SOURCE = "ADB_PATH"
            return ADB_BIN

    path = shutil.which("adb")
    if path:
        ADB_BIN = path
        ADB_SOURCE = "PATH"
        return ADB_BIN

    for base in [os.environ.get("ANDROID_HOME"), os.environ.get("ANDROID_SDK_ROOT")]:
        if base:
            candidate = os.path.join(base, "platform-tools", "adb.exe")
            if os.path.isfile(candidate):
                ADB_BIN = candidate
                ADB_SOURCE = "ANDROID_SDK"
                return ADB_BIN

    for base in [os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)"), os.environ.get("LOCALAPPDATA")]:
        if not base:
            continue
        for rel in [
            r"LDPlayer\LDPlayer9\adb.exe",
            r"LDPlayer\LDPlayer4\adb.exe",
            r"LDPlayer\adb.exe",
        ]:
            candidate = os.path.join(base, rel)
            if os.path.isfile(candidate):
                ADB_BIN = candidate
                ADB_SOURCE = "LDPlayer"
                return ADB_BIN

    ldconsole = resolve_ldconsole()
    if ldconsole:
        candidate = os.path.join(os.path.dirname(ldconsole), "adb.exe")
        if os.path.isfile(candidate):
            ADB_BIN = candidate
            ADB_SOURCE = "LDPlayer"
            return ADB_BIN

    for drive in ["C:\\", "D:\\", "E:\\"]:
        for rel in [
            r"LDPlayer\LDPlayer9\adb.exe",
            r"LDPlayer\LDPlayer4\adb.exe",
            r"LDPlayer\adb.exe",
            r"LDPlayer9\adb.exe",
            r"LDPlayer4\adb.exe",
        ]:
            candidate = os.path.join(drive, rel)
            if os.path.isfile(candidate):
                ADB_BIN = candidate
                ADB_SOURCE = "Drive"
                return ADB_BIN

    scanned = scan_for_exe("adb.exe", max_depth=6)
    if scanned:
        ADB_BIN = scanned
        ADB_SOURCE = "Scan"
        return ADB_BIN

    return None


def resolve_ldconsole():
    """Resolve LDPlayer console executable (ldconsole.exe)."""
    global LDCONSOLE_BIN, LDCONSOLE_SOURCE
    if LDCONSOLE_BIN:
        return LDCONSOLE_BIN

    env_path = os.environ.get("LDPLAYER_DIR") or os.environ.get("LDPLAYER_HOME") or os.environ.get("LDPLAYER_PATH")
    if env_path:
        if os.path.isdir(env_path):
            candidate = os.path.join(env_path, "ldconsole.exe")
            if os.path.isfile(candidate):
                LDCONSOLE_BIN = candidate
                LDCONSOLE_SOURCE = "LDPLAYER_DIR"
                return LDCONSOLE_BIN
        elif os.path.isfile(env_path):
            LDCONSOLE_BIN = env_path
            LDCONSOLE_SOURCE = "LDPLAYER_DIR"
            return LDCONSOLE_BIN

    for base in [os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)"), os.environ.get("LOCALAPPDATA")]:
        if not base:
            continue
        for rel in [
            r"LDPlayer\LDPlayer9\ldconsole.exe",
            r"LDPlayer\LDPlayer4\ldconsole.exe",
            r"LDPlayer\ldconsole.exe",
            r"ldplayer\ldconsole.exe",
        ]:
            candidate = os.path.join(base, rel)
            if os.path.isfile(candidate):
                LDCONSOLE_BIN = candidate
                LDCONSOLE_SOURCE = "LDPlayer"
                return LDCONSOLE_BIN

    for drive in ["C:\\", "D:\\", "E:\\"]:
        for rel in [
            r"LDPlayer\LDPlayer9\ldconsole.exe",
            r"LDPlayer\LDPlayer4\ldconsole.exe",
            r"LDPlayer\ldconsole.exe",
            r"LDPlayer9\ldconsole.exe",
            r"LDPlayer4\ldconsole.exe",
        ]:
            candidate = os.path.join(drive, rel)
            if os.path.isfile(candidate):
                LDCONSOLE_BIN = candidate
                LDCONSOLE_SOURCE = "Drive"
                return LDCONSOLE_BIN

    scanned = scan_for_exe("ldconsole.exe", max_depth=6)
    if scanned:
        LDCONSOLE_BIN = scanned
        LDCONSOLE_SOURCE = "Scan"
        return LDCONSOLE_BIN

    return None


def get_machine_id():
    """Stable id for this machine so one license = one machine (shared copy needs new purchase)."""
    raw = f"{platform.node()}-{platform.system()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]

def list_adb_devices():
    """Return a list of (serial, state, port) from `adb devices`."""
    adb = resolve_adb()
    if not adb:
        return None, "adb not found on PATH"

    announce_adb_path()
    subprocess.run([adb, "start-server"], capture_output=True, text=True, check=False)

    result = subprocess.run([adb, "devices"], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip()
        return None, err or "adb devices failed"

    devices = []
    lines = (result.stdout or "").splitlines()
    for line in lines[1:]:  # skip header
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        serial, state = parts[0], parts[1]
        port = _port_from_serial(serial)
        devices.append((serial, state, port))

    return devices, None


def list_adb_devices_via_ppadb():
    """Return a list of (serial, state, port) via ADB server (ppadb)."""
    try:
        ensure_adb_client()
        client = AdbClient(host="127.0.0.1", port=5037)
        devices = client.devices()
    except Exception as e:
        return None, str(e)

    results = []
    for dev in devices:
        serial = dev.serial
        try:
            state = dev.get_state()
        except Exception:
            state = "device"
        port = _port_from_serial(serial)
        results.append((serial, state, port))

    return results, None


def _run_ldconsole(*args):
    exe = resolve_ldconsole()
    if not exe:
        return None, "", "ldconsole not found"
    announce_ldconsole_path()
    result = subprocess.run([exe, *args], capture_output=True, text=True, check=False)
    return result.returncode, (result.stdout or "").strip(), (result.stderr or "").strip()


def _port_from_serial(serial):
    if serial.startswith("emulator-"):
        return serial.split("-")[-1]
    if ":" in serial:
        return serial.split(":")[-1]
    return None


def _extract_port_from_text(text):
    adb_match = re.search(r"adb[^0-9]{0,12}(\d{4,5})", text, re.IGNORECASE)
    if adb_match:
        return int(adb_match.group(1))
    for match in re.findall(r"\b(\d{4,5})\b", text):
        try:
            num = int(match)
        except ValueError:
            continue
        if 5550 <= num <= 5600:
            return num
    return None


def list_ldplayer_instances():
    """Return a list of LDPlayer instances with best-effort port detection."""
    code, out, err = _run_ldconsole("list2")
    if code is None or not out:
        code, out, err = _run_ldconsole("list")

    if code is None:
        return None, err or "ldconsole not found"
    if code != 0 or not out:
        return None, err or "ldconsole returned no data"

    running_ids = set()
    r_code, r_out, _ = _run_ldconsole("runninglist")
    if r_code == 0 and r_out:
        for match in re.findall(r"\d+", r_out):
            try:
                running_ids.add(int(match))
            except ValueError:
                continue

    instances = []
    for line in out.splitlines():
        raw = line.strip()
        if not raw:
            continue
        fields = [f.strip() for f in raw.split(",")]
        if fields and not fields[0].isdigit():
            continue
        idx = int(fields[0]) if fields and fields[0].isdigit() else None
        name = fields[1] if len(fields) > 1 else None
        port = _extract_port_from_text(raw)
        running = idx in running_ids if running_ids else None
        instances.append(
            {
                "index": idx,
                "name": name,
                "port": port,
                "running": running,
                "raw": raw,
            }
        )

    if running_ids:
        running_instances = [i for i in instances if i["running"]]
        if running_instances:
            instances = running_instances

    return instances, None


def wait_for_adb_devices(poll_interval=3):
    """Keep checking for ADB devices until at least one is found."""
    first_notice = True
    while True:
        devices, err = list_adb_devices()
        if err:
            log(f"ADB check failed: {err}", "error", show_time=False)
        elif devices:
            return devices
        if first_notice:
            log("No ADB devices detected. Waiting for LDPlayer to launch... (Ctrl+C to cancel)", "wait", show_time=False)
            first_notice = False
        time.sleep(poll_interval)


def validate_license(key, timeout=10):
    """Check key with server. Returns: 'valid' | 'paused' | 'invalid'. Paused = Kaptaan stopped usage."""
    status, _ = check_license_status(timeout=timeout)
    if status == "error":
        return "invalid"
    return status


def _extract_message(body_text):
    try:
        data = json.loads(body_text)
    except Exception:
        return None
    if isinstance(data, dict):
        return data.get("message") or data.get("error")
    return None


def check_license_status(timeout=10):
    """Return (status, message). Status: valid | paused | invalid | error."""
    key = get_machine_id()
    if not key or not key.strip():
        return "invalid", "Missing machine ID."
    key = key.strip()
    try:
        url = f"{LICENSE_CHECK_URL}?key={key}&machine_id={get_machine_id()}"
        req = urlopen(url, timeout=timeout)
        body = req.read().decode("utf-8", "ignore")
        msg = _extract_message(body)
        return "valid", msg
    except HTTPError as e:
        try:
            body = e.read().decode("utf-8", "ignore")
        except Exception:
            body = ""
        msg = _extract_message(body)
        if e.code == 503:
            return "paused", msg or "Kaptaan has stopped users from using."
        return "invalid", msg or f"Authorization failed (HTTP {e.code})."
    except (URLError, OSError) as e:
        return "error", str(e)


def get_disclaimer_status(timeout=10):
    """Return (accepted, version, text) or raise on error."""
    try:
        url = f"{LICENSE_BASE_URL}/disclaimer/status?machine_id={get_machine_id()}"
        req = urlopen(url, timeout=timeout)
        body = req.read().decode("utf-8", "ignore")
        data = json.loads(body)
        accepted = bool(data.get("accepted"))
        version = data.get("version") or DISCLAIMER_VERSION
        text = data.get("text") or DEFAULT_DISCLAIMER_TEXT
        return accepted, version, text
    except (HTTPError, URLError, OSError) as e:
        raise RuntimeError(str(e))
    except Exception as e:
        raise RuntimeError(f"Disclaimer check failed: {e}")


def accept_disclaimer(version, timeout=10):
    payload = json.dumps(
        {"machine_id": get_machine_id(), "version": version}
    ).encode("utf-8")
    req = Request(
        f"{LICENSE_BASE_URL}/disclaimer/accept",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    urlopen(req, timeout=timeout)


def get_payment_info(timeout=10):
    """Fetch payment instructions from the license server."""
    try:
        url = f"{LICENSE_BASE_URL}/payment_info"
        req = urlopen(url, timeout=timeout)
        body = req.read().decode("utf-8", "ignore")
        data = json.loads(body)
        instructions = normalize_multiline(data.get("instructions") or "")
        pay_url = (data.get("url") or "").strip()
        return instructions, pay_url
    except Exception as e:
        raise RuntimeError(f"Payment info unavailable: {e}")


def require_disclaimer_acceptance():
    try:
        accepted, version, text = get_disclaimer_status(timeout=10)
    except RuntimeError as e:
        print_section("Disclaimer")
        print_block([f"Could not reach license server for disclaimer check: {e}"])
        raise SystemExit(1)

    if accepted:
        return

    print_section("Disclaimer")
    text = normalize_multiline(text)
    print_block([line for line in text.strip().splitlines() if line.strip()])
    print()
    response = input("  Type YES to accept and continue (or anything else to exit): ").strip()
    if response != "YES":
        print_block(["You did not accept the terms. Exiting now."])
        raise SystemExit(1)

    try:
        accept_disclaimer(version, timeout=10)
    except Exception as e:
        print_block([f"Could not record acceptance: {e}"])
        raise SystemExit(1)


def log(msg, level="info", show_time=True):
    """User-friendly logging with optional timestamp and clear prefix."""
    ts = datetime.now().strftime("%H:%M:%S")
    icons = {
        "ok": "[OK]",
        "info": "[*]",
        "retry": "[...]",
        "done": "[DONE]",
        "wait": "[WAIT]",
        "error": "[!]",
        "debug": "[DBG]",
        "note": "[i]",
    }
    icon = icons.get(level, "[*]")
    tag = getattr(LOG_CONTEXT, "tag", "")
    tag_text = f"[{tag}] " if tag else ""
    if show_time:
        print(f"  {ts}  {icon}  {tag_text}{msg}")
    else:
        print(f"  {icon}  {tag_text}{msg}")


def debug(msg):
    if VERBOSE:
        log(msg, "debug")


def announce_adb_path():
    global ADB_LOGGED
    if ADB_LOGGED:
        return
    adb = resolve_adb()
    if adb:
        source = ADB_SOURCE or "unknown"
        debug(f"Using adb: {adb} ({source})")
        ADB_LOGGED = True


def announce_ldconsole_path():
    global LDCONSOLE_LOGGED
    if LDCONSOLE_LOGGED:
        return
    exe = resolve_ldconsole()
    if exe:
        source = LDCONSOLE_SOURCE or "unknown"
        debug(f"Using LDPlayer console: {exe} ({source})")
        LDCONSOLE_LOGGED = True


def print_banner(lines=None):
    lines = lines or DEFAULT_BANNER_LINES
    print()
    for line in lines:
        print(f"  {line}")


def print_section(title):
    print(f"\n  {title}")
    print("  ----------------------------------------")


def print_block(lines):
    for line in lines:
        print(f"  {line}")


def start_spinner(message):
    stop_event = threading.Event()

    def _spin():
        frames = "|/-\\"
        i = 0
        while not stop_event.is_set():
            sys.stdout.write(f"\r  [*]  {message} {frames[i % len(frames)]}")
            sys.stdout.flush()
            time.sleep(0.2)
            i += 1
        sys.stdout.write("\r" + " " * (len(message) + 8) + "\r")
        sys.stdout.flush()

    t = threading.Thread(target=_spin, daemon=True)
    t.start()
    return stop_event


def stop_spinner(stop_event, final_message=None):
    stop_event.set()
    time.sleep(0.1)
    if final_message:
        print(f"  [OK]  {final_message}")


def close_splash():
    """Close PyInstaller splash screen if present."""
    try:
        import pyi_splash
        pyi_splash.close()
    except Exception:
        pass


def pause_on_exit():
    """Keep console open until the user closes it."""
    try:
        if sys.stdin and sys.stdin.isatty():
            print("\n  Press Enter to close this window.")
            try:
                input()
            except KeyboardInterrupt:
                return
    except Exception:
        pass


def install_excepthook():
    def _hook(exc_type, exc, tb):
        print_section("Unexpected error")
        print_block(["The bot stopped due to an unexpected error. Details:"])
        formatted = "".join(traceback.format_exception(exc_type, exc, tb)).strip().splitlines()
        for line in formatted:
            print(f"  {line}")
        pause_on_exit()
    sys.excepthook = _hook


class RestartRequested(Exception):
    """Raised when user requests a restart via shortcut key."""


RESTART_EVENT = threading.Event()


def request_restart():
    RESTART_EVENT.set()


def clear_restart_request():
    RESTART_EVENT.clear()


def restart_requested():
    return RESTART_EVENT.is_set()


def flush_key_buffer():
    try:
        import msvcrt
        while msvcrt.kbhit():
            msvcrt.getch()
    except Exception:
        pass


def start_restart_listener():
    stop_event = threading.Event()

    def _listen():
        try:
            import msvcrt
        except Exception:
            return
        while not stop_event.is_set():
            if msvcrt.kbhit():
                ch = msvcrt.getch()
                if ch in (b"r", b"R"):
                    request_restart()
            time.sleep(0.1)

    t = threading.Thread(target=_listen, daemon=True)
    t.start()
    return stop_event


LOG_CONTEXT = threading.local()


def set_log_tag(tag):
    LOG_CONTEXT.tag = tag


def clear_log_tag():
    if hasattr(LOG_CONTEXT, "tag"):
        del LOG_CONTEXT.tag


def resource_path(relative_path):
    """Get absolute path to a bundled resource (PyInstaller) or local file."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, relative_path)


def normalize_multiline(text):
    if not text:
        return ""
    return str(text).replace("\\n", "\n").replace("\\r", "\r").strip()


HEADER_CACHE = {"lines": None, "ts": 0.0}
HEADER_CACHE_TTL = 60


def get_header_info(timeout=5):
    now = time.time()
    cached = HEADER_CACHE.get("lines")
    if cached and (now - HEADER_CACHE.get("ts", 0.0)) < HEADER_CACHE_TTL:
        return cached

    url = f"{LICENSE_BASE_URL}/ui_header"
    try:
        req = urlopen(url, timeout=timeout)
        if req.status != 200:
            return None
        payload = json.loads(req.read().decode("utf-8"))
        lines = payload.get("lines")
        text = payload.get("text")
        result = []
        if isinstance(lines, list) and lines:
            for line in lines:
                line = normalize_multiline(line)
                if line:
                    result.append(line)
        elif text:
            for line in normalize_multiline(text).splitlines():
                if line.strip():
                    result.append(line)
        if result:
            HEADER_CACHE["lines"] = result
            HEADER_CACHE["ts"] = now
            return result
    except Exception:
        return None
    return None


def _has_model_files(path):
    if not path or not os.path.isdir(path):
        return False
    for name in os.listdir(path):
        lower = name.lower()
        if lower.endswith(".pth") or lower.endswith(".pt"):
            return True
    return False


def resolve_easyocr_model_dir():
    """Find a bundled or local EasyOCR model directory if present."""
    candidates = []
    bundled = os.path.join(resource_path("easyocr_models"), "model")
    candidates.append(bundled)

    local = os.path.join(os.path.dirname(os.path.abspath(__file__)), "easyocr_models", "model")
    if local not in candidates:
        candidates.append(local)

    home = os.path.expanduser("~/.EasyOCR/model")
    if home not in candidates:
        candidates.append(home)

    for path in candidates:
        if _has_model_files(path):
            return path
    return None


def create_ocr_reader():
    """Initialize EasyOCR without noisy console output."""
    ensure_easyocr()
    model_dir = resolve_easyocr_model_dir()
    base_kwargs = {}
    if model_dir:
        base_kwargs["model_storage_directory"] = model_dir
        base_kwargs["download_enabled"] = False

    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        for kwargs in ({"gpu": False, "verbose": False}, {"gpu": False}, {}):
            try:
                merged = dict(base_kwargs)
                merged.update(kwargs)
                return easyocr.Reader(['en'], **merged)
            except TypeError:
                continue


class LudoBot:
    def __init__(
        self,
        device_serial,
        resolution=None,
        popup_template_path='popup_template.png',
        club_code='#bcaf9533',
        external_stop_event=None,
    ):
        resolution = resolution or LDPLAYER_RESOLUTION
        self.device_serial = device_serial
        ensure_adb_client()
        self.client = AdbClient(host="127.0.0.1", port=5037)
        self.device = None
        self.last_auth_check = 0
        self.stop_requested = False
        self.stop_message = None
        self.external_stop_event = external_stop_event
        self.connect_device(device_serial)
        self.resolution = resolution
        self.scale_x = resolution[0] / 1080
        self.scale_y = resolution[1] / 1920
        spinner = start_spinner("Loading OCR engine (first run may take a while)")
        try:
            self.reader = create_ocr_reader()
            stop_spinner(spinner, "OCR engine ready.")
        except Exception:
            stop_spinner(spinner)
            raise
        
        # ROIs for 1080x1920 — used as-is when LDPlayer matches LDPLAYER_RESOLUTION
        self.input_field_roi = (154, 827, 694, 896)
        self.search_button_roi = (742, 833, 957, 887)
        self.member_roi = (618, 1151, 710, 1190)
        self.action_button_roi = (786, 1113, 919, 1172)
        self.popup_roi = (300, 800, 780, 1000)
        self.confirm_coord = (49, 677)
        self.club_code = club_code
        self.debug_counter = 0  # For debugging cropped images
        self.first_search = True  # Flag to input code only on first search
        
        # Load popup template
        ensure_cv2()
        popup_template_path = resource_path(popup_template_path)
        self.popup_template = cv2.imread(popup_template_path)
        if self.popup_template is None:
            raise FileNotFoundError(f"Popup template not found at {popup_template_path}")
    
    def _run_adb(self, *args):
        adb = resolve_adb()
        if not adb:
            return None, "", "adb not found on PATH"
        announce_adb_path()
        result = subprocess.run(
            [adb, *args],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode, (result.stdout or "").strip(), (result.stderr or "").strip()

    def _ensure_adb_server(self):
        global ADB_MISSING_LOGGED
        code, out, err = self._run_adb("start-server")
        if code is None:
            if not ADB_MISSING_LOGGED:
                debug("adb not found on PATH; skipping adb start-server.")
                ADB_MISSING_LOGGED = True
            return
        if code != 0:
            log(f"adb start-server failed: {err or out}", "error")
        else:
            debug("adb server is running.")

    def _log_adb_devices(self, label="ADB devices"):
        code, out, err = self._run_adb("devices")
        if code is None:
            debug("adb not available; skipping adb devices.")
            return
        if code != 0:
            log(f"adb devices failed: {err or out}", "error")
            return
        lines = out.splitlines()
        if not lines:
            debug(f"{label}: (no output)")
            return
        for line in lines:
            debug(f"{label}: {line}")

    def connect_device(self, serial):
        """Connect to the device using the provided serial or port."""
        log("Connecting to emulator via ADB...", "info")
        self._ensure_adb_server()
        self._log_adb_devices("ADB devices (before connect)")

        # Extract host and port from the serial
        if ":" in serial:
            host, port = serial.split(":")
            port = int(port)
        elif serial.isdigit():
            host = "127.0.0.1"
            port = int(serial)
        elif serial.startswith("emulator-"):
            port = int(serial.split("-")[-1])
            host = "127.0.0.1"
        else:
            raise ValueError(f"Invalid serial format: {serial}")

        # Connect to the emulator
        try:
            self.client.remote_connect(host, port)
            log(f"Connected to emulator at {host}:{port}", "ok")
        except Exception as e:
            # Do not fail fast: device may already be connected.
            log(f"ADB connect failed (may already be connected): {e}", "error")

        self._log_adb_devices("ADB devices (after connect)")

        # Verify the device is connected
        devices = self.client.devices()
        if not devices:
            raise ConnectionError("No devices connected. Ensure LDPlayer is running and ADB is enabled.")

        # Debug: Log all connected devices
        debug("Listing all connected devices...")
        for device in devices:
            debug(f"Found device: {device.serial}")

        # Match the user-provided serial to a connected device
        candidates = set()
        if serial.startswith("emulator-"):
            port = serial.split("-")[-1]
            candidates.add(serial)
            candidates.add(f"127.0.0.1:{port}")
        elif serial.isdigit():
            candidates.add(f"127.0.0.1:{serial}")
            candidates.add(f"emulator-{serial}")
        elif ":" in serial:
            host, port = serial.split(":")
            candidates.add(serial)
            candidates.add(f"emulator-{port}")
        else:
            raise ValueError(f"Invalid serial format: {serial}")

        debug(f"Serial candidates: {sorted(candidates)}")

        for device in devices:
            debug(f"Comparing candidates to device serial '{device.serial}'")
            if device.serial in candidates:
                self.device = device
                log(f"Connected to device: {device.serial}", "ok")
                return

        raise ConnectionError(
            "Failed to connect to device.\n\n"
            "Try this:\n"
            "  1. Open LDPlayer and ensure it is running.\n"
            "  2. In LDPlayer: Settings > Other > Enable ADB (port matching your input).\n"
            "  3. In Command Prompt run once:  adb connect 127.0.0.1:<your-port>\n"
            "  4. Check:  adb devices   (you should see 127.0.0.1:<your-port>  device).\n"
            f"  Current ADB devices: {[device.serial for device in devices]}"
        )
    
    def connect_to_emulator(self, serial):
        """Connect to the LDPlayer emulator using ADB."""
        ensure_adb_client()
        client = AdbClient(host="127.0.0.1", port=5037)

        try:
            # Handle different serial formats
            if ":" in serial:  # Host:Port format (e.g., 127.0.0.1:5555)
                host, port = serial.split(":")
                port = int(port)
            elif serial.startswith("emulator-"):
                port = int(serial.split("-")[-1])
                host = "127.0.0.1"
            else:  # Assume it's just a port number
                port = int(serial)
                host = "127.0.0.1"

            # Attempt to connect to the emulator
            client.remote_connect(host, port)
            log(f"Connected to emulator: {serial}", "ok")
        except Exception as e:
            log(f"Failed to connect to emulator: {e}", "error")

        # List connected devices
        devices = client.devices()
        if not devices:
            log("No devices connected.", "error")
        else:
            for device in devices:
                log(f"Connected device: {device.serial}", "info")

    def scale_coord(self, x, y):
        return int(x * self.scale_x), int(y * self.scale_y)
    
    def tap(self, x, y):
        jitter_x = random.randint(-2, 2)
        jitter_y = random.randint(-2, 2)
        x += jitter_x
        y += jitter_y
        x, y = self.scale_coord(x, y)
        self.device.shell(f"input tap {x} {y}")
    
    def input_text(self, text):
        self.device.shell(f"input text '{text}'")
    
    def capture_screen(self):
        ensure_cv2()
        result = self.device.screencap()
        img = np.frombuffer(result, dtype=np.uint8)
        img = cv2.imdecode(img, cv2.IMREAD_COLOR)
        return img
    
    def read_text(self, roi, allowlist=None):
        img = self.capture_screen()
        x1, y1, x2, y2 = roi
        x1, y1 = self.scale_coord(x1, y1)
        x2, y2 = self.scale_coord(x2, y2)
        cropped = img[y1:y2, x1:x2]
        cropped = cv2.cvtColor(cropped, cv2.COLOR_BGR2GRAY)  # Convert to grayscale for better OCR
        # cv2.imwrite(f'debug_cropped_{self.debug_counter}.png', cropped)  # Debug: save cropped image (disabled)
        self.debug_counter += 1
        text = self.reader.readtext(cropped, detail=0, allowlist=allowlist)
        return ' '.join(text).strip()
    
    def detect_popup(self):
        img = self.capture_screen()
        result = cv2.matchTemplate(img, self.popup_template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, _ = cv2.minMaxLoc(result)
        return max_val > 0.60  # threshold

    def _maybe_check_server(self, force=False):
        if self.stop_requested:
            return
        now = time.time()
        if force or now - self.last_auth_check >= LICENSE_POLL_SECONDS:
            status, message = check_license_status(timeout=5)
            self.last_auth_check = now
            if status == "paused":
                self.stop_requested = True
                self.stop_message = message or "Kaptaan has stopped users from using."
            elif status == "invalid":
                self.stop_requested = True
                self.stop_message = message or "Authorization revoked."
            elif status == "error":
                debug(f"License check failed: {message}")

    def _ensure_running(self):
        if restart_requested():
            log("Restart requested by user.", "info", show_time=False)
            raise RestartRequested()
        if self.external_stop_event and self.external_stop_event.is_set():
            self.stop_requested = True
            if not self.stop_message:
                self.stop_message = "Stopped by user."
        if self.stop_requested:
            print_section("Access paused")
            if self.stop_message:
                print_block([self.stop_message])
            log("Stopping bot now.", "error", show_time=False)
            raise SystemExit(1)

    def run(self):
        # Pre-calculate centers
        search_center_x = (self.search_button_roi[0] + self.search_button_roi[2]) // 2
        search_center_y = (self.search_button_roi[1] + self.search_button_roi[3]) // 2
        action_center_x = (self.action_button_roi[0] + self.action_button_roi[2]) // 2
        action_center_y = (self.action_button_roi[1] + self.action_button_roi[3]) // 2

        self._maybe_check_server(force=True)
        self._ensure_running()

        while True:
            self._maybe_check_server()
            self._ensure_running()
            if self.first_search:
                input_center_x = (self.input_field_roi[0] + self.input_field_roi[2]) // 2
                input_center_y = (self.input_field_roi[1] + self.input_field_roi[3]) // 2
                log("Entering club code and searching...", "info")
                self.tap(input_center_x, input_center_y)
                self.device.shell("input keyevent KEYCODE_DEL")
                self.input_text(self.club_code)
                time.sleep(0.2)
                self.tap(search_center_x, search_center_y)
                time.sleep(0.1)
                self.tap(search_center_x, search_center_y)
                time.sleep(0.2)
                self.first_search = False
            else:
                log("Searching again...", "retry")
                self.tap(search_center_x, search_center_y)
            
            members = self.read_text(self.member_roi, allowlist='0123456789/')
            action = self.read_text(self.action_button_roi)
            
            if "30/30" in members:
                log("Club is full. Trying again in a moment.", "retry")
                continue
            
            if action in ["Private", "Request"]:
                log("Club is private or needs request. Trying another search.", "retry")
                continue
            
            if "Join" in action and "30/30" not in members:
                log("Join button found! Attempting to join club...", "info")
                self.tap(action_center_x, action_center_y)
                time.sleep(1.2)
                if self.detect_popup():
                    log("Club not found or error popup. Searching again.", "retry")
                    continue
                else:
                    self.tap(self.confirm_coord[0], self.confirm_coord[1])
                    log("You have joined the club!", "done")
                    while True:
                        self._maybe_check_server()
                        self._ensure_running()
                        time.sleep(0.5)
                        search_text = self.read_text(self.search_button_roi)
                        if "Search" in search_text:
                            log("Back at search screen. Tapping confirm and starting over.", "info")
                            self.tap(self.confirm_coord[0], self.confirm_coord[1])
                            self.first_search = True
                            break
                    if "Search" in search_text:
                        continue


def parse_csv_list(value):
    if not value:
        return []
    parts = re.split(r"[,\s]+", value.strip())
    return [p for p in parts if p]


def normalize_club_code(code):
    if not code:
        return ""
    code = code.strip()
    if not code:
        return ""
    if not code.startswith("#"):
        code = "#" + code
    return code


def prompt_bot_count():
    while True:
        print_block(["How many bots do you want to run?"])
        response = input("  Number of bots (default 1): ").strip()
        if not response:
            return 1
        try:
            count = int(response)
        except ValueError:
            log("Please enter a valid number (e.g., 1, 2, 3).", "error", show_time=False)
            continue
        if count < 1:
            log("Please enter a number greater than 0.", "error", show_time=False)
            continue
        return count


def prompt_serial(default_serial):
    while True:
        print_block(["Enter your ADB PORT shown above. Examples: 5555 or 5556"])
        serial = input("  ADB port: ").strip()
        if not serial:
            serial = default_serial
        if serial:
            return serial
        log("No ADB port provided. Please try again.", "error", show_time=False)


def prompt_club():
    print_section("Target club")
    print_block(["Enter your TARGETED CLUB CODE."])
    print_block(["Example: bcaf9533 or #bcaf9533."])
    while True:
        club = input("  Club code: ").strip()
        if club:
            return normalize_club_code(club)
        log("No club code provided. Please enter a club code.", "error", show_time=False)


class BotWorker:
    def __init__(self, serial, club, stagger_seconds=0.0):
        self.serial = serial
        self.club = club
        self.stagger_seconds = stagger_seconds
        self.stop_event = threading.Event()
        self.thread = None
        self.status = "stopped"
        self.last_error = None
        self._lock = threading.Lock()

    def _set_status(self, status):
        with self._lock:
            self.status = status

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self._set_status("starting")
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def request_stop(self):
        self.stop_event.set()

    def join(self, timeout=None):
        if self.thread:
            self.thread.join(timeout=timeout)

    def is_alive(self):
        return bool(self.thread and self.thread.is_alive())

    def _run(self):
        set_log_tag(self.serial)
        try:
            log(f"Starting bot for device {self.serial}...", "info", show_time=False)
            bot = LudoBot(
                self.serial,
                resolution=LDPLAYER_RESOLUTION,
                popup_template_path="popup_template.png",
                club_code=self.club,
                external_stop_event=self.stop_event,
            )
            self._set_status("running")
            bot.run()
            self._set_status("stopped")
        except RestartRequested:
            self._set_status("restart")
        except SystemExit:
            self._set_status("stopped")
        except Exception:
            self._set_status("crashed")
            self.last_error = traceback.format_exc()
            log("Worker crashed. Details below:", "error", show_time=False)
            formatted = self.last_error.strip().splitlines()
            for line in formatted:
                print(f"  {line}")
        finally:
            clear_log_tag()


class MultiBotController:
    def __init__(self, serials, clubs, stagger_seconds=0.0):
        self.workers = {}
        self.stagger_seconds = stagger_seconds
        self.input_lock = threading.Lock()
        for serial, club in zip(serials, clubs):
            self.workers[serial] = BotWorker(serial, club, stagger_seconds=stagger_seconds)

    def _find_worker_key(self, serial_input):
        if serial_input in self.workers:
            return serial_input
        port = _port_from_serial(serial_input)
        if serial_input.isdigit():
            port = serial_input
        if port:
            for key in self.workers:
                if _port_from_serial(key) == port:
                    return key
        return None

    def start_all(self):
        for i, worker in enumerate(self.workers.values()):
            worker.start()
            if self.stagger_seconds and self.stagger_seconds > 0 and i < len(self.workers) - 1:
                time.sleep(self.stagger_seconds)

    def any_alive(self):
        return any(worker.is_alive() for worker in self.workers.values())

    def stop_all(self):
        for worker in self.workers.values():
            worker.request_stop()

    def join_all(self, timeout=5):
        for worker in self.workers.values():
            worker.join(timeout=timeout)

    def stop_bot(self, serial_input):
        key = self._find_worker_key(serial_input)
        if not key:
            log(f"Bot not found for serial/port: {serial_input}", "error", show_time=False)
            return False
        worker = self.workers[key]
        worker.request_stop()
        log(f"Stop requested for {key}.", "note", show_time=False)
        return True

    def restart_bot(self, serial_input, new_club):
        key = self._find_worker_key(serial_input)
        if not key:
            log(f"Bot not found for serial/port: {serial_input}", "error", show_time=False)
            return False
        worker = self.workers[key]
        worker.request_stop()
        worker.join(timeout=10)
        worker.club = new_club
        worker.start()
        log(f"Restarted {key} with club {new_club}.", "ok", show_time=False)
        return True

    def print_status(self):
        print_section("Bot status")
        lines = []
        for key, worker in self.workers.items():
            status = worker.status
            club = worker.club
            lines.append(f"{key}  [{status}]  club {club}")
        print_block(lines)

    def control_prompt(self):
        if not self.input_lock.acquire(False):
            return
        try:
            print_section("Bot control")
            print_block([
                "Enter the bot serial/port you want to control.",
                "Leave club blank to stop only.",
                "Type 'list' to show all bots.",
            ])
            serial_input = input("  Bot serial/port: ").strip()
            if not serial_input:
                return
            if serial_input.lower() in ("list", "l"):
                self.print_status()
                return
            club = input("  New club code (blank = stop): ").strip()
            if not club:
                self.stop_bot(serial_input)
                return
            club = normalize_club_code(club)
            self.restart_bot(serial_input, club)
        finally:
            self.input_lock.release()


def start_multi_control_listener(controller):
    stop_event = threading.Event()

    def _listen():
        try:
            import msvcrt
        except Exception:
            return
        while not stop_event.is_set():
            if msvcrt.kbhit():
                ch = msvcrt.getch()
                if ch in (b"c", b"C"):
                    controller.control_prompt()
                elif ch in (b"l", b"L"):
                    controller.print_status()
                elif ch in (b"r", b"R"):
                    request_restart()
            time.sleep(0.1)

    t = threading.Thread(target=_listen, daemon=True)
    t.start()
    return stop_event


def run_multi_bots(serials, clubs, stagger_seconds=0.0):
    controller = MultiBotController(serials, clubs, stagger_seconds=stagger_seconds)
    controller.start_all()
    control_stop = start_multi_control_listener(controller)

    try:
        while controller.any_alive():
            if restart_requested():
                break
            time.sleep(0.5)
    except KeyboardInterrupt:
        log("Stopping all bots...", "error", show_time=False)
    finally:
        controller.stop_all()
        controller.join_all(timeout=5)
        if control_stop:
            control_stop.set()

    if restart_requested():
        return "restart"
    return "done"

if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(line_buffering=True)
        sys.stderr.reconfigure(line_buffering=True)
    except Exception:
        pass
    install_excepthook()
    atexit.register(pause_on_exit)
    parser = argparse.ArgumentParser(
        description="Ludo Titan Automation Bot - set LDPlayer to script's config, then run with your club code."
    )
    parser.add_argument("club_code", nargs="?", default=None, help="Club code to join (e.g. #bcaf9533)")
    parser.add_argument("--club", dest="club_code_opt", default=None, help="Club code (alternative to positional)")
    parser.add_argument("--serial", default=LDPLAYER_ADB_DEFAULT, help=f"ADB device (default: {LDPLAYER_ADB_DEFAULT})")
    parser.add_argument("--serials", default=None, help="Comma/space-separated ADB devices (multi-device mode)")
    parser.add_argument("--clubs", default=None, help="Comma/space-separated club codes (one or same count as --serials)")
    parser.add_argument("--stagger", type=float, default=0.2, help="Delay between starting devices (seconds)")
    parser.add_argument("--verbose", action="store_true", help="Show extra debug logs")
    args = parser.parse_args()
    VERBOSE = args.verbose

    header_lines = get_header_info(timeout=5)
    print_banner(header_lines)
    close_splash()
    log("Starting... preparing environment. Please wait.", "info", show_time=False)
    log("Welcome to the Ludo Titan Automation Bot!", "note", show_time=False)
    log("This bot requires a one-time payment to use.", "note", show_time=False)
    log("Please follow the instructions below to get started:", "note", show_time=False)

    # Disclaimer (required)
    require_disclaimer_acceptance()

    print_section("Step 1 of 3: Payment")
    print_block(["Make a payment using one of the following methods:"])
    payment_instructions = ""
    payment_url = ""
    try:
        instructions, pay_url = get_payment_info(timeout=10)
        payment_instructions = instructions
        payment_url = pay_url
        if instructions:
            print_block(instructions.splitlines())
        if pay_url:
            print_block([f"Or visit: {pay_url}"])
    except RuntimeError as e:
        print_block([str(e), "Please contact admin for payment details."])

    print_section("Step 2 of 3: Machine ID")
    print_block(["After payment, send your Machine ID and payment proof to the provided contact."])
    print_block([f"Your Machine ID: {get_machine_id()}"])

    print_section("Step 3 of 3: Authorization")
    print_block(["Once your Machine ID is authorized, you can use the bot."])

    # --- License check: every user must have a valid Machine ID ---
    machine_id = get_machine_id()
    log("Checking authorization...", "info")
    result, message = check_license_status(timeout=10)

    if result == "paused":
        print_section("Authorization paused")
        print_block([message or "Kaptaan has stopped users from using.", "Check back later."])
        raise SystemExit(1)
    elif result == "invalid":
        print_section("Authorization failed")
        if message:
            print_block([message])
        else:
            print_block([f"Unauthorized Machine ID: {machine_id}"])
        print_block(["To use this bot, please send your Machine ID and payment details to:"])
        if not payment_instructions and not payment_url:
            try:
                payment_instructions, payment_url = get_payment_info(timeout=10)
            except RuntimeError:
                payment_instructions, payment_url = "", ""
        if payment_instructions:
            print_block(payment_instructions.splitlines())
        if payment_url:
            print_block([f"Or visit: {payment_url}"])
        raise SystemExit(1)
    elif result == "error":
        print_section("Authorization error")
        print_block([f"Could not reach license server: {message}"])
        raise SystemExit(1)
    else:
        log("Authorization successful. You can now use the bot.", "ok")

    serials = parse_csv_list(args.serials)
    if serials:
        clubs_input = parse_csv_list(args.clubs)
        default_club = args.club_code_opt or args.club_code
        if clubs_input:
            if len(clubs_input) == 1:
                clubs = clubs_input * len(serials)
            elif len(clubs_input) == len(serials):
                clubs = clubs_input
            else:
                log("Club list must be 1 entry or match --serials count.", "error", show_time=False)
                raise SystemExit(1)
        elif default_club:
            clubs = [default_club] * len(serials)
        else:
            log("Missing club code. Provide --club or --clubs when using --serials.", "error", show_time=False)
            raise SystemExit(1)

        clubs = [normalize_club_code(c) for c in clubs]
        log(f"Starting multi-device mode for {len(serials)} devices.", "info", show_time=False)
        log("Shortcuts: C=control bot, L=list bots, R=restart/stop all.", "note", show_time=False)
        run_multi_bots(serials, clubs, stagger_seconds=args.stagger)
        raise SystemExit(0)

    while True:
        clear_restart_request()
        flush_key_buffer()
        # --- Ask for ADB port and club code ---
        print_section("Device setup")
        log("Checking ADB devices...", "info", show_time=False)
        devices = None
        err = None
        suggested_serial = None
        devices_source = None
    
        devices, err = list_adb_devices()
        if not devices:
            ppadb_devices, ppadb_err = list_adb_devices_via_ppadb()
            if ppadb_devices:
                devices = ppadb_devices
                devices_source = "ADB server"
                debug("Using ADB server device list.")
            elif ppadb_err:
                log(f"ADB server check failed: {ppadb_err}", "error", show_time=False)
            elif err and "adb not found" not in err.lower():
                log(f"ADB check failed: {err}", "error", show_time=False)
    
        if devices:
            if not devices_source:
                devices_source = "adb"
            log(f"Detected ADB devices ({devices_source}):", "note", show_time=False)
            ports = []
            for serial, state, port in devices:
                port_info = f" (port {port})" if port else ""
                print(f"   {serial}  [{state}]{port_info}")
                if port:
                    ports.append(str(port))
            if ports:
                unique_ports = sorted({int(p) for p in ports if str(p).isdigit()})
                if unique_ports:
                    log(f"ADB ports found: {', '.join(str(p) for p in unique_ports)}", "note", show_time=False)
            if len(devices) == 1:
                suggested_serial = devices[0][0]
        else:
            if err is None:
                devices = wait_for_adb_devices()
                if devices:
                    log("Detected ADB devices:", "note", show_time=False)
                    for serial, state, port in devices:
                        port_info = f" (port {port})" if port else ""
                        print(f"   {serial}  [{state}]{port_info}")
                    if len(devices) == 1:
                        suggested_serial = devices[0][0]
    
        instances, inst_err = list_ldplayer_instances()
        if instances:
            instances_with_port = [i for i in instances if i.get("port")]
            if instances_with_port:
                log("Detected LDPlayer instances:", "note", show_time=False)
                for inst in instances_with_port:
                    name = inst["name"] or (f"Instance {inst['index']}" if inst["index"] is not None else "Instance")
                    status = "running" if inst["running"] is True else "stopped" if inst["running"] is False else "unknown"
                    port_display = str(inst["port"])
                    print(f"   {name}  [{status}]  port {port_display}")
            else:
                debug("LDPlayer instances found but no ADB ports detected.")

        print()
        bot_count = prompt_bot_count()
        default_serial = suggested_serial or LDPLAYER_ADB_DEFAULT

        if bot_count > 1:
            serials = []
            clubs = []
            for i in range(bot_count):
                print_section(f"Bot {i + 1}")
                serial = prompt_serial(default_serial)
                log(f"Using serial: {serial}", "info", show_time=False)
                club = prompt_club()
                serials.append(serial)
                clubs.append(club)

            print_section("Summary")
            summary_lines = []
            for i, (serial, club) in enumerate(zip(serials, clubs), 1):
                summary_lines.append(f"Bot {i}:  Device {serial}  Club {club}")
            print_block(summary_lines)

            log("Starting automation. Press Ctrl+C to stop.", "info", show_time=False)
            log("Shortcuts: C=control bot, L=list bots, R=restart/stop all.", "note", show_time=False)
            result = run_multi_bots(serials, clubs, stagger_seconds=args.stagger)

            if result == "restart":
                log("Restarting setup...", "info", show_time=False)
                continue
            raise SystemExit(0)

        serial = prompt_serial(default_serial)
        log(f"Using serial: {serial}", "info", show_time=False)
        club = prompt_club()

        log("Preparing bot...", "info", show_time=False)
    
        bot = LudoBot(
            serial,
            resolution=LDPLAYER_RESOLUTION,
            popup_template_path="popup_template.png",
            club_code=club,
        )
    
        ocr_ready = bool(getattr(bot, "reader", None))
        print_section("Summary")
        print_block([
            f"Club code:  {club}",
            f"Device:     {serial}",
            f"Resolution: {LDPLAYER_RESOLUTION[0]}x{LDPLAYER_RESOLUTION[1]}",
            f"OCR status: {'ready' if ocr_ready else 'not ready'}",
        ])
        log("Starting automation. Press Ctrl+C to stop.", "info", show_time=False)
        log("Shortcut: Press R to restart setup.", "note", show_time=False)
        listener_stop = start_restart_listener()
        try:
            bot.run()
        except RestartRequested:
            log("Restarting setup...", "info", show_time=False)
            continue
        except KeyboardInterrupt:
            log("Stopped by user.", "error", show_time=False)
            raise SystemExit(0)
        finally:
            if listener_stop:
                listener_stop.set()
