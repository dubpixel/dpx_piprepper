#!/usr/bin/env python3
# ================================================================================
# WEB GUI - Browser front end for the cardbless SD card blesser
# ================================================================================
# Local-only web UI. Two jobs from the CLI (bless a card that is already
# flashed, SSH cleanup of a booted unit), driven from a page in your browser.
# Flashing is not offered here; flash with Etcher (or the CLI), then bless.
# ================================================================================
# PROJECT: cardbless (dpx_piPrepper)
# ================================================================================
#
# File: cardbless-gui.py
# Purpose: stdlib http.server on 127.0.0.1 that wraps the functions in
#          cardbless-v1.py (boot-volume check, rendering, validated writes, eject).
#          The CLI file is imported unchanged; nothing in it is modified.
# Dependencies: PyYAML (via cardbless-v1.py), stdlib only otherwise
#
# Safety model (this can erase disks, so the server is strict):
#   - binds 127.0.0.1 only; Host header must be localhost/127.0.0.1
#   - every POST needs a random per-run token, sent in the X-Token header
#   - the chosen boot volume is re-checked server side against the same
#     marker files the CLI requires; the browser's word is never trusted
#   - nothing here writes to a raw disk or needs admin rights
#   - passwords are hashed in memory and never written to the log
#
# ================================================================================

from __future__ import annotations

import argparse
import importlib.util
import ipaddress
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
PAGE = HERE / "gui" / "index.html"
VERSION_FILE = HERE.parent / "VERSION"

# The CLI file has a hyphen in its name, so load it by path. It stays the one
# source of truth for rendering, validation of YAML, disk rules and ejecting.
_spec = importlib.util.spec_from_file_location("cardbless_core", HERE / "cardbless-v1.py")
core = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(core)

USER_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
SSH_USER_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$")
TOKEN = secrets.token_urlsafe(24)
MAX_BODY = 64 * 1024


def version() -> str:
    try:
        return VERSION_FILE.read_text().strip()
    except OSError:
        return "?"


# --------------------------------------------------------------------------
# Background job (one at a time; the page polls /api/job)
# --------------------------------------------------------------------------

class Job:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        self.state = "idle"      # idle | running | done | failed
        self.title = ""
        self.steps: list[dict] = []
        self.message = ""

    def start(self, title: str) -> bool:
        with self.lock:
            if self.state == "running":
                return False
            self.reset()
            self.state = "running"
            self.title = title
            return True

    def step(self, name: str) -> None:
        with self.lock:
            for s in self.steps:
                if s["state"] == "running":
                    s["state"] = "done"
            self.steps.append({"name": name, "state": "running"})

    def finish(self, ok: bool, message: str) -> None:
        with self.lock:
            for s in self.steps:
                if s["state"] == "running":
                    s["state"] = "done" if ok else "failed"
            self.state = "done" if ok else "failed"
            self.message = message

    def snapshot(self) -> dict:
        with self.lock:
            return {"state": self.state, "title": self.title,
                    "steps": list(self.steps), "message": self.message}


JOB = Job()


def run_in_thread(title: str, fn) -> bool:
    """Run fn(job_step) in a thread. fn returns a success message or raises."""
    if not JOB.start(title):
        return False

    def runner() -> None:
        try:
            JOB.finish(True, fn(JOB.step))
        except core.Abort:
            JOB.finish(False, "Stopped before finishing. Nothing further was written.")
        except subprocess.CalledProcessError as e:
            JOB.finish(False, f"Command failed: {e}")
        except Exception as e:  # report any failure to the page instead of dying silently
            JOB.finish(False, f"{type(e).__name__}: {e}")

    threading.Thread(target=runner, daemon=True).start()
    return True


# --------------------------------------------------------------------------
# State the page needs (all lists come from the same code the CLI uses)
# --------------------------------------------------------------------------

def list_boot_volumes(root: Path = Path("/Volumes")) -> list[dict]:
    try:
        entries = list(root.iterdir())
    except OSError:
        return []
    return [
        {"path": str(p), "name": p.name}
        for p in sorted(entries)
        if p.is_dir() and all((p / m).exists() for m in core.BOOT_MARKERS)
    ]


def get_state() -> dict:
    return {
        "version": version(),
        "boot_volumes": list_boot_volumes(),
        "sshpass": bool(shutil.which("sshpass")),
    }


# --------------------------------------------------------------------------
# Validation (mirrors the CLI prompts; returns a list of problems)
# --------------------------------------------------------------------------

def hash_password(password: str) -> str:
    for candidate in core.OPENSSL_CANDIDATES:
        exe = shutil.which(candidate) if candidate == "openssl" else (candidate if Path(candidate).exists() else None)
        if not exe:
            continue
        test = subprocess.run([exe, "passwd", "-6", "-salt", "test", "test"], capture_output=True, text=True)
        if test.returncode != 0:
            continue
        res = subprocess.run([exe, "passwd", "-6", "-stdin"], input=password,
                             capture_output=True, text=True, check=True)
        return res.stdout.strip()
    raise RuntimeError("No openssl with -6 support found. Install it with: brew install openssl@3")


def clean_bless_fields(p: dict) -> tuple[dict | None, list[str]]:
    errs: list[str] = []
    username = str(p.get("username", "")).strip() or "dubpixel"
    if username == "companion":
        errs.append("'companion' is the Companion service account, pick another username.")
    elif not USER_RE.match(username):
        errs.append("Username: lowercase letters, digits, hyphens, underscores; start with a letter or underscore.")

    hostname = str(p.get("hostname", "")).strip()
    if not core.HOSTNAME_RE.match(hostname):
        errs.append("Hostname: letters, digits, hyphens only; start with a letter; no leading or trailing hyphen.")

    password = str(p.get("password", ""))
    if not password or password != str(p.get("password2", "")):
        errs.append("Passwords are empty or do not match.")

    net = None
    try:
        prefix = int(str(p.get("prefix", "")).strip())
        ip = ipaddress.ip_address(str(p.get("ip", "")).strip())
        gw = ipaddress.ip_address(str(p.get("gateway", "")).strip())
        subnet = ipaddress.ip_network(f"{ip}/{prefix}", strict=False)
        if ip not in subnet or gw not in subnet:
            errs.append(f"IP and gateway must both be inside {subnet}.")
        else:
            net = {"ip": str(ip), "prefix": prefix, "gateway": str(gw), "vlan": None}
    except ValueError as e:
        errs.append(f"Network: {e}")

    MAX_VLANS = 3
    vlist = p.get("vlans") or []
    if not isinstance(vlist, list) or len(vlist) > MAX_VLANS:
        errs.append(f"At most {MAX_VLANS} extra VLANs.")
        vlist = []
    if vlist and net:
        main = ipaddress.ip_network(f"{net['ip']}/{net['prefix']}", strict=False)
        seen_ids: set[int] = set()
        taken = [main]          # subnets already in use; overlaps cause duplicate routes
        out = []
        for n, v in enumerate(vlist, start=1):
            try:
                vid = int(str(v.get("id", "")).strip())
                if not 1 <= vid <= 4094:
                    raise ValueError("VLAN ID must be 1-4094")
                if vid in seen_ids:
                    raise ValueError(f"VLAN {vid} is listed twice")
                seen_ids.add(vid)
                if v.get("mode") == "static":
                    vprefix = int(str(v.get("prefix", "")).strip())
                    vip = ipaddress.ip_address(str(v.get("ip", "")).strip())
                    vnet = ipaddress.ip_network(f"{vip}/{vprefix}", strict=False)
                    clash = next((t for t in taken if vnet.overlaps(t)), None)
                    if clash:
                        raise ValueError(f"{vnet} overlaps {clash}; pick a different subnet")
                    taken.append(vnet)
                    out.append({"id": vid, "ip": str(vip), "prefix": vprefix})
                else:
                    out.append({"id": vid, "ip": None, "prefix": None})
            except ValueError as e:
                errs.append(f"VLAN {n}: {e}")
        net["vlan"] = out or None

    if errs or net is None:
        return None, errs
    return {"username": username, "hostname": hostname, "password": password, "net": net}, []


# --------------------------------------------------------------------------
# The two jobs
# --------------------------------------------------------------------------

def do_bless(bootfs: Path, f: dict, password_hash: str, disk_label: str, eject_after: bool = True) -> str:
    ok = False
    try:
        core.bless(bootfs, f["username"], password_hash, f["hostname"], f["net"])
        ok = True
    finally:
        core.log_result(f["hostname"], f["net"]["ip"], disk_label, ok)
    if eject_after:
        core.eject(bootfs)
    return f"{f['hostname']} ({f['net']['ip']}) blessed" + (" and ejected." if eject_after else ".")


def start_bless(p: dict) -> tuple[int, dict]:
    fields, errs = clean_bless_fields(p)
    if errs:
        return 400, {"errors": errs}
    chosen = str(p.get("boot", ""))
    if chosen not in {v["path"] for v in list_boot_volumes()}:
        return 400, {"errors": ["That boot volume is no longer mounted. Refresh and pick again."]}
    try:
        pw_hash = hash_password(fields["password"])
    except (RuntimeError, subprocess.CalledProcessError) as e:
        return 400, {"errors": [str(e)]}

    def work(step) -> str:
        step(f"Writing cloud-init files to {chosen}")
        msg = do_bless(Path(chosen), fields, pw_hash, chosen)
        step("Done")
        return msg

    if not run_in_thread("Bless card", work):
        return 409, {"errors": ["Another job is still running."]}
    return 200, {"started": True}


def _ssh_env(password: str) -> dict:
    return {**os.environ, "SSHPASS": password}


def _clean_ssh_target(p: dict) -> tuple[tuple[str, str, str] | None, list[str]]:
    errs = []
    try:
        ip = str(ipaddress.ip_address(str(p.get("ip", "")).strip()))
    except ValueError:
        ip = ""
        errs.append("Enter a valid IP address.")
    user = str(p.get("username", "")).strip() or "dubpixel"
    if not SSH_USER_RE.match(user):
        errs.append("SSH username has characters that are not allowed.")
    pw = str(p.get("password", ""))
    if not pw:
        errs.append("Enter the SSH password.")
    if not shutil.which("sshpass"):
        errs.append("This needs sshpass. Install it with: brew install sshpass")
    return (None, errs) if errs else ((ip, user, pw), [])


def cleanup_check(p: dict) -> tuple[int, dict]:
    target, errs = _clean_ssh_target(p)
    if errs:
        return 400, {"errors": errs}
    ip, user, pw = target
    subprocess.run(["ssh-keygen", "-R", ip], capture_output=True, check=False)
    r = subprocess.run(
        ["sshpass", "-e", "ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=5",
         f"{user}@{ip}", "hostname"],
        capture_output=True, text=True, env=_ssh_env(pw),
    )
    if r.returncode != 0:
        return 400, {"errors": [f"Could not connect: {r.stderr.strip() or 'no response'}"]}
    return 200, {"hostname": r.stdout.strip()}


def cleanup_run(p: dict) -> tuple[int, dict]:
    target, errs = _clean_ssh_target(p)
    if errs:
        return 400, {"errors": errs}
    ip, user, pw = target

    def work(step) -> str:
        step(f"Removing user-data and network-config on {ip}")
        r = subprocess.run(
            ["sshpass", "-e", "ssh", "-o", "ConnectTimeout=5", f"{user}@{ip}",
             "sudo rm -f /boot/firmware/user-data /boot/firmware/network-config"],
            capture_output=True, text=True, env=_ssh_env(pw),
        )
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip() or "ssh command failed")
        return f"Cleaned up {ip}."

    if not run_in_thread("Clean up unit", work):
        return 409, {"errors": ["Another job is still running."]}
    return 200, {"started": True}


# --------------------------------------------------------------------------
# HTTP server
# --------------------------------------------------------------------------

ROUTES = {
    "/api/bless": start_bless,
    "/api/cleanup-check": cleanup_check,
    "/api/cleanup-run": cleanup_run,
}


class Handler(BaseHTTPRequestHandler):
    server_version = "cardbless-gui"

    def log_message(self, fmt, *args) -> None:  # keep the terminal quiet; passwords ride in bodies
        pass

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
        return host in ("127.0.0.1", "localhost")

    def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj: dict) -> None:
        self._send(code, json.dumps(obj).encode())

    def do_GET(self) -> None:
        if not self._host_ok():
            return self._json(HTTPStatus.FORBIDDEN, {"error": "bad host"})
        if self.path in ("/", "/index.html"):
            html = PAGE.read_text(encoding="utf-8").replace("__TOKEN__", TOKEN)
            return self._send(200, html.encode(), "text/html; charset=utf-8")
        if self.path == "/api/state":
            return self._json(200, get_state())
        if self.path == "/api/job":
            return self._json(200, JOB.snapshot())
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if not self._host_ok():
            return self._json(HTTPStatus.FORBIDDEN, {"error": "bad host"})
        if not secrets.compare_digest(self.headers.get("X-Token", ""), TOKEN):
            return self._json(HTTPStatus.FORBIDDEN, {"error": "bad token"})
        route = self.path.partition("?")[0]
        handler = ROUTES.get(route)
        if handler is None:
            return self._json(404, {"error": "not found"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_BODY:
                raise ValueError("bad length")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("body must be an object")
        except (ValueError, json.JSONDecodeError):
            return self._json(400, {"errors": ["Bad request body."]})
        code, obj = handler(payload)
        self._json(code, obj)


def make_server(port: int) -> ThreadingHTTPServer:
    for p in range(port, port + 20):
        try:
            return ThreadingHTTPServer(("127.0.0.1", p), Handler)
        except OSError:
            continue
    raise SystemExit(f"No free port in {port}-{port + 19}.")


def main() -> None:
    ap = argparse.ArgumentParser(description="cardbless web GUI (local only)")
    ap.add_argument("--version", action="version", version=f"cardbless-gui {version()}")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-open", action="store_true", help="do not open the browser")
    args = ap.parse_args()
    if sys.platform != "darwin":
        sys.exit("This tool is macOS-only for now (uses diskutil).")
    srv = make_server(args.port)
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    print(f"cardbless GUI {version()} at {url}  (Ctrl-C to stop)")
    if not args.no_open:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
