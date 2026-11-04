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

import getpass
import ipaddress
import os
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


def ask_choice(prompt: str, options: list[str]) -> int:
    """Plain numbered-list picker. Type a number, or q to quit."""
    print(prompt)
    for i, opt in enumerate(options, start=1):
        print(f"  {i}. {opt}")
    while True:
        raw = input("> ").strip()
        if raw.lower() in ("q", "quit"):
            raise Abort()
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return int(raw) - 1
        print(f"  enter a number 1-{len(options)} (or q to quit)")


def confirm(prompt: str) -> bool:
    raw = input(f"{prompt} [y/N]: ").strip().lower()
    return raw in ("y", "yes")


# --------------------------------------------------------------------------
# Image picker: numbered list of images found recursively under ~/Downloads
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


def pick_image() -> Path:
    """Plain numbered list of every matching image found recursively under
    ~/Downloads. Type a number, or q to quit."""
    start = Path.home() / "Downloads"
    if not start.is_dir():
        start = Path.home()
    found = _find_images_below(start)
    if not found:
        print(f"No .img/.img.xz files found under {start} (searched subfolders too).")
        raw = input("Type a full path to the image (or leave blank to cancel): ").strip()
        if not raw:
            raise Abort()
        path = Path(raw).expanduser()
        if not path.is_file():
            raise Abort()
        return path
    print(f"CompanionPi images found under {start}:")
    for i, p in enumerate(found, start=1):
        size_gb = p.stat().st_size / (1024 ** 3)
        print(f"  {i}. {p.relative_to(start)}  ({size_gb:.2f} GB)")
    print(f"  {len(found) + 1}. Type a full path instead")
    while True:
        raw = input("> ").strip()
        if raw.lower() in ("q", "quit"):
            raise Abort()
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


def ask_vlan(net: dict) -> dict | None:
    """Optionally add one tagged 802.1Q VLAN on eth0 (trunk mode).

    The main network stays untagged on eth0 (works on an access port OR on a
    trunk whose native VLAN is the main one). The extra VLAN is a tagged
    subinterface eth0.<id>, so a trunk port can carry both. Returns None when
    the user declines, else {"id": int, "ip": str|None, "prefix": int|None}
    where ip is None for link-local (self-assigned 169.254.x.x, as Dante uses).
    """
    if not confirm("Also join a tagged VLAN on this port (trunk mode)?"):
        return None
    main_net = ipaddress.ip_network(f"{net['ip']}/{net['prefix']}", strict=False)
    while True:
        vid_raw = ask("VLAN ID (1-4094)", default="218")
        if not (vid_raw.isdigit() and 1 <= int(vid_raw) <= 4094):
            print("  VLAN ID must be a whole number from 1 to 4094")
            continue
        vid = int(vid_raw)
        break
    mode = ask_choice(
        f"Addressing for VLAN {vid}:",
        [
            "Link-local (self-assigned 169.254.x.x, no config; what Dante uses)",
            "Static IP",
        ],
    )
    if mode == 0:
        return {"id": vid, "ip": None, "prefix": None}
    while True:
        ip_raw = ask(f"Static IP on VLAN {vid}")
        prefix_raw = ask("Network prefix length", default=str(net["prefix"]))
        try:
            prefix = int(prefix_raw)
            vlan_net = ipaddress.ip_network(f"{ip_raw}/{prefix}", strict=False)
            ip = ipaddress.ip_address(ip_raw)
        except ValueError as e:
            print(f"  invalid input: {e}")
            continue
        # Overlapping subnets on two interfaces cause duplicate connected
        # routes and traffic leaving the wrong port, so refuse them outright.
        if vlan_net.overlaps(main_net):
            print(f"  {vlan_net} overlaps the main network {main_net}; pick a different subnet")
            continue
        return {"id": vid, "ip": str(ip), "prefix": prefix}


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


def render_network_config(ip: str, prefix: int, gateway: str, vlan: dict | None = None) -> str:
    text = (
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
    if vlan:
        # Tagged subinterface only for the extra VLAN; the main network stays
        # untagged on eth0 (never create a tagged twin of it). No gateway or
        # DNS here: the extra VLAN is an isolated segment.
        text += (
            "  vlans:\n"
            f"    eth0.{vlan['id']}:\n"
            f"      id: {vlan['id']}\n"
            "      link: eth0\n"
            "      dhcp4: false\n"
        )
        if vlan["ip"] is None:
            text += "      link-local: [ipv4]\n"
        else:
            text += f"      addresses:\n        - {vlan['ip']}/{vlan['prefix']}\n"
    return text


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
    write_validated(bootfs / "network-config", render_network_config(net["ip"], net["prefix"], net["gateway"], net.get("vlan")))
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

def cleanup_over_ssh() -> None:
    """Post-boot step: SSH into an already-booted unit and remove the
    cloud-init user-data/network-config files from its boot partition, so
    the password hash doesn't sit around on a world-readable FAT partition.
    This has to happen over the network after the card is booted in the Pi -
    it can't be chained onto bless(), which runs before that."""
    if not shutil.which("sshpass"):
        print("This needs sshpass. Install it with: brew install sshpass")
        raise Abort()
    ip = ask("IP address of the booted unit")
    username = ask("SSH username", default="dubpixel")
    password = getpass.getpass("SSH password: ")

    subprocess.run(["ssh-keygen", "-R", ip], capture_output=True, check=False)
    env = {**os.environ, "SSHPASS": password}
    check = subprocess.run(
        ["sshpass", "-e", "ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=5",
         f"{username}@{ip}", "hostname"],
        capture_output=True, text=True, env=env,
    )
    if check.returncode != 0:
        print(f"  could not connect: {check.stderr.strip()}")
        raise Abort()
    hostname = check.stdout.strip()
    print(f"  connected to {hostname} ({ip})")
    if not confirm(f"Remove user-data/network-config from {hostname}'s boot partition?"):
        raise Abort()
    result = subprocess.run(
        ["sshpass", "-e", "ssh", "-o", "ConnectTimeout=5", f"{username}@{ip}",
         "sudo rm -f /boot/firmware/user-data /boot/firmware/network-config"],
        capture_output=True, text=True, env=env,
    )
    if result.returncode == 0:
        print(f"\nOK: cleaned up {hostname} ({ip}).")
    else:
        print(f"\nFAIL: {result.stderr.strip()}")


def run_one_card() -> None:
    print("\n=== cardbless: prep a CompanionPi SD card ===")
    mode = ask_choice(
        "What do you need to do?",
        [
            "Flash a new image, then bless it",
            "Bless a card that's already flashed",
            "Clean up cloud-init files on a booted unit (over SSH)",
        ],
    )
    if mode == 2:
        cleanup_over_ssh()
        return

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
    net["vlan"] = ask_vlan(net)

    print(f"\nAbout to bless {bootfs}:")
    print(f"  user: {username}   hostname: {hostname}   ip: {net['ip']}/{net['prefix']}   gw: {net['gateway']}")
    if net["vlan"]:
        v = net["vlan"]
        addr = "link-local (169.254.x.x)" if v["ip"] is None else f"{v['ip']}/{v['prefix']}"
        print(f"  tagged VLAN {v['id']} on eth0.{v['id']}: {addr}")
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
