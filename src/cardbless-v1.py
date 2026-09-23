#!/usr/bin/env python3
# ================================================================================
# CLI TOOL - Interactive SD card flasher + blesser for CompanionPi units
# ================================================================================
# Walks the user through picking an image, picking a disk, and answering a few
# questions, then writes the per-unit cloud-init files (or flashes first).
# ================================================================================
# PROJECT: cardbless (dpx_piPrepper)
# ================================================================================
#
# File: cardbless-v1.py
# Purpose: interactive-only CLI, no flags. See doc/CLAUDE.md for the full spec.
# Dependencies: PyYAML (pip install pyyaml)
#
# ================================================================================

from __future__ import annotations

import curses
import getpass
import ipaddress
import plistlib
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    import yaml
except ImportError:
    print("This tool needs PyYAML. Install it with: pip3 install pyyaml")
    sys.exit(1)

MAX_DISK_BYTES = 256 * 1024 ** 3  # refuse to touch anything bigger than this
BOOT_MARKERS = ("cmdline.txt", "config.txt", "meta-data")
LEGACY_FILES = ("userconf.txt", "userconf", "ssh")
TIMEZONE = "America/New_York"  # matches the known-good example in doc/CLAUDE.md
LOG_FILE = Path(__file__).resolve().parent.parent / "cardbless.log"

RPI_IMAGER = "/Applications/Raspberry Pi Imager.app/Contents/MacOS/rpi-imager"
OPENSSL_CANDIDATES = ["/opt/homebrew/opt/openssl@3/bin/openssl", "/usr/local/opt/openssl@3/bin/openssl", "openssl"]


class Abort(Exception):
    """Raised to bail out of the current card cleanly (user chose to quit)."""


# --------------------------------------------------------------------------
# Small interactive helpers
# --------------------------------------------------------------------------

def ask(prompt: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        raw = input(f"{prompt}{suffix}: ").strip()
        if raw:
            return raw
        if default is not None:
            return default
        print("  (required, try again)")


def _visible_rows(h: int) -> int:
    """How many list rows fit below the title/hint lines."""
    return max(h - 4, 1)


def _clamp_offset(cursor: int, offset: int, visible: int, total: int) -> int:
    if cursor < offset:
        offset = cursor
    elif cursor >= offset + visible:
        offset = cursor - visible + 1
    return max(0, min(offset, max(0, total - visible)))


def _draw_menu(stdscr, title: str, hint: str, rows: list[str], cursor: int, offset: int) -> None:
    stdscr.erase()
    h, w = stdscr.getmaxyx()
    visible = _visible_rows(h)
    more_above = offset > 0
    more_below = offset + visible < len(rows)
    scroll_note = ""
    if more_above and more_below:
        scroll_note = "  (more above/below - scroll with up/down)"
    elif more_above:
        scroll_note = "  (more above - scroll up)"
    elif more_below:
        scroll_note = "  (more below - scroll down)"
    stdscr.addstr(0, 0, title[: max(w - 1, 0)])
    stdscr.addstr(1, 0, (hint + scroll_note)[: max(w - 1, 0)])
    to_draw = list(range(offset, min(offset + visible, len(rows))))
    drawn = 0
    for i in to_draw:
        y = 3 + (i - offset)
        attr = curses.A_REVERSE if i == cursor else curses.A_NORMAL
        try:
            stdscr.addstr(y, 2, rows[i][: max(w - 3, 0)], attr)
            drawn += 1
        except curses.error:
            pass
    stdscr.refresh()
    if to_draw and drawn == 0:
        # Every row failed to render (terminal/curses quirk) - don't fail silently,
        # let the caller catch this and fall back to a plain list instead.
        raise RuntimeError("curses could not draw any menu rows in this terminal")


def _menu_loop(stdscr, title: str, options: list[str]) -> int | None:
    curses.curs_set(0)
    cursor = 0
    offset = 0
    while True:
        h, _ = stdscr.getmaxyx()
        offset = _clamp_offset(cursor, offset, _visible_rows(h), len(options))
        _draw_menu(stdscr, title, "up/down or j/k move, enter select, q cancel", options, cursor, offset)
        ch = stdscr.getch()
        if ch in (ord("q"), 27):
            return None
        elif ch in (curses.KEY_UP, ord("k")):
            cursor = max(0, cursor - 1)
        elif ch in (curses.KEY_DOWN, ord("j")):
            cursor = min(len(options) - 1, cursor + 1)
        elif ch in (10, 13, curses.KEY_ENTER):
            return cursor


def _fallback_choice(prompt: str, options: list[str]) -> int | None:
    """Plain numbered-list picker, used if curses can't run in this terminal."""
    print(prompt)
    for i, opt in enumerate(options, start=1):
        print(f"  {i}. {opt}")
    while True:
        raw = input("> ").strip()
        if raw.lower() in ("q", "quit"):
            return None
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return int(raw) - 1
        print(f"  enter a number 1-{len(options)} (or q to quit)")


def ask_choice(prompt: str, options: list[str]) -> int:
    """Arrow-key menu (curses); falls back to a numbered list if curses fails."""
    try:
        result = curses.wrapper(_menu_loop, prompt, options)
    except Exception as e:
        print(f"(arrow-key menu unavailable in this terminal: {e}; using a plain list instead)")
        result = _fallback_choice(prompt, options)
    if result is None:
        raise Abort()
    return result


def confirm(prompt: str) -> bool:
    raw = input(f"{prompt} [y/N]: ").strip().lower()
    return raw in ("y", "yes")


# --------------------------------------------------------------------------
# Image picker: arrow-key filesystem browser, starts in ~/Downloads
# --------------------------------------------------------------------------

IMAGE_EXTS = (".img", ".img.xz")


def _find_images_below(root: Path, max_depth: int = 6) -> list[Path]:
    """Recursively find image files at or below root, skipping hidden dirs."""
    found = []
    stack = [(root, 0)]
    while stack:
        d, depth = stack.pop()
        try:
            entries = list(d.iterdir())
        except (PermissionError, FileNotFoundError):
            continue
        for p in entries:
            if p.name.startswith("."):
                continue
            if p.is_dir():
                if depth < max_depth:
                    stack.append((p, depth + 1))
            elif p.name.endswith(IMAGE_EXTS):
                found.append(p)
    return sorted(found, key=lambda p: str(p.relative_to(root)).lower())


def _browse_loop(stdscr, start: Path):
    curses.curs_set(0)
    current = start
    cursor = 0
    offset = 0
    while True:
        try:
            entries = sorted(current.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except (PermissionError, FileNotFoundError):
            entries = []
        rows: list[tuple[str, str, Path | None]] = []
        if current != current.parent:
            rows.append(("up", "..", current.parent))
        dirs = [p for p in entries if p.is_dir() and not p.name.startswith(".")]
        for p in dirs:
            rows.append(("dir", p.name + "/", p))
        found = _find_images_below(current)
        for p in found:
            rel = p.relative_to(current)
            size_gb = p.stat().st_size / (1024 ** 3)
            rows.append(("file", f"{rel}  ({size_gb:.2f} GB)", p))
        if not dirs and not found:
            rows.append(("none", "(nothing to show here)", None))
        cursor = min(cursor, len(rows) - 1)

        title = f"Pick a CompanionPi image  -  {current}"
        hint = "up/down or j/k move, enter open/select, backspace up a dir, q cancel"
        h, _ = stdscr.getmaxyx()
        offset = _clamp_offset(cursor, offset, _visible_rows(h), len(rows))
        _draw_menu(stdscr, title, hint, [r[1] for r in rows], cursor, offset)
        ch = stdscr.getch()
        if ch in (ord("q"), 27):
            return None
        elif ch in (curses.KEY_UP, ord("k")):
            cursor = max(0, cursor - 1)
        elif ch in (curses.KEY_DOWN, ord("j")):
            cursor = min(len(rows) - 1, cursor + 1)
        elif ch in (curses.KEY_BACKSPACE, 127, 8):
            current = current.parent
            cursor = 0
            offset = 0
        elif ch in (10, 13, curses.KEY_ENTER):
            kind, _, path = rows[cursor]
            if kind == "file":
                return path
            elif kind in ("dir", "up"):
                current = path
                cursor = 0
                offset = 0


def _fallback_pick_image(start: Path) -> Path | None:
    """Plain numbered list of every matching image found recursively under start."""
    found = _find_images_below(start)
    if not found:
        print(f"No .img/.img.xz files found under {start} (searched subfolders too).")
        raw = input("Type a full path to the image (or leave blank to cancel): ").strip()
        if not raw:
            return None
        path = Path(raw).expanduser()
        return path if path.is_file() else None
    print(f"CompanionPi images found under {start}:")
    for i, p in enumerate(found, start=1):
        size_gb = p.stat().st_size / (1024 ** 3)
        print(f"  {i}. {p.relative_to(start)}  ({size_gb:.2f} GB)")
    print(f"  {len(found) + 1}. Type a full path instead")
    while True:
        raw = input("> ").strip()
        if raw.lower() in ("q", "quit"):
            return None
        if raw.isdigit():
            n = int(raw)
            if 1 <= n <= len(found):
                return found[n - 1]
            if n == len(found) + 1:
                raw2 = input("Full path: ").strip()
                path = Path(raw2).expanduser()
                if path.is_file():
                    return path
                print(f"  no such file: {path}")
                continue
        print(f"  enter a number 1-{len(found) + 1} (or q to quit)")


def pick_image() -> Path:
    """Plain numbered list, recursive under ~/Downloads. No curses here -
    the arrow-key file browser was unreliable across terminals; this isn't."""
    start = Path.home() / "Downloads"
    if not start.is_dir():
        start = Path.home()
    result = _fallback_pick_image(start)
    if result is None:
        raise Abort()
    return result


# --------------------------------------------------------------------------
# Disk picker (diskutil, external + physical only)
# --------------------------------------------------------------------------

def list_external_disks() -> list[dict]:
    out = subprocess.run(
        ["diskutil", "list", "-plist", "external", "physical"],
        capture_output=True, check=True,
    ).stdout
    plist = plistlib.loads(out)
    disks = []
    for disk_id in plist.get("WholeDisks", []):
        info = subprocess.run(
            ["diskutil", "info", "-plist", disk_id], capture_output=True, check=True,
        ).stdout
        d = plistlib.loads(info)
        if d.get("Internal") or d.get("VirtualOrPhysical") == "Virtual":
            continue
        disks.append({
            "id": disk_id,
            "size": d.get("TotalSize", 0),
            "media_name": d.get("MediaName", "?"),
            "volume_names": [
                p.get("VolumeName", "") for p in plist.get("AllDisksAndPartitions", [])
                if p.get("DeviceIdentifier") == disk_id
                for p in p.get("Partitions", [])
            ],
        })
    return disks


def pick_disk() -> str:
    disks = [d for d in list_external_disks() if d["size"] <= MAX_DISK_BYTES]
    if not disks:
        print("No eligible external disks found (external, physical, under "
              f"{MAX_DISK_BYTES // 1024**3} GB). Insert the card and try again.")
        raise Abort()
    options = []
    for d in disks:
        vols = ", ".join(v for v in d["volume_names"] if v) or "(no volumes)"
        options.append(f"{d['id']}  {d['size'] / 1024**3:.1f} GB  {d['media_name']}  [{vols}]")
    idx = ask_choice("Pick the destination disk (THIS WILL BE ERASED):", options)
    chosen = disks[idx]
    typed = ask(f"Type '{chosen['id']}' to confirm you want to erase it")
    if typed != chosen["id"]:
        print("  that didn't match, aborting for safety.")
        raise Abort()
    return chosen["id"]


# --------------------------------------------------------------------------
# Flashing
# --------------------------------------------------------------------------

def flash_image(image_path: Path, disk_id: str) -> None:
    device = f"/dev/{disk_id}"
    if Path(RPI_IMAGER).exists():
        print(f"\nFlashing with rpi-imager CLI to {device} ...")
        print("(this writes raw to disk and needs admin rights; you may be asked for your password)")
        subprocess.run(["sudo", RPI_IMAGER, "--cli", str(image_path), device], check=True)
        return
    print(f"\nrpi-imager not found at {RPI_IMAGER}; falling back to dd.")
    subprocess.run(["diskutil", "unmountDisk", device], check=True)
    rdevice = f"/dev/r{disk_id}"
    if image_path.suffix == ".xz":
        pv = shutil.which("pv")
        src_cmd = ["xz", "-dc", str(image_path)]
        print("Writing (this takes a while, no progress if 'pv' isn't installed) ...")
        xz_proc = subprocess.Popen(src_cmd, stdout=subprocess.PIPE)
        dd_cmd = ["sudo", "dd", f"of={rdevice}", "bs=4m"]
        if pv:
            pv_proc = subprocess.Popen(["pv"], stdin=xz_proc.stdout, stdout=subprocess.PIPE)
            xz_proc.stdout.close()
            subprocess.run(dd_cmd, stdin=pv_proc.stdout, check=True)
        else:
            subprocess.run(dd_cmd, stdin=xz_proc.stdout, check=True)
    else:
        subprocess.run(["sudo", "dd", f"if={image_path}", f"of={rdevice}", "bs=4m", "status=progress"], check=True)
    subprocess.run(["diskutil", "mountDisk", device], check=True)


def wait_for_bootfs(timeout: int = 30) -> Path:
    deadline = time.time() + timeout
    while time.time() < deadline:
        p = Path("/Volumes/bootfs")
        if p.is_dir() and (p / "cmdline.txt").exists():
            return p
        time.sleep(1)
    raise Abort()


# --------------------------------------------------------------------------
# Bless-mode helpers
# --------------------------------------------------------------------------

def find_boot_volume() -> Path:
    candidates = [
        p for p in Path("/Volumes").iterdir()
        if p.is_dir() and all((p / m).exists() for m in BOOT_MARKERS)
    ]
    if not candidates:
        print("No mounted volume looks like a Pi boot partition "
              f"(need {', '.join(BOOT_MARKERS)}). Mount the card and try again.")
        raise Abort()
    if len(candidates) == 1:
        return candidates[0]
    idx = ask_choice("Multiple boot-looking volumes mounted, pick one:", [str(p) for p in candidates])
    return candidates[idx]


HOSTNAME_RE = re.compile(r"^[a-zA-Z]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$")


def ask_hostname() -> str:
    while True:
        h = ask("Hostname (mDNS name, becomes <hostname>.local)")
        if HOSTNAME_RE.match(h):
            return h
        print("  letters, digits, hyphens only; must start with a letter, no leading/trailing hyphen.")


def ask_username() -> str:
    while True:
        u = ask("Admin username", default="dubpixel")
        if u == "companion":
            print("  'companion' is the Companion service account, pick something else.")
            continue
        if re.match(r"^[a-z_][a-z0-9_-]{0,31}$", u):
            return u
        print("  lowercase letters/digits/hyphens/underscores, must start with a letter or underscore.")


def ask_password_hash() -> str:
    openssl = None
    for candidate in OPENSSL_CANDIDATES:
        path = candidate if candidate == "openssl" else Path(candidate)
        exe = shutil.which(candidate) if candidate == "openssl" else (str(path) if path.exists() else None)
        if not exe:
            continue
        test = subprocess.run([exe, "passwd", "-6", "-salt", "test", "test"], capture_output=True, text=True)
        if test.returncode == 0:
            openssl = exe
            break
    if not openssl:
        print("No openssl with -6 support found. Install it with: brew install openssl@3")
        raise Abort()
    while True:
        pw1 = getpass.getpass("Password for the admin user: ")
        pw2 = getpass.getpass("Confirm password: ")
        if pw1 == pw2 and pw1:
            break
        print("  passwords didn't match (or were empty), try again.")
    result = subprocess.run([openssl, "passwd", "-6", "-stdin"], input=pw1, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def ask_network(gateway_hint: str | None = None) -> dict:
    while True:
        ip_raw = ask("Static IP for this unit (e.g. 10.196.11.25)")
        prefix_raw = ask("Network prefix length (e.g. 15 for a /15)", default="24")
        gw_raw = ask("Gateway", default=gateway_hint)
        try:
            prefix = int(prefix_raw)
            net = ipaddress.ip_network(f"{ip_raw}/{prefix}", strict=False)
            ip = ipaddress.ip_address(ip_raw)
            gw = ipaddress.ip_address(gw_raw)
        except ValueError as e:
            print(f"  invalid input: {e}")
            continue
        if ip not in net or gw not in net:
            print(f"  IP and gateway must both be inside {net}")
            continue
        return {"ip": str(ip), "prefix": prefix, "gateway": str(gw)}


# --------------------------------------------------------------------------
# Rendering + writing
# --------------------------------------------------------------------------

def render_user_data(username: str, password_hash: str, hostname: str) -> str:
    return (
        "#cloud-config\n\n"
        f"hostname: {hostname}\n"
        "manage_etc_hosts: true\n"
        f"timezone: {TIMEZONE}\n\n"
        "users:\n"
        f"  - name: {username}\n"
        "    groups: users,adm,dialout,audio,netdev,video,plugdev,cdrom,games,input,gpio,spi,i2c,render,sudo\n"
        "    shell: /bin/bash\n"
        "    lock_passwd: false\n"
        f'    passwd: "{password_hash}"\n'
        "    sudo: ALL=(ALL) NOPASSWD:ALL\n\n"
        "enable_ssh: true\n"
        "ssh_pwauth: true\n"
    )


def render_network_config(ip: str, prefix: int, gateway: str) -> str:
    return (
        "network:\n"
        "  version: 2\n"
        "  renderer: NetworkManager\n"
        "  ethernets:\n"
        "    eth0:\n"
        "      dhcp4: false\n"
        "      addresses:\n"
        f"        - {ip}/{prefix}\n"
        "      routes:\n"
        "        - to: default\n"
        f"          via: {gateway}\n"
        "      nameservers:\n"
        f"        addresses: [{gateway}]\n"
    )


def write_validated(path: Path, content: str) -> None:
    yaml.safe_load(content)  # raises on bad YAML before we touch disk
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write(content)
    readback = path.read_text(encoding="utf-8")
    if readback != content:
        raise RuntimeError(f"read-back mismatch on {path}, aborting")


def strip_ip_kernel_param(bootfs: Path) -> None:
    cmdline = bootfs / "cmdline.txt"
    text = cmdline.read_text(encoding="utf-8")
    tokens = text.strip().split(" ")
    kept = [t for t in tokens if not t.startswith("ip=")]
    if len(kept) != len(tokens):
        print("  found a stray ip= kernel param in cmdline.txt, stripping it.")
        with cmdline.open("w", encoding="utf-8", newline="\n") as f:
            f.write(" ".join(kept) + "\n")


def clean_legacy_files(bootfs: Path) -> None:
    for name in LEGACY_FILES:
        p = bootfs / name
        if p.exists():
            print(f"  removing legacy first-boot file: {name}")
            p.unlink()


def bless(bootfs: Path, username: str, password_hash: str, hostname: str, net: dict) -> None:
    write_validated(bootfs / "user-data", render_user_data(username, password_hash, hostname))
    write_validated(bootfs / "network-config", render_network_config(net["ip"], net["prefix"], net["gateway"]))
    strip_ip_kernel_param(bootfs)
    clean_legacy_files(bootfs)
    # dot_clean routinely fails on macOS's own hidden system folders on FAT
    # volumes (.Spotlight-V100, .fseventsd, etc); that's expected, not a real
    # error, so swallow its output instead of scaring the user with it.
    subprocess.run(["dot_clean", str(bootfs)], check=False, capture_output=True)


def eject(disk_or_path) -> None:
    subprocess.run(["diskutil", "eject", str(disk_or_path)], check=False)


def log_result(hostname: str, ip: str, disk_id: str, ok: bool) -> None:
    stamp = datetime.now().isoformat(timespec="seconds")
    status = "OK" if ok else "FAIL"
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(f"{stamp}  {status}  hostname={hostname} ip={ip} disk={disk_id}\n")


# --------------------------------------------------------------------------
# Main flow
# --------------------------------------------------------------------------

def run_one_card() -> None:
    print("\n=== cardbless: prep a CompanionPi SD card ===")
    mode = ask_choice(
        "What do you need to do?",
        ["Flash a new image, then bless it", "Bless a card that's already flashed"],
    )

    disk_id = None
    if mode == 0:
        image_path = pick_image()
        disk_id = pick_disk()
        flash_image(image_path, disk_id)
        bootfs = wait_for_bootfs()
    else:
        bootfs = find_boot_volume()

    username = ask_username()
    password_hash = ask_password_hash()
    hostname = ask_hostname()
    net = ask_network()

    print(f"\nAbout to bless {bootfs}:")
    print(f"  user: {username}   hostname: {hostname}   ip: {net['ip']}/{net['prefix']}   gw: {net['gateway']}")
    if not confirm("Proceed?"):
        raise Abort()

    ok = False
    try:
        bless(bootfs, username, password_hash, hostname, net)
        ok = True
    finally:
        log_result(hostname, net["ip"], disk_id or str(bootfs), ok)
        if ok:
            eject(disk_id if disk_id else bootfs)
            print(f"\nOK: {hostname} ({net['ip']}) blessed and ejected.")
        else:
            print(f"\nFAIL: {hostname} ({net['ip']}) did not complete cleanly.")


def main() -> None:
    if sys.platform != "darwin":
        print("This tool is macOS-only for now (uses diskutil).")
        sys.exit(1)
    while True:
        try:
            run_one_card()
        except Abort:
            print("\nStopped.")
        except subprocess.CalledProcessError as e:
            print(f"\nCommand failed: {e}")
        if not confirm("\nPrep another card?"):
            break


if __name__ == "__main__":
    main()
