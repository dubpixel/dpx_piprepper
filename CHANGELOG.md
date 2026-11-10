# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Project scaffolding and documentation established: `AGENTS.md` PROJECT section
  and `README.md` filled in for cardbless (the CompanionPi SD card flasher +
  blesser); see `doc/CLAUDE.md` for the full spec.

### Changed
- 

### Deprecated
- 

### Removed
- 

### Fixed
- 

### Security
- 

## [0.3.0] - 2026-11-09

### Added
- Local web GUI (`src/cardbless-gui.py`, page in `src/gui/index.html`): flash + bless,
  bless only, and SSH cleanup from the browser, with a drop zone for the disk
  image (matched by name and size to files under ~/Downloads, uploaded to
  ~/Downloads/cardbless-drops if not found). Run `python3 src/cardbless-gui.py`.
  Flashing elevates through the macOS admin password dialog (no terminal sudo).
  Up to 3 extra tagged VLANs. Binds 127.0.0.1 only, per-run token on every POST, server-side re-check of
  disks, boot volumes and images, typed disk-id confirmation before any erase.
- `tests/test_gui.py`: validation, boot-volume detection, bless into a temp
  directory, and server auth rules (no real disk is touched).

### Fixed
- GUI flash: the root process behind the admin dialog could not read images in
  ~/Downloads (macOS privacy protection); the image is now copied to a staging
  folder first and removed afterward.
- GUI flash uses xz+dd behind the admin dialog instead of rpi-imager's CLI, which
  sat idle and wrote nothing there (cause not verified), and shows live percent,
  GB and MB/s while writing.

### Notes
- The CLI (`src/cardbless-v1.py`) is imported unchanged.
- Not yet verified against a real card: flashing and blessing from the GUI.
- After a GUI flash, every boot marker is checked before writing (the CLI only
  checks `cmdline.txt`; see issue #2).

## [0.2.2] - 2026-11-04

### Added
- Single-page manual at `index.html` (walkthrough of the real prompts, safety
  checks, VLAN trunk mode, files written, gotchas, limits), published with GitHub
  Pages. It takes the place of the Jekyll render of the README as the site's home.
- GitHub Pages deploy workflow (`.github/workflows/pages.yml`), runs on push to `main`.

### Changed
- `README.md` documents the `sshpass` prerequisite for the SSH cleanup mode and why
  cleanup exists (the password hash would otherwise stay on the world-readable FAT
  boot partition).

## [0.2.1] - 2026-11-04

### Added
- `LICENSE.txt` (MIT) ahead of making the repo public.
- Self-hosted GitHub Pages theme from the dpx template (`_layouts/`, `_sass/`,
  `_includes/`, `assets/`).

### Changed
- `_config.yml` migrated off the `pages-themes/midnight` remote theme (removes the
  third-party attribution banner); title and description set for this project.
- `README.md` moved to the v0.5.8 software footer-logo template and rewritten to
  describe the working interactive tool (it still said "spec complete,
  implementation not started" with a planned flag-based CLI).
- `AGENTS.md` PROJECT section refreshed to match the shipped tool: status,
  architecture, VLAN decision, real run instructions, and what is still unbuilt.

### Fixed
- Stale comment in `src/cardbless-v1.py` that still described the image picker as
  an arrow-key browser.

## [0.2.0] - 2026-11-04

### Added
- Optional tagged VLAN (trunk mode) when blessing a card. The main network stays
  untagged on `eth0`; an extra 802.1Q VLAN (default 218) is added as `eth0.<id>`,
  either link-local (self-assigned 169.254.x.x, as Dante uses) or a static IP.
  Overlapping subnets are refused. Declining the prompt leaves `network-config`
  byte-identical to before.

## [0.1.0] - 2026-09-22

### Added
- Interactive `cardbless` CLI (`src/cardbless-v1.py`): flash + bless or bless only,
  no flags, plain numbered menus, loops to prep another card.
- Image picker: numbered list of `.img` / `.img.xz` found recursively under
  `~/Downloads`, or a typed path.
- Disk picker limited to external, physical, size-capped disks with a typed
  confirmation before erasing.
- Flashing via the rpi-imager CLI with a `dd` + `xz` fallback.
- Cloud-init rendering for `user-data` and `network-config`, validated with
  PyYAML and read back after writing; password hashed with `openssl passwd -6`.
- Post-boot cleanup mode over SSH that removes leftover cloud-init files from a
  booted unit.
- `cardbless.log` run log (gitignored).

### Fixed
- Suppressed the harmless `dot_clean` stderr noise on FAT boot partitions.

---

## [0.1.0] - YYYY-MM-DD

### Added
- Initial release
- Core functionality implementation

---

## Version Guidelines

### Semantic Versioning (MAJOR.MINOR.PATCH)

- **MAJOR**: Breaking changes, incompatible API modifications
- **MINOR**: New features, backwards-compatible additions
- **PATCH**: Bug fixes, documentation updates, typos

### Change Categories

- **Added**: New features or capabilities
- **Changed**: Changes to existing functionality
- **Deprecated**: Features marked for future removal (still working)
- **Removed**: Removed features or functionality
- **Fixed**: Bug fixes
- **Security**: Security patches or vulnerability fixes

### Example Entry Format

```markdown
## [1.2.0] - 2026-03-15

### Added
- New authentication system with JWT tokens
- Export functionality for CSV and JSON formats
- Dark mode toggle in user preferences

### Changed
- Improved database query performance by 40%
- Updated UI library from v2.1 to v3.0

### Fixed
- Fixed memory leak in background worker process
- Corrected timezone handling in date picker component

### Security
- Patched XSS vulnerability in user input validation
```

### Version Comparison Links

Add these at the bottom of the file (replace with your repo owner/name):

```markdown
[Unreleased]: https://github.com/owner/repo/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/owner/repo/releases/tag/v0.1.0
```

---

## Tips for Maintaining This Changelog

1. **Update as you work**: Add entries when making changes, not at release time
2. **Keep it scannable**: Use clear, concise descriptions
3. **Link to issues/PRs**: Include `(#123)` references when relevant
4. **Date format**: Use ISO 8601 (YYYY-MM-DD)
5. **Group by type**: Keep all Added items together, all Fixed items together, etc.
6. **User perspective**: Write what changed for users, not implementation details
7. **Unreleased section**: Keep active changes here, move to version section on release
