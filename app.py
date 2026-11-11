import atexit
import json
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

PORT = 8765
HOST = "0.0.0.0"
BASE_DIR = Path(__file__).resolve().parent
BRIDGE_SCRIPT = BASE_DIR / "winrt_bridge.ps1"
POWERSHELL_EXE = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
ADB_EXE = BASE_DIR / "platform-tools" / "adb.exe"
CONFIG_FILE = BASE_DIR / "adb_config.json"


def ensure_adb() -> bool:
    """Descarga automáticamente platform-tools de Google si no existen."""
    if ADB_EXE.exists():
        return True
    try:
        import urllib.request
        import zipfile
        url = "https://dl.google.com/android/repository/platform-tools-latest-windows.zip"
        dest_zip = BASE_DIR / "platform-tools.zip"
        print("[ADB] Descargando platform-tools oficial de Google...")
        urllib.request.urlretrieve(url, dest_zip)
        with zipfile.ZipFile(dest_zip, "r") as z:
            z.extractall(BASE_DIR)
        if dest_zip.exists():
            dest_zip.unlink()
        return ADB_EXE.exists()
    except Exception as e:
        print(f"[ADB] Error al descargar platform-tools: {e}")
        return False


def load_config() -> dict:
    try:
        if CONFIG_FILE.exists():
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def save_config(updates: dict) -> None:
    try:
        data = load_config()
        data.update(updates)
        CONFIG_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def get_lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return ""


class AdbMediaBridge:
    """Queries Android media_session (Track Title, Artist, App, Play/Pause state) and sends media keyevents via ADB (Wi-Fi or USB) for any Android device."""

    APP_NAMES = {
        "com.google.android.apps.youtube.music": "YouTube Music",
        "com.google.android.youtube": "YouTube",
        "com.spotify.music": "Spotify",
        "deezer.android.app": "Deezer",
        "com.apple.android.music": "Apple Music",
        "com.amazon.mp3": "Amazon Music",
        "com.miui.player": "Música",
        "com.sec.android.app.music": "Samsung Music",
        "org.videolan.vlc": "VLC",
        "com.tiktok.android.music": "TikTok Music",
        "com.zhiliaoapp.musically": "TikTok",
        "com.soundcloud.android": "SoundCloud",
        "com.tidal.wave": "TIDAL",
    }

    KEYEVENTS = {
        "play_pause": "85",  # KEYCODE_MEDIA_PLAY_PAUSE
        "play": "126",       # KEYCODE_MEDIA_PLAY
        "pause": "127",      # KEYCODE_MEDIA_PAUSE
        "next": "87",        # KEYCODE_MEDIA_NEXT
        "prev": "88",        # KEYCODE_MEDIA_PREVIOUS
    }

    def __init__(self) -> None:
        self._lock = threading.Lock()
        cfg = load_config()
        self.state: dict = {
            "available": ADB_EXE.exists(),
            "connected": False,
            "deviceSerial": "",
            "deviceModel": "",
            "connectionType": "",
            "lastIp": str(cfg.get("last_ip", "")),
            "media": {
                "found": False,
                "app": "",
                "title": "",
                "artist": "",
                "album": "",
                "playing": False,
                "status": "None",
            },
        }
        self._stop = False
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    def _save_ip(self, ip_addr: str) -> None:
        self.state["lastIp"] = ip_addr
        save_config({"last_ip": ip_addr})

    def _run_adb(self, args: list[str], timeout: float = 4.0) -> tuple[int, str, str]:
        if not ADB_EXE.exists():
            if not ensure_adb():
                return -1, "", "adb.exe no encontrado"
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            proc = subprocess.run(
                [str(ADB_EXE)] + args,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                creationflags=creationflags,
            )
            return proc.returncode, proc.stdout or "", proc.stderr or ""
        except subprocess.TimeoutExpired:
            return -2, "", "Tiempo de espera agotado"
        except Exception as exc:
            return -3, "", str(exc)

    def _discover_device(self) -> tuple[str, str, str]:
        code, out, _ = self._run_adb(["devices", "-l"], timeout=2.5)
        if code == 0:
            for line in out.splitlines()[1:]:
                parts = line.strip().split()
                if len(parts) >= 2 and parts[1] == "device":
                    serial = parts[0]
                    ctype = "Wi-Fi" if (":" in serial or "._tcp" in serial) else "USB"
                    model_match = re.search(r"model:(\S+)", line)
                    model = model_match.group(1).replace("_", " ") if model_match else serial
                    return serial, ctype, model

        # Check mDNS wireless debugging services automatically for any Android 11+ device
        code, out, _ = self._run_adb(["mdns", "services"], timeout=2.0)
        if code == 0:
            for line in out.splitlines():
                if "_adb-tls-connect._tcp" in line or "_adb._tcp" in line:
                    parts = line.strip().split()
                    if parts:
                        addr = parts[-1]
                        if ":" in addr:
                            self._run_adb(["connect", addr], timeout=3.0)
                            return addr, "Wi-Fi", addr
        return "", "", ""

    def _parse_media_session(self, text: str) -> dict:
        sessions = []
        blocks = re.split(r"\n\s{4}(?=\S+/\S+\s+\(userId=)", text)
        for b in blocks[1:]:
            pkg_match = re.match(r"^([a-zA-Z0-9_.]+)/", b.strip())
            pkg = pkg_match.group(1) if pkg_match else ""
            active = "active=true" in b
            state_match = re.search(r"state=PlaybackState\s*\{state=(\d+)", b)
            state_code = int(state_match.group(1)) if state_match else 0
            meta_match = re.search(
                r"metadata:\s*size=\d+,\s*description=(.+)", b
            )
            desc = meta_match.group(1).strip() if meta_match else ""
            if not desc or desc == "null, null, null":
                continue
            parts = [p.strip() for p in desc.split(",")]
            title = parts[0] if len(parts) > 0 and parts[0] != "null" else ""
            artist = parts[1] if len(parts) > 1 and parts[1] != "null" else ""
            album = parts[2] if len(parts) > 2 and parts[2] != "null" else ""
            if not title:
                continue

            friendly_app = self.APP_NAMES.get(pkg, pkg.split(".")[-1].capitalize() if pkg else "Multimedia")
            playing = state_code == 3  # 3 = STATE_PLAYING, 2 = STATE_PAUSED
            priority = (2 if playing else (1 if active else 0))
            sessions.append(
                {
                    "priority": priority,
                    "found": True,
                    "app": friendly_app,
                    "title": title,
                    "artist": artist,
                    "album": album,
                    "playing": playing,
                    "status": "Playing" if playing else "Paused",
                }
            )

        if not sessions:
            return {
                "found": False,
                "app": "",
                "title": "",
                "artist": "",
                "album": "",
                "playing": False,
                "status": "None",
            }
        sessions.sort(key=lambda s: s["priority"], reverse=True)
        best = sessions[0]
        best.pop("priority", None)
        return best

    def _poll_loop(self) -> None:
        while not self._stop:
            try:
                serial, ctype, model = self._discover_device()
                if not serial:
                    self.state["connected"] = False
                    self.state["deviceSerial"] = ""
                    self.state["deviceModel"] = ""
                    self.state["connectionType"] = ""
                    self.state["media"] = {
                        "found": False,
                        "app": "",
                        "title": "",
                        "artist": "",
                        "album": "",
                        "playing": False,
                        "status": "None",
                    }
                    time.sleep(2.0)
                    continue

                self.state["connected"] = True
                self.state["deviceSerial"] = serial
                self.state["deviceModel"] = model
                self.state["connectionType"] = ctype

                code, out, _ = self._run_adb(
                    ["-s", serial, "shell", "dumpsys", "media_session"],
                    timeout=2.5,
                )
                if code == 0 and out:
                    self.state["media"] = self._parse_media_session(out)
            except Exception:
                pass
            time.sleep(1.2)

    def send_media_key(self, cmd: str) -> dict:
        serial = self.state.get("deviceSerial", "")
        if not serial:
            return {"ok": False, "error": "ADB no está conectado al dispositivo."}
        keycode = self.KEYEVENTS.get(cmd)
        if not keycode:
            return {"ok": False, "error": f"Comando desconocido: {cmd}"}
        code, _, err = self._run_adb(
            ["-s", serial, "shell", "input", "keyevent", keycode],
            timeout=2.5,
        )
        if code == 0:
            if cmd == "play_pause":
                self.state["media"]["playing"] = not self.state["media"].get("playing", False)
            elif cmd == "play":
                self.state["media"]["playing"] = True
            elif cmd == "pause":
                self.state["media"]["playing"] = False
            return {"ok": True}
        return {"ok": False, "error": err.strip() or "Error enviando comando ADB"}

    def pair_wifi(self, pair_address: str, pair_code: str) -> dict:
        pair_address = pair_address.strip()
        pair_code = pair_code.strip()
        if not pair_address or not pair_code:
            return {"ok": False, "error": "Ingresa IP:Puerto de vinculación y el código de 6 dígitos."}
        code, out, err = self._run_adb(["pair", pair_address, pair_code], timeout=8.0)
        combined = (out + "\n" + err).strip()
        if code == 0 and ("Successfully paired" in combined or "paired" in combined.lower()):
            host_only = pair_address.split(":")[0]
            self._save_ip(host_only)
            return {"ok": True, "message": f"¡Dispositivo vinculado con éxito! ({combined})"}
        return {"ok": False, "error": combined or "No se pudo vincular. Verifica IP:Puerto y código."}

    def connect_wifi(self, connect_address: str) -> dict:
        connect_address = connect_address.strip()
        if not connect_address:
            return {"ok": False, "error": "Ingresa la dirección IP:Puerto de Depuración Inalámbrica."}
        if ":" not in connect_address:
            connect_address = f"{connect_address}:5555"
        code, out, err = self._run_adb(["connect", connect_address], timeout=6.0)
        combined = (out + "\n" + err).strip()
        if "connected to" in combined.lower():
            self._save_ip(connect_address)
            return {"ok": True, "message": f"¡Conectado por Wi-Fi a {connect_address}!"}
        return {"ok": False, "error": combined or "No se pudo conectar."}

    def switch_usb_to_wifi(self) -> dict:
        code, out, _ = self._run_adb(["devices"], timeout=3.0)
        usb_serial = ""
        if code == 0:
            for line in out.splitlines()[1:]:
                parts = line.strip().split()
                if len(parts) >= 2 and parts[1] == "device" and ":" not in parts[0]:
                    usb_serial = parts[0]
                    break
        if not usb_serial:
            return {
                "ok": False,
                "error": "No se detectó ningún dispositivo por cable USB (asegúrate de activar Depuración USB y aceptar el permiso en la pantalla del dispositivo).",
            }

        _, ip_out, _ = self._run_adb(["-s", usb_serial, "shell", "ip", "route"], timeout=3.0)
        phone_ip = ""
        for line in ip_out.splitlines():
            m = re.search(r"src\s+(\d+\.\d+\.\d+\.\d+)", line)
            if m:
                phone_ip = m.group(1)
                break

        self._run_adb(["-s", usb_serial, "tcpip", "5555"], timeout=4.0)
        time.sleep(1.0)
        if phone_ip:
            target = f"{phone_ip}:5555"
            self._run_adb(["connect", target], timeout=5.0)
            self._save_ip(target)
            return {
                "ok": True,
                "message": f"¡Listo! Conectado por Wi-Fi en {target}. Ya puedes desconectar el cable USB.",
            }
        return {
            "ok": True,
            "message": "Modo Wi-Fi (puerto 5555) activado en el dispositivo. Ingresa su IP para conectar.",
        }


class WinRTBridge:
    """Manages a persistent PowerShell 5.1 process holding the WinRT AudioPlaybackConnection and CoreAudio mixer."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self.audio_connected = False
        self._start_process()

    def _start_process(self) -> None:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self._proc = subprocess.Popen(
            [
                POWERSHELL_EXE,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(BRIDGE_SCRIPT),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
            creationflags=creationflags,
        )

    def send_command(self, payload: dict) -> dict:
        with self._lock:
            if self._proc is None or self._proc.poll() is not None:
                self._start_process()
            assert self._proc is not None
            assert self._proc.stdin is not None
            assert self._proc.stdout is not None

            try:
                line = json.dumps(payload, ensure_ascii=False)
                self._proc.stdin.write(line + "\n")
                self._proc.stdin.flush()
                raw_resp = self._proc.stdout.readline()
                if not raw_resp:
                    return {"ok": False, "error": "El puente WinRT no respondio."}
                data = json.loads(raw_resp.strip())
                if "devices" in data:
                    devs = data["devices"]
                    if devs is None:
                        data["devices"] = []
                    elif isinstance(devs, dict):
                        data["devices"] = [devs]
                if "audioConnected" in data:
                    self.audio_connected = bool(data.get("audioConnected"))
                elif payload.get("action") == "disconnect":
                    self.audio_connected = False
                elif payload.get("action") == "connect" and data.get("ok"):
                    self.audio_connected = True
                return data
            except Exception as exc:
                return {"ok": False, "error": str(exc)}

    def close(self) -> None:
        with self._lock:
            if self._proc and self._proc.poll() is None:
                try:
                    self._proc.stdin.write(json.dumps({"action": "disconnect"}) + "\n")
                    self._proc.stdin.flush()
                    self._proc.stdout.readline()
                except Exception:
                    pass
                try:
                    self._proc.terminate()
                except Exception:
                    pass
                self.audio_connected = False


HTML_PAGE = """<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Mini-Reproductor Bluetooth Multi-Dispositivo</title>
  <style>
    :root {
      --bg: #0f1117;
      --card: #181b24;
      --card-border: #262b38;
      --text: #f3f4f6;
      --muted: #9ca3af;
      --accent: #10b981;
      --accent-hover: #059669;
      --danger: #ef4444;
      --danger-hover: #dc2626;
      --primary: #3b82f6;
      --primary-hover: #2563eb;
      --warn-bg: rgba(245, 158, 11, 0.14);
      --warn-border: rgba(245, 158, 11, 0.4);
      --warn-text: #fcd34d;
    }

    * {
      box-sizing: border-box;
      margin: 0;
      padding: 0;
      user-select: none;
    }

    body {
      font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
      background: var(--bg);
      color: var(--text);
      min-height: 100vh;
      display: flex;
      justify-content: center;
      align-items: flex-start;
      padding: 12px;
    }

    .player-shell {
      width: 100%;
      max-width: 390px;
      display: flex;
      flex-direction: column;
      gap: 10px;
    }

    .header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 2px;
    }

    .brand {
      display: flex;
      align-items: center;
      gap: 8px;
      font-weight: 600;
      font-size: 0.95rem;
    }

    .status-pill {
      font-size: 0.74rem;
      padding: 4px 10px;
      border-radius: 999px;
      font-weight: 600;
      display: flex;
      align-items: center;
      gap: 6px;
      background: #232734;
      color: var(--muted);
    }

    .status-pill.live {
      background: rgba(16, 185, 129, 0.15);
      color: #34d399;
      border: 1px solid rgba(16, 185, 129, 0.35);
    }

    .status-pill.paused {
      background: rgba(245, 158, 11, 0.15);
      color: #fbbf24;
      border: 1px solid rgba(245, 158, 11, 0.35);
    }

    .dot {
      width: 7px;
      height: 7px;
      border-radius: 50%;
      background: currentColor;
    }

    .status-pill.live .dot {
      box-shadow: 0 0 8px #34d399;
      animation: pulse 1.6s infinite;
    }

    @keyframes pulse {
      0%, 100% { opacity: 1; }
      50% { opacity: 0.4; }
    }

    .card {
      background: var(--card);
      border: 1px solid var(--card-border);
      border-radius: 14px;
      padding: 13px;
    }

    .warn-banner {
      background: var(--warn-bg);
      border: 1px solid var(--warn-border);
      color: var(--warn-text);
      border-radius: 12px;
      padding: 12px;
      font-size: 0.82rem;
      display: none;
      flex-direction: column;
      gap: 8px;
    }

    .warn-banner.visible {
      display: flex;
    }

    .row {
      display: flex;
      gap: 7px;
      align-items: center;
    }

    label.section-label {
      font-size: 0.72rem;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: var(--muted);
      margin-bottom: 6px;
      display: block;
      font-weight: 600;
    }

    select, input[type="text"] {
      flex: 1;
      background: #10131a;
      color: var(--text);
      border: 1px solid var(--card-border);
      border-radius: 8px;
      padding: 7px 10px;
      font-size: 0.83rem;
      outline: none;
      user-select: text;
    }

    input[type="text"]:focus {
      border-color: var(--primary);
    }

    button {
      cursor: pointer;
      border: none;
      border-radius: 9px;
      font-family: inherit;
      font-weight: 600;
      transition: all 0.15s ease;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 5px;
    }

    button:disabled {
      opacity: 0.4;
      cursor: not-allowed;
    }

    .btn-icon {
      background: #232836;
      color: var(--text);
      padding: 7px 10px;
      font-size: 0.83rem;
    }

    .btn-icon:hover:not(:disabled) {
      background: #2e3446;
    }

    .btn-primary {
      background: var(--primary);
      color: white;
      padding: 7px 12px;
      font-size: 0.8rem;
    }

    .btn-primary:hover:not(:disabled) {
      background: var(--primary-hover);
    }

    .btn-stream-toggle {
      width: 100%;
      margin-top: 8px;
      padding: 8px 12px;
      font-size: 0.82rem;
      border-radius: 9px;
      background: #232836;
      color: var(--text);
      border: 1px solid #32394e;
    }

    .btn-stream-toggle:hover:not(:disabled) {
      background: #2d3446;
    }

    .btn-stream-toggle.connected {
      background: rgba(239, 68, 68, 0.14);
      color: #fca5a5;
      border-color: rgba(239, 68, 68, 0.35);
    }

    .btn-stream-toggle.connected:hover:not(:disabled) {
      background: var(--danger);
      color: white;
    }

    /* Now Playing Card */
    .now-playing {
      display: flex;
      flex-direction: column;
      align-items: center;
      text-align: center;
      gap: 10px;
      padding: 14px 13px;
    }

    .top-viz-row {
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 10px;
      width: 100%;
    }

    .app-badge {
      font-size: 0.72rem;
      padding: 3px 9px;
      border-radius: 999px;
      background: rgba(59, 130, 246, 0.16);
      color: #93c5fd;
      border: 1px solid rgba(59, 130, 246, 0.32);
      font-weight: 600;
      display: none;
    }

    .app-badge.visible {
      display: inline-block;
    }

    .Visualizer {
      width: 64px;
      height: 46px;
      border-radius: 12px;
      background: linear-gradient(135deg, #1e293b, #0f172a);
      border: 1px solid #334155;
      display: flex;
      align-items: flex-end;
      justify-content: center;
      gap: 5px;
      padding: 8px;
    }

    .bar {
      width: 5px;
      height: 6px;
      background: var(--muted);
      border-radius: 99px;
      transition: height 0.18s ease, background 0.18s ease;
    }

    .Visualizer.active .bar {
      background: var(--accent);
    }

    .Visualizer.muted .bar {
      background: #f59e0b;
      height: 6px !important;
    }

    .track-title {
      font-size: 1.02rem;
      font-weight: 700;
      color: var(--text);
      max-width: 100%;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .track-artist {
      font-size: 0.81rem;
      color: var(--muted);
      margin-top: 3px;
      max-width: 100%;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .controls {
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 10px;
      width: 100%;
    }

    .btn-ctrl {
      height: 42px;
      padding: 0 12px;
      border-radius: 11px;
      background: #232836;
      color: var(--text);
      font-size: 0.9rem;
    }

    .btn-ctrl:hover:not(:disabled) {
      background: #30374a;
      transform: translateY(-1px);
    }

    .btn-ctrl.play-pause {
      width: 58px;
      height: 58px;
      border-radius: 50%;
      background: var(--accent);
      color: #052e16;
      font-size: 1.35rem;
      box-shadow: 0 4px 14px rgba(16, 185, 129, 0.3);
    }

    .btn-ctrl.play-pause:hover:not(:disabled) {
      background: var(--accent-hover);
      color: white;
    }

    .btn-ctrl.play-pause.is-playing {
      background: var(--primary);
      color: white;
      box-shadow: 0 4px 14px rgba(59, 130, 246, 0.3);
    }

    .sub-controls {
      display: flex;
      gap: 8px;
      width: 100%;
    }

    .btn-sub {
      flex: 1;
      padding: 6px 10px;
      font-size: 0.76rem;
      background: #1b202c;
      color: #cbd5e1;
      border: 1px solid #283044;
      border-radius: 8px;
    }

    .btn-sub:hover:not(:disabled) {
      background: #252c3d;
    }

    .btn-sub.active-mute {
      background: rgba(245, 158, 11, 0.16);
      color: #fcd34d;
      border-color: rgba(245, 158, 11, 0.4);
    }

    /* Volume Slider Box */
    .volume-box {
      width: 100%;
      background: #12151d;
      border: 1px solid #222736;
      border-radius: 11px;
      padding: 9px 11px;
      display: flex;
      flex-direction: column;
      gap: 7px;
    }

    .vol-header {
      display: flex;
      justify-content: space-between;
      font-size: 0.76rem;
      color: var(--muted);
      font-weight: 600;
    }

    .vol-value {
      color: var(--text);
      font-weight: 700;
    }

    input[type="range"] {
      width: 100%;
      accent-color: var(--primary);
      cursor: pointer;
    }

    .presets {
      display: flex;
      justify-content: space-between;
      gap: 6px;
    }

    .btn-preset {
      flex: 1;
      padding: 4px 0;
      font-size: 0.73rem;
      background: #1e2330;
      color: var(--muted);
      border-radius: 6px;
    }

    .btn-preset:hover {
      background: #2a3142;
      color: var(--text);
    }

    .toggle-option {
      display: flex;
      align-items: center;
      justify-content: space-between;
      font-size: 0.78rem;
      color: #cbd5e1;
      padding: 8px 11px;
      background: #141821;
      border: 1px solid var(--card-border);
      border-radius: 10px;
      width: 100%;
      cursor: pointer;
      text-align: left;
    }

    .toggle-option input {
      width: 15px;
      height: 15px;
      accent-color: var(--accent);
      cursor: pointer;
      flex-shrink: 0;
    }

    /* Collapsible Multi-Device Sync panel */
    details.sync-panel {
      background: var(--card);
      border: 1px solid var(--card-border);
      border-radius: 14px;
      padding: 10px 13px;
      font-size: 0.8rem;
    }

    details.sync-panel summary {
      cursor: pointer;
      font-weight: 600;
      display: flex;
      align-items: center;
      justify-content: space-between;
      list-style: none;
    }

    details.sync-panel summary::-webkit-details-marker {
      display: none;
    }

    .sync-badge {
      font-size: 0.7rem;
      padding: 2px 8px;
      border-radius: 99px;
      background: #232836;
      color: var(--muted);
    }

    .sync-badge.ok {
      background: rgba(16, 185, 129, 0.16);
      color: #34d399;
    }

    .sync-body {
      margin-top: 10px;
      display: flex;
      flex-direction: column;
      gap: 8px;
      border-top: 1px solid var(--card-border);
      padding-top: 10px;
      color: #cbd5e1;
      line-height: 1.35;
    }

    .help-steps {
      font-size: 0.74rem;
      color: var(--muted);
      background: #12151d;
      padding: 8px 10px;
      border-radius: 8px;
      border: 1px solid #222736;
    }

    .toast {
      font-size: 0.79rem;
      padding: 8px 11px;
      border-radius: 9px;
      background: #1e2433;
      border: 1px solid #2e364f;
      color: #cbd5e1;
      display: none;
    }

    .toast.visible {
      display: block;
    }

    .footer-help {
      font-size: 0.71rem;
      color: var(--muted);
      text-align: center;
      line-height: 1.35;
    }
  </style>
</head>
<body>
  <div class="player-shell">
    <div class="header">
      <div class="brand">
        <span>🎧</span>
        <span>Mini-Reproductor Bluetooth</span>
      </div>
      <div id="connectionPill" class="status-pill">
        <span class="dot"></span>
        <span id="connectionText">Desconectado</span>
      </div>
    </div>

    <div id="btOffBanner" class="warn-banner">
      <div>⚠️ <strong>El Bluetooth de tu PC está apagado.</strong></div>
      <div class="row">
        <button class="btn-primary" onclick="powerBluetooth(true)">⚡ Encender Bluetooth</button>
        <button class="btn-icon" onclick="openBtSettings()">⚙️ Ajustes</button>
      </div>
    </div>

    <!-- Tarjeta Principal del Reproductor -->
    <div class="card now-playing">
      <div class="top-viz-row">
        <div id="visualizer" class="Visualizer">
          <div id="bar1" class="bar"></div>
          <div id="bar2" class="bar"></div>
          <div id="bar3" class="bar"></div>
          <div id="bar4" class="bar"></div>
          <div id="bar5" class="bar"></div>
        </div>
      </div>

      <span id="appBadge" class="app-badge">🎵 Multimedia</span>

      <div style="width: 100%;">
        <div id="trackTitle" class="track-title">Dispositivo Bluetooth</div>
        <div id="trackArtist" class="track-artist">Listo para reproducir en tus audífonos del PC</div>
      </div>

      <!-- Controles Multimedia Principales (Anterior / Play-Pausa / Siguiente) -->
      <div class="controls">
        <button id="prevBtn" class="btn-ctrl" title="Canción anterior en el dispositivo" onclick="sendMediaCommand('prev')">⏮</button>
        <button class="btn-ctrl" title="Bajar volumen (-10%)" onclick="stepVolume(-10)">🔉</button>
        <button id="playPauseBtn" class="btn-ctrl play-pause" title="Pausar / Reanudar en el dispositivo (Espacio)" onclick="toggleListenPlayPause()">▶</button>
        <button class="btn-ctrl" title="Subir volumen (+10%)" onclick="stepVolume(10)">🔊</button>
        <button id="nextBtn" class="btn-ctrl" title="Siguiente canción en el dispositivo" onclick="sendMediaCommand('next')">⏭</button>
      </div>

      <!-- Botón secundario para silenciar solo el PC sin pausar el dispositivo -->
      <div class="sub-controls">
        <button id="pcMuteBtn" class="btn-sub" onclick="togglePcMuteOnly()">
          🔇 Silenciar solo en PC (sin pausar dispositivo)
        </button>
      </div>

      <!-- Control de Volumen Independiente -->
      <div class="volume-box">
        <div class="vol-header">
          <span>Volumen de Música en PC</span>
          <span id="volLabel" class="vol-value">100%</span>
        </div>
        <input id="volSlider" type="range" min="0" max="100" value="100" oninput="onSliderInput(this.value)" onchange="setVolume(this.value)" />
        <div class="presets">
          <button class="btn-preset" onclick="setVolume(15)">15% (Suave)</button>
          <button class="btn-preset" onclick="setVolume(35)">35%</button>
          <button class="btn-preset" onclick="setVolume(65)">65%</button>
          <button class="btn-preset" onclick="setVolume(100)">100%</button>
        </div>
      </div>

      <!-- Opciones inteligentes universales -->
      <label class="toggle-option" title="Funciona con cualquier celular o tablet (Android, iPhone, iPad): al pulsar Pausa suspende el enlace A2DP para que el dispositivo pause la canción de inmediato.">
        <span>📱 Pausar canción en el dispositivo al pulsar ⏸</span>
        <input id="pausePhoneCheck" type="checkbox" checked />
      </label>

      <label class="toggle-option" title="Al cerrar esta pestaña o ventana del navegador, desconecta automáticamente el enlace de audio Bluetooth del teléfono para que quede libre.">
        <span>🔌 Desconectar Bluetooth al cerrar la ventana</span>
        <input id="disconnectOnCloseCheck" type="checkbox" checked />
      </label>

      <label class="toggle-option">
        <span>🛡️ Silenciar música si hablan en Teams</span>
        <input id="autoDuckCheck" type="checkbox" checked />
      </label>
    </div>

    <!-- Panel Multi-Dispositivo para Metadatos y Control Avanzado -->
    <details id="syncDetails" class="sync-panel">
      <summary>
        <span>📲 Ver Canción en Vivo (Android / iPhone / Enlace Móvil)</span>
        <span id="syncStatusBadge" class="sync-badge">Opcional</span>
      </summary>
      <div class="sync-body">
        <div class="help-steps">
          <strong>1. Cualquier Android o iPhone con Enlace Móvil de Windows:</strong><br/>
          Si vinculas tu teléfono en <em>Enlace Móvil (Phone Link)</em> de Windows, el nombre de la canción y los controles se sincronizan automáticamente.
        </div>
        <button class="btn-icon" onclick="openPhoneLink()">💻 Abrir Enlace Móvil de Windows</button>

        <div class="help-steps" style="margin-top: 2px;">
          <strong>2. Cualquier Android (Samsung, Motorola, Xiaomi, Pixel, etc.) por ADB:</strong><br/>
          Activa <em>Depuración USB</em> (o <em>Depuración inalámbrica</em>) en <em>Opciones de desarrollador</em> de tu Android.<br/>
          • <em>Samsung / Motorola / Pixel:</em> Ajustes → Acerca del teléfono → Información de software → toca 7 veces en <em>Número de compilación</em>.<br/>
          • <em>Xiaomi / HyperOS:</em> Ajustes → Sobre el teléfono → toca 7 veces en <em>Versión de HyperOS</em> (y activa <em>Depuración USB - Ajustes de seguridad</em>).
        </div>
        <button class="btn-primary" onclick="usbToWifi()">🔌 Pasar Android conectado por USB a Wi-Fi Automático</button>

        <div class="row">
          <input id="adbConnectAddr" type="text" placeholder="IP:Puerto Android (ej. 192.168.1.50:5555)" />
          <button class="btn-primary" onclick="connectAdbWifi()">Conectar Wi-Fi</button>
        </div>

        <details style="font-size: 0.75rem; color: var(--muted);">
          <summary style="cursor: pointer; color: #93c5fd;">Vincular Android por código de 6 dígitos (Depuración Inalámbrica)</summary>
          <div style="display: flex; flex-direction: column; gap: 6px; margin-top: 6px;">
            <input id="adbPairAddr" type="text" placeholder="IP:Puerto de vinculación (ej. 192.168.1.50:38123)" />
            <div class="row">
              <input id="adbPairCode" type="text" placeholder="Código de 6 dígitos" />
              <button class="btn-icon" onclick="pairAdbWifi()">Vincular</button>
            </div>
          </div>
        </details>
      </div>
    </details>

    <!-- Tarjeta de Selector de Dispositivo Bluetooth (Cualquier celular / tablet / dispositivo) -->
    <div class="card">
      <label class="section-label" for="deviceSelect">Dispositivo Bluetooth vinculado</label>
      <div class="row">
        <select id="deviceSelect" onchange="onDeviceSelectionChange()"></select>
        <button class="btn-icon" title="Actualizar lista de dispositivos" onclick="refreshState(true)">🔄</button>
        <button class="btn-icon" title="Vincular nuevo dispositivo en Windows" onclick="openBtSettings()">⚙️</button>
      </div>

      <button id="toggleConnBtn" class="btn-stream-toggle" onclick="toggleBluetoothLink()">
        🔗 Conectar Enlace de Audio Bluetooth
      </button>
    </div>

    <div id="toastBox" class="toast"></div>

    <div class="footer-help">
      Atajos: <strong>Espacio</strong> (Play/Pausa) · <strong>Flechas ←/→</strong> (Canción Ant/Sig) · <strong>↑/↓</strong> (Volumen)<br/>
      <span id="lanUrlText" style="opacity: 0.85; font-size: 0.68rem; margin-top: 3px; display: inline-block;"></span>
    </div>
  </div>

  <script>
    let currentState = {
      btRadioState: "On",
      audioConnected: false,
      activeDeviceId: "",
      activeDeviceName: "",
      devices: [],
      smtc: { found: false },
      adb: { connected: false, media: { found: false } },
      mixer: { found: false, volume: 100, muted: false, peak: 0, teamsActive: false }
    };
    let selectedDeviceId = "";
    let busy = false;
    let draggingSlider = false;
    let autoMutedByTeams = false;
    let teamsQuietCount = 0;
    let pausedByBtDisconnect = false;

    function showToast(msg, duration = 3800) {
      const box = document.getElementById("toastBox");
      box.textContent = msg;
      box.classList.add("visible");
      if (duration > 0) {
        setTimeout(() => box.classList.remove("visible"), duration);
      }
    }

    async function apiPost(payload) {
      const res = await fetch("/api/command", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      return await res.json();
    }

    async function refreshState(manual = false) {
      if (busy && !manual) return;
      try {
        const data = await apiPost({ action: "state" });
        if (!data.ok) return;
        currentState = data;
        const connectInput = document.getElementById("adbConnectAddr");
        if (connectInput && !connectInput.value && data.adb && data.adb.lastIp) {
          connectInput.value = data.adb.lastIp;
        }
        await handleTeamsAutoDuck();
        renderState();
      } catch (e) {}
    }

    async function handleTeamsAutoDuck() {
      const autoDuck = document.getElementById("autoDuckCheck").checked;
      if (!autoDuck || !currentState.audioConnected) return;

      const mixer = currentState.mixer || {};
      if (mixer.teamsActive && !mixer.muted) {
        autoMutedByTeams = true;
        teamsQuietCount = 0;
        await apiPost({ action: "mixer", mute: true });
        currentState.mixer.muted = true;
        showToast("🛡️ Audio de Teams detectado: música silenciada temporalmente.");
      } else if (autoMutedByTeams && !mixer.teamsActive) {
        teamsQuietCount++;
        if (teamsQuietCount >= 3) {
          autoMutedByTeams = false;
          teamsQuietCount = 0;
          await apiPost({ action: "mixer", mute: false });
          currentState.mixer.muted = false;
          showToast("🎧 Llamada en silencio: música reanudada.");
        }
      }
    }

    function getActiveMediaInfo() {
      if (currentState.adb && currentState.adb.connected && currentState.adb.media && currentState.adb.media.found) {
        return { source: "adb", ...currentState.adb.media };
      }
      if (currentState.smtc && currentState.smtc.found && currentState.smtc.title) {
        return { source: "smtc", ...currentState.smtc };
      }
      return { found: false };
    }

    function getSelectedOrActiveDeviceName() {
      if (currentState.activeDeviceName) {
        return currentState.activeDeviceName;
      }
      const select = document.getElementById("deviceSelect");
      if (select && select.selectedIndex >= 0 && select.options[select.selectedIndex]?.value) {
        return select.options[select.selectedIndex].text;
      }
      if (currentState.devices && currentState.devices.length > 0) {
        return currentState.devices[0].name;
      }
      return "Dispositivo Bluetooth";
    }

    function renderState() {
      const btBanner = document.getElementById("btOffBanner");
      btBanner.classList.toggle("visible", currentState.btRadioState === "Off");

      const mixer = currentState.mixer || {};
      const isConnected = currentState.audioConnected;
      const isMuted = mixer.muted;
      const media = getActiveMediaInfo();
      const adbConn = currentState.adb && currentState.adb.connected;

      // Selector de dispositivos (soporta múltiples dispositivos vinculados)
      const select = document.getElementById("deviceSelect");
      const preferredVal = selectedDeviceId || currentState.activeDeviceId || currentState.lastBtDeviceId;
      select.innerHTML = "";
      if (!currentState.devices || currentState.devices.length === 0) {
        const opt = document.createElement("option");
        opt.value = "";
        opt.textContent = "Ningún dispositivo de audio detectado";
        select.appendChild(opt);
      } else {
        currentState.devices.forEach((dev) => {
          const opt = document.createElement("option");
          opt.value = dev.id;
          opt.textContent = dev.name + (dev.isConnected ? " (Conectado)" : "");
          select.appendChild(opt);
        });
        if (preferredVal && currentState.devices.some(d => d.id === preferredVal)) {
          select.value = preferredVal;
        } else {
          select.value = currentState.devices[0].id;
        }
        selectedDeviceId = select.value;
      }

      const devLabel = getSelectedOrActiveDeviceName();

      // Badge de estado ADB / SMTC
      const syncBadge = document.getElementById("syncStatusBadge");
      if (adbConn) {
        const modelStr = currentState.adb.deviceModel ? " · " + currentState.adb.deviceModel : "";
        syncBadge.textContent = "Activo (" + currentState.adb.connectionType + modelStr + ")";
        syncBadge.classList.add("ok");
      } else if (currentState.smtc && currentState.smtc.found) {
        syncBadge.textContent = "Activo (Windows)";
        syncBadge.classList.add("ok");
      } else {
        syncBadge.textContent = "Opcional";
        syncBadge.classList.remove("ok");
      }

      const isPlayingAudio = isConnected && !isMuted && (media.found ? media.playing : true);

      // Pill superior
      const pill = document.getElementById("connectionPill");
      const pillText = document.getElementById("connectionText");
      pill.classList.remove("live", "paused");
      if (!isConnected && pausedByBtDisconnect) {
        pill.classList.add("paused");
        pillText.textContent = "Pausado en Dispositivo";
      } else if (!isConnected) {
        pillText.textContent = "Desconectado";
      } else if (isMuted || (media.found && !media.playing)) {
        pill.classList.add("paused");
        pillText.textContent = isMuted ? "Silenciado en PC" : "Pausado en Dispositivo";
      } else {
        pill.classList.add("live");
        pillText.textContent = "Escuchando en PC";
      }

      // App Badge (YouTube Music, Spotify, Apple Music, etc.)
      const appBadge = document.getElementById("appBadge");
      if (media.found && media.app) {
        appBadge.textContent = "🎵 " + media.app + (isConnected ? " · " + devLabel : "");
        appBadge.classList.add("visible");
      } else if (isConnected) {
        appBadge.textContent = "📱 " + devLabel;
        appBadge.classList.add("visible");
      } else {
        appBadge.classList.remove("visible");
      }

      // Título y Artista
      const titleEl = document.getElementById("trackTitle");
      const artistEl = document.getElementById("trackArtist");
      const playBtn = document.getElementById("playPauseBtn");

      if (media.found) {
        titleEl.textContent = media.title;
        const sub = [media.artist, media.album].filter(Boolean).join(" · ");
        artistEl.textContent = sub || (media.playing ? "Reproduciendo en " + devLabel : "En pausa en " + devLabel);
        if (media.playing && isConnected && !isMuted) {
          playBtn.textContent = "⏸";
          playBtn.classList.add("is-playing");
        } else {
          playBtn.textContent = "▶";
          playBtn.classList.remove("is-playing");
        }
      } else if (!isConnected && pausedByBtDisconnect) {
        titleEl.textContent = devLabel + " — En Pausa";
        artistEl.textContent = "Reproducción pausada en el dispositivo · Pulsa ▶ para reconectar";
        playBtn.textContent = "▶";
        playBtn.classList.remove("is-playing");
      } else if (!isConnected) {
        titleEl.textContent = devLabel;
        artistEl.textContent = "Pulsa ▶ para conectar y escuchar en tu PC";
        playBtn.textContent = "▶";
        playBtn.classList.remove("is-playing");
      } else if (isMuted) {
        titleEl.textContent = devLabel + " — Silenciado en PC";
        artistEl.textContent = "El dispositivo sigue conectado · Pulsa ▶ para escuchar";
        playBtn.textContent = "▶";
        playBtn.classList.remove("is-playing");
      } else {
        titleEl.textContent = devLabel + " — Escuchando";
        artistEl.textContent = mixer.peak > 0.5
          ? "🎵 Audio en vivo (" + mixer.peak + "%) · Pulsa ⏸ para pausar el dispositivo"
          : "Conectado · Reproduce música o video en " + devLabel;
        playBtn.textContent = "⏸";
        playBtn.classList.add("is-playing");
      }

      // Botón secundario de Silenciar solo PC
      const pcMuteBtn = document.getElementById("pcMuteBtn");
      if (isMuted) {
        pcMuteBtn.classList.add("active-mute");
        pcMuteBtn.textContent = "🔊 Quitar silencio en PC";
      } else {
        pcMuteBtn.classList.remove("active-mute");
        pcMuteBtn.textContent = "🔇 Silenciar solo en PC (sin pausar dispositivo)";
      }

      // Visualizador dinámico
      const viz = document.getElementById("visualizer");
      viz.classList.toggle("active", isPlayingAudio);
      viz.classList.toggle("muted", isConnected && isMuted);
      const peak = isPlayingAudio ? Math.max(8, Math.min(100, mixer.peak * 1.8)) : 12;
      const heights = [
        Math.max(6, peak * 0.22),
        Math.max(6, peak * 0.35),
        Math.max(6, peak * 0.42),
        Math.max(6, peak * 0.30),
        Math.max(6, peak * 0.20)
      ];
      ["bar1", "bar2", "bar3", "bar4", "bar5"].forEach((id, idx) => {
        const el = document.getElementById(id);
        if (el) el.style.height = (isPlayingAudio && mixer.peak > 0.2 ? heights[idx] : 7) + "px";
      });

      // Volumen
      if (!draggingSlider && typeof mixer.volume === "number") {
        document.getElementById("volSlider").value = mixer.volume;
        document.getElementById("volLabel").textContent = (isMuted ? "Silenciado (" + mixer.volume + "%)" : mixer.volume + "%");
      }

      // Botón inferior de enlace Bluetooth
      const connBtn = document.getElementById("toggleConnBtn");
      const selectedMatchesActive = isConnected && (selectedDeviceId === currentState.activeDeviceId);
      if (selectedMatchesActive) {
        connBtn.classList.add("connected");
        connBtn.textContent = "🔌 Desconectar " + devLabel;
      } else if (isConnected && selectedDeviceId !== currentState.activeDeviceId) {
        connBtn.classList.remove("connected");
        connBtn.textContent = "🔄 Cambiar conexión al dispositivo seleccionado";
      } else {
        connBtn.classList.remove("connected");
        connBtn.textContent = "🔗 Conectar Audio Bluetooth (" + devLabel + ")";
      }

      const lanEl = document.getElementById("lanUrlText");
      if (lanEl && currentState.lanIp) {
        lanEl.textContent = "📲 Control desde móvil/tablet: http://" + currentState.lanIp + ":" + (location.port || "8765");
      }
    }

    async function onDeviceSelectionChange() {
      const select = document.getElementById("deviceSelect");
      selectedDeviceId = select.value;
      renderState();
    }

    async function sendMediaCommand(cmd) {
      const res = await apiPost({ action: "media_cmd", cmd });
      if (!res.ok) {
        showToast("Abre el panel 📲 abajo para habilitar cambio de canción en este dispositivo.");
      } else {
        setTimeout(() => refreshState(true), 350);
      }
    }

    async function togglePcMuteOnly() {
      if (!currentState.audioConnected) {
        showToast("Conecta primero el enlace de audio Bluetooth.");
        return;
      }
      autoMutedByTeams = false;
      const res = await apiPost({ action: "mixer", toggleMute: true });
      if (res.ok) {
        currentState.mixer.muted = res.muted;
        renderState();
      }
    }

    // Botón central Play/Pausa universal para cualquier dispositivo
    async function toggleListenPlayPause() {
      const pauseOnPhone = document.getElementById("pausePhoneCheck").checked;
      const hasDirectControl = (currentState.adb && currentState.adb.connected) || (currentState.smtc && currentState.smtc.found);

      // 1) Si tenemos ADB o SMTC activo, enviamos Play/Pause directo
      if (hasDirectControl) {
        if (!currentState.audioConnected) {
          await toggleBluetoothLink();
        }
        if (currentState.mixer && currentState.mixer.muted) {
          await apiPost({ action: "mixer", mute: false });
        }
        await sendMediaCommand("play_pause");
        return;
      }

      // 2) Si no está conectado el audio Bluetooth, conectarlo al dispositivo elegido
      if (!currentState.audioConnected) {
        pausedByBtDisconnect = false;
        await toggleBluetoothLink();
        return;
      }

      // 3) Si está silenciado solo en PC, quitar silencio
      if (currentState.mixer && currentState.mixer.muted) {
        autoMutedByTeams = false;
        await apiPost({ action: "mixer", mute: false });
        await refreshState(true);
        return;
      }

      // 4) Intentar primero comando BLE (AMS para iPhone/iPad o GMCS) y si no, suspender enlace A2DP
      // lo cual pausa automáticamente la reproducción en cualquier celular o tablet (Android / iOS).
      if (pauseOnPhone) {
        busy = true;
        const bleTry = await apiPost({ action: "media_cmd", cmd: "pause" });
        if (bleTry && bleTry.ok) {
          busy = false;
          showToast("⏸ Pausa enviada al dispositivo.");
          await refreshState(true);
          return;
        }
        pausedByBtDisconnect = true;
        await apiPost({ action: "disconnect" });
        busy = false;
        showToast("⏸ Reproducción pausada en tu dispositivo.");
        await refreshState(true);
      } else {
        await togglePcMuteOnly();
      }
    }

    async function usbToWifi() {
      showToast("Configurando conexión Wi-Fi desde USB...");
      const res = await apiPost({ action: "adb_usb_to_wifi" });
      showToast(res.message || res.error || "Listo", 5000);
      await refreshState(true);
    }

    async function connectAdbWifi() {
      const addr = document.getElementById("adbConnectAddr").value.trim();
      showToast("Conectando a " + addr + "...");
      const res = await apiPost({ action: "adb_connect", address: addr });
      showToast(res.message || res.error || "Resultado recibido", 4500);
      await refreshState(true);
    }

    async function pairAdbWifi() {
      const addr = document.getElementById("adbPairAddr").value.trim();
      const code = document.getElementById("adbPairCode").value.trim();
      showToast("Vinculando con " + addr + "...");
      const res = await apiPost({ action: "adb_pair", address: addr, code: code });
      showToast(res.message || res.error || "Resultado recibido", 5000);
      await refreshState(true);
    }

    async function openPhoneLink() {
      await apiPost({ action: "open_phonelink" });
    }

    function onSliderInput(val) {
      draggingSlider = true;
      document.getElementById("volLabel").textContent = val + "%";
    }

    async function setVolume(val) {
      draggingSlider = false;
      const num = Math.max(0, Math.min(100, parseInt(val, 10) || 0));
      document.getElementById("volSlider").value = num;
      document.getElementById("volLabel").textContent = num + "%";
      const res = await apiPost({ action: "mixer", volume: num, mute: (num === 0) });
      if (res.ok) {
        currentState.mixer.volume = res.volume;
        currentState.mixer.muted = res.muted;
        renderState();
      }
    }

    async function stepVolume(delta) {
      const current = (currentState.mixer && typeof currentState.mixer.volume === "number")
        ? currentState.mixer.volume
        : 100;
      await setVolume(current + delta);
    }

    async function toggleBluetoothLink() {
      const select = document.getElementById("deviceSelect");
      const devId = select.value;

      if (currentState.audioConnected && devId === currentState.activeDeviceId) {
        busy = true;
        pausedByBtDisconnect = false;
        showToast("Desconectando enlace de audio Bluetooth...");
        await apiPost({ action: "disconnect" });
        busy = false;
        showToast("Enlace Bluetooth desconectado.");
        await refreshState(true);
        return;
      }

      if (!devId) {
        showToast("Selecciona un dispositivo en la lista primero.");
        return;
      }

      const rawText = select.options[select.selectedIndex]?.text || "Dispositivo";
      const devName = rawText.replace(" (Conectado)", "");
      busy = true;
      showToast("Conectando audio desde " + devName + "...");
      const res = await apiPost({ action: "connect", deviceId: devId, deviceName: devName });
      busy = false;
      if (res.ok) {
        pausedByBtDisconnect = false;
        showToast("¡Listo! Escuchando " + devName + " en tu PC.");
      } else {
        showToast(res.error || "No se pudo conectar.");
      }
      await refreshState(true);
    }

    async function powerBluetooth(enable) {
      busy = true;
      await apiPost({ action: "bt_power", enable });
      busy = false;
      await refreshState(true);
    }

    async function openBtSettings() {
      await apiPost({ action: "open_settings" });
    }

    window.addEventListener("keydown", (e) => {
      if (e.target.tagName === "SELECT" || e.target.tagName === "INPUT") return;
      if (e.code === "Space") {
        e.preventDefault();
        toggleListenPlayPause();
      } else if (e.code === "ArrowUp") {
        e.preventDefault();
        stepVolume(5);
      } else if (e.code === "ArrowDown") {
        e.preventDefault();
        stepVolume(-5);
      } else if (e.code === "ArrowRight") {
        e.preventDefault();
        sendMediaCommand("next");
      } else if (e.code === "ArrowLeft") {
        e.preventDefault();
        sendMediaCommand("prev");
      }
    });

    function handleWindowCloseDisconnect() {
      const check = document.getElementById("disconnectOnCloseCheck");
      const shouldDisconnect = check ? check.checked : true;
      if (!shouldDisconnect || !currentState.audioConnected) return;
      const data = JSON.stringify({ action: "disconnect" });
      if (navigator.sendBeacon) {
        navigator.sendBeacon("/api/command", new Blob([data], { type: "application/json" }));
      }
      try {
        fetch("/api/command", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: data,
          keepalive: true
        });
      } catch (err) {}
    }

    window.addEventListener("pagehide", handleWindowCloseDisconnect);
    window.addEventListener("beforeunload", handleWindowCloseDisconnect);

    refreshState(true);
    setInterval(() => refreshState(false), 1000);
  </script>
</body>
</html>
"""


bridge = WinRTBridge()
adb_bridge = AdbMediaBridge()
LAST_CLIENT_HEARTBEAT = time.time()


def start_watchdog(bridge_inst: WinRTBridge) -> None:
    def _loop() -> None:
        # Período de gracia inicial de 12 segundos para permitir que el navegador abra y cargue
        time.sleep(12.0)
        while True:
            time.sleep(2.0)
            now = time.time()
            # Si transcurren más de 5.5s sin peticiones de ninguna pestaña/ventana y el audio está conectado:
            if (now - LAST_CLIENT_HEARTBEAT) > 5.5:
                if bridge_inst.audio_connected:
                    print("\n[Watchdog] No se detecta ventana del navegador activa (sin clientes en 5s).")
                    print("[Watchdog] Desconectando automáticamente el audio Bluetooth para liberar tu teléfono...")
                    bridge_inst.send_command({"action": "disconnect"})
                    bridge_inst.audio_connected = False
    t = threading.Thread(target=_loop, daemon=True)
    t.start()


class MiniPlayerHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:
        pass

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/index.html"):
            body = HTML_PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/command":
            global LAST_CLIENT_HEARTBEAT
            LAST_CLIENT_HEARTBEAT = time.time()
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
            try:
                payload = json.loads(raw)
            except Exception:
                payload = {}

            action = payload.get("action", "")
            if action == "state":
                resp = bridge.send_command({"action": "state"})
                resp["adb"] = adb_bridge.state
                resp["lastBtDeviceId"] = load_config().get("last_bt_device_id", "")
                resp["lanIp"] = get_lan_ip()
            elif action == "connect":
                resp = bridge.send_command(payload)
                if resp.get("ok"):
                    save_config(
                        {
                            "last_bt_device_id": str(payload.get("deviceId", "")),
                            "last_bt_device_name": str(payload.get("deviceName", "")),
                        }
                    )
            elif action == "media_cmd":
                cmd = str(payload.get("cmd", "play_pause"))
                if adb_bridge.state.get("connected"):
                    resp = adb_bridge.send_media_key(cmd)
                else:
                    resp = bridge.send_command({"action": "smtc_cmd", "cmd": cmd})
            elif action == "adb_pair":
                resp = adb_bridge.pair_wifi(
                    str(payload.get("address", "")),
                    str(payload.get("code", "")),
                )
            elif action == "adb_connect":
                resp = adb_bridge.connect_wifi(str(payload.get("address", "")))
            elif action == "adb_usb_to_wifi":
                resp = adb_bridge.switch_usb_to_wifi()
            else:
                resp = bridge.send_command(payload)

            body = json.dumps(resp, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)


def open_miniplayer_window(url: str) -> None:
    candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    for browser_path in candidates:
        if os.path.exists(browser_path):
            try:
                subprocess.Popen(
                    [
                        browser_path,
                        f"--app={url}",
                        "--window-size=415,700",
                    ]
                )
                return
            except Exception:
                pass
    webbrowser.open(url)


def main() -> None:
    # Auto-desconectar de forma garantizada si el proceso finaliza o se interrumpe
    atexit.register(bridge.close)

    def _cleanup_handler(*args):
        print("\nCerrando aplicación y desconectando Bluetooth...")
        bridge.close()
        sys.exit(0)

    try:
        signal.signal(signal.SIGINT, _cleanup_handler)
        signal.signal(signal.SIGTERM, _cleanup_handler)
        if hasattr(signal, "SIGBREAK"):
            signal.signal(signal.SIGBREAK, _cleanup_handler)
    except Exception:
        pass

    # Iniciar watchdog de desconexión si se cierran todas las ventanas del navegador
    start_watchdog(bridge)

    # Auto-connect on startup to the last used Bluetooth device (or the first available one)
    try:
        st = bridge.send_command({"action": "state"})
        devs = st.get("devices") or []
        if devs and not st.get("audioConnected"):
            saved_id = load_config().get("last_bt_device_id", "")
            target_dev = next((d for d in devs if d.get("id") == saved_id), devs[0])
            bridge.send_command(
                {
                    "action": "connect",
                    "deviceId": target_dev["id"],
                    "deviceName": target_dev["name"],
                }
            )
    except Exception:
        pass

    server = ThreadingHTTPServer((HOST, PORT), MiniPlayerHandler)
    local_url = f"http://localhost:{PORT}"
    lan_ip = ""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        lan_ip = s.getsockname()[0]
        s.close()
    except Exception:
        pass
    lan_url = f"http://{lan_ip}:{PORT}" if lan_ip else ""

    print("==========================================================")
    print(f" Mini-Reproductor Bluetooth activo:")
    print(f" • Local en PC:       {local_url}")
    if lan_url:
        print(f" • En móvil/red Wi-Fi: {lan_url}")
    print(" No cierres esta consola mientras estés escuchando música.")
    print(" Presiona Ctrl+C para salir.")
    print("==========================================================")

    if "--no-browser" not in sys.argv:
        threading.Timer(0.6, open_miniplayer_window, args=(local_url,)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nCerrando puente de audio Bluetooth...")
    finally:
        bridge.close()
        server.server_close()


if __name__ == "__main__":
    main()
