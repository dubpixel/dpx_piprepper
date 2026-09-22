# CLAUDE.md: cardbless (CompanionPi SD card flasher + blesser)

Spec version: v1 (2026-09-22)

## What this is

A small macOS command-line utility that prepares Raspberry Pi SD cards running the
CompanionPi image (Bitfocus Companion v5.x) so each unit boots with:

- a known admin user (no default user exists on current images)
- SSH enabled
- a static IPv4 address, unique per unit
- a unique hostname per unit

Two jobs:

1. **bless**: card is already flashed; write per-unit cloud-init files to its boot partition.
2. **flash + bless**: write the CompanionPi image to the card first, then bless it.

It should support batch runs: work through a list of units, prompting "insert next card",
detect it, do the job, eject, repeat.

## Owner preferences (follow these)

- macOS (Apple Silicon) is the primary platform. Linux support is nice-to-have, not required for v1.
- Code: clean, well commented, readable for a beginner-to-intermediate engineer.
- Version script filenames: `cardbless-v1.py`, `cardbless-v2.py`, etc.
- Ask before making breaking changes.
- When documenting changes, give line numbers and surrounding context.
- Never use em dashes in output text or docs; use semicolons, colons, or commas.
- Metric units where units come up.
- Do not guess. If something about the image, rpi-imager, or macOS behavior is uncertain, test it
  or say so. Verify against the actual card contents rather than assuming.

## Suggested stack

Python 3 single file, stdlib first. Acceptable extra deps: PyYAML (for validation). Shell out to
`diskutil`, `openssl`, `dd`, `xz`, and optionally rpi-imager. If you think bash is a better fit,
propose it before switching.

## Verified facts about the target image

These were confirmed by inspecting a real card and the Bitfocus build repo on 2026-09-22.

- CompanionPi images are built from **Raspberry Pi OS Lite arm64, Debian Trixie**
  (build repo `bitfocus/companion-pi`, file `companionpi.pkr.hcl`, e.g.
  `2026-09-15-raspios-trixie-arm64-lite.img.xz`).
- Trixie images after 2025-11-24 use **cloud-init** for first boot. The FAT32 boot partition
  mounts on macOS as **`/Volumes/bootfs`** and contains:
  - `meta-data` (leave untouched)
  - `user-data` (stock file is a fully commented-out template)
  - `network-config`
  - plus `cmdline.txt`, `config.txt`, etc.
- The root filesystem is ext4 and is **not** readable on macOS. All configuration must happen
  through the boot partition.
- `user-data` must start with the literal line `#cloud-config` or cloud-init ignores it.
- Networking on this OS: cloud-init writes `network-config` into netplan (`/etc/netplan/`),
  netplan renders to NetworkManager. Connections show in `nmcli` with a `netplan-` prefix.
- The username must **not** be `companion` (that is the service account Companion runs as).
- Hostnames: letters, digits, hyphens only. No underscores (RFC 952/1123).

## Known-good output files (this exact content booted and worked)

`user-data`:

```yaml
#cloud-config

hostname: comp-operator
manage_etc_hosts: true
timezone: America/New_York

users:
  - name: dubpixel
    groups: users,adm,dialout,audio,netdev,video,plugdev,cdrom,games,input,gpio,spi,i2c,render,sudo
    shell: /bin/bash
    lock_passwd: false
    passwd: "<SHA-512 crypt hash, $6$...>"
    sudo: ALL=(ALL) NOPASSWD:ALL

enable_ssh: true
ssh_pwauth: true
```

`network-config`:

```yaml
network:
  version: 2
  renderer: NetworkManager
  ethernets:
    eth0:
      dhcp4: false
      addresses:
        - 10.196.11.25/15
      routes:
        - to: default
          via: 10.196.11.1
      nameservers:
        addresses: [10.196.11.1]
```

`cmdline.txt` must stay stock (single line, no `ip=` kernel parameter):

```
console=serial0,115200 console=tty1 root=PARTUUID=<varies> rootfstype=ext4 fsck.repair=yes rootwait resize
```

Note: PARTUUID differs per image build. Never hardcode it; only check for and strip a stray `ip=`.

## Configuration model

One config file (YAML) for shared settings plus a list of units. Example:

```yaml
image: ~/Images/companionpi-v5.x.img.xz   # only needed for flash mode
user:
  name: dubpixel
  password_hash: "$6$..."                 # store the HASH, never the plaintext
timezone: America/New_York
network:
  prefix: 15
  gateway: 10.196.11.1
  dns: [10.196.11.1]
units:
  - hostname: comp-operator
    ip: 10.196.11.25
  - hostname: comp-td
    ip: 10.196.11.26
```

Also provide a helper command to generate the password hash interactively (prompt with no echo,
confirm, output the `$6$` hash). Use `openssl passwd -6`. Check whether macOS's system
`openssl` (LibreSSL) supports `-6`; if not, look for Homebrew `openssl@3` and tell the user to
install it if missing. Do not use Python's `crypt` module (removed in Python 3.13).

## Required behavior

### Card detection and safety (most important part)

Writing to the wrong disk would wipe a drive. Be paranoid.

- Only consider disks from `diskutil list -plist external physical`.
- Refuse any disk that is internal, is the boot volume, or is larger than a configurable max size
  (default 256 GB).
- Before flashing, show: device node, size, media name, and current volume names; require the
  user to type the disk identifier (e.g. `disk6`) to confirm. Provide a `--yes` flag only for
  batch mode after the first confirmation of a session, and even then re-check it is the same
  kind of device (external, physical, under max size).
- Bless mode does not need the typed confirmation, but must confirm the mounted boot volume
  actually looks like a Pi boot partition (`cmdline.txt`, `config.txt`, and `meta-data` present)
  before writing anything.

### Flash mode

Offer two backends; pick via flag, default to the first one that is available:

1. **rpi-imager CLI** (Imager 2.x):
   `"/Applications/Raspberry Pi Imager.app/Contents/MacOS/rpi-imager" --cli <image> /dev/diskN`
   Accepts compressed images. Do NOT rely on its `--cloudinit-userdata` / `--cloudinit-networkconfig`
   flags: there is a reported bug (rpi-imager issue #1385, Dec 2025) where on macOS the image was
   written but those files were never copied. Do the bless step ourselves afterward regardless.
   Check whether it needs sudo on macOS and handle accordingly.
2. **dd fallback**: `diskutil unmountDisk /dev/diskN`, then
   `xz -dc image.img.xz | sudo dd of=/dev/rdiskN bs=4m` (use `rdisk` for speed; handle
   uncompressed `.img` too). Show progress if practical (e.g. `status=progress` if the macOS dd
   supports it, otherwise pipe through `pv` if installed).

After flashing: `diskutil mountDisk /dev/diskN`, wait for `/Volumes/bootfs` to appear (with a
timeout), then bless.

balena-cli (`balena local flash`) also exists but is out of scope for v1; heavy install aimed at
balenaCloud.

### Bless mode

For the target unit:

1. Render `user-data` and `network-config` from templates + config.
2. Validate both with PyYAML before writing (catch indentation errors, bad keys).
3. Validate inputs: hostname rules above; IP is valid IPv4 and inside the configured subnet along
   with the gateway; no duplicate IPs or hostnames in the units list.
4. Write files with **LF line endings, UTF-8, no BOM**, overwriting the existing ones.
5. Leave `meta-data` untouched.
6. Check `cmdline.txt`: if it contains an `ip=` parameter, warn and offer to strip it. Never
   add a newline or split the line.
7. Delete stale legacy files if present: `userconf.txt`, `userconf`, empty `ssh`. (They belong
   to the old pre-cloud-init first-boot system; don't mix the two.)
8. Read the files back and diff against what was intended; fail loudly on mismatch.
9. Run `dot_clean /Volumes/bootfs` (macOS drops `._*` AppleDouble files on FAT volumes).
10. `diskutil eject` the disk.
11. Print a summary line: hostname, IP, disk, OK/FAIL. Append it to a local log file
    (`cardbless.log`) with a timestamp.

### Batch loop

`cardbless batch --config show.yaml [--flash]`: iterate units in order (or allow `--start <hostname>`),
prompt to insert a card, detect, flash (optional), bless, eject, then prompt for the next.
Allow skip and quit at each prompt.

## Gotchas learned the hard way (don't reintroduce these)

- Never double-click files on the boot partition in Finder. FAT has no permission bits, so macOS
  treats every file as executable; Finder hands it to Terminal and runs it. A second attempt gets
  blocked by Gatekeeper with a misleading "damaged" message. The tool should never `open` these
  files.
- When writing the hash through a shell, `$6$...` gets expanded unless single-quoted or written
  via a quoted heredoc (`<< 'EOF'`). Writing from Python avoids this entirely; do that.
- An earlier manual attempt accidentally pasted the heredoc wrapper lines (`cat > ... << 'EOF'`
  and `EOF`) into `network-config`, making it invalid YAML. YAML validation plus read-back
  verification should catch this class of error.
- Kernel `ip=` in `cmdline.txt` was tried first and is the wrong approach on this OS; cloud-init
  `network-config` is the right one.

## Nice-to-have (not v1)

- Post-boot verify command: ping each unit's IP, then check `http://<ip>:8000` responds.
- Optional SSH public key in `user-data` (`ssh_authorized_keys`) instead of, or alongside, password.
- Linux support (`lsblk` instead of `diskutil`, mount point differs).

## Open questions for the owner

- Subnet: current config uses a /15 (`255.254.0.0`). Confirm that is correct for the show network.
- DNS: gateway is used as DNS as a placeholder. Real resolver, or none?
- Keep `NOPASSWD` sudo, or require the password for sudo?
- Where should the CompanionPi `.img.xz` live, and should the tool download the latest image
  itself (from the Bitfocus site) or always use a local file?
