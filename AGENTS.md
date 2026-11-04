# dpx Agent Workflow & Documentation Standards - v1d6

This document provides operational directives for AI coding assistants (GitHub Copilot, Claude Code, Cursor, etc.) working on dubpixel projects. These rules ensure consistent workflow automation, code quality, and documentation maintenance across all repositories.

---

## PROJECT: cardbless (dpx_piPrepper)

**Status:** v0.2.0, working (bless, flash + bless, post-boot SSH cleanup). The
optional VLAN trunk prompt (v0.2.0) is rendered and YAML-validated but not yet
boot-tested from a blessed card.
**Branch:** `main` is the default; work lands on `feature/*` or `docs/*` branches
**Version File:** `VERSION` (currently 0.2.0)

### Architecture (2-minute summary)

A macOS command-line utility that prepares Raspberry Pi SD cards running the
CompanionPi image (Bitfocus Companion v5.x) so each unit boots with a known admin
user, SSH enabled, a static per-unit IPv4 address, and a unique hostname. It is
fully interactive (numbered menus, no flags, no config file) and loops with "prep
another card?". Three jobs: **flash + bless** (write the CompanionPi image, then
bless), **bless** (card already flashed; write per-unit cloud-init files to its boot
partition), and **clean up** (over SSH, remove leftover cloud-init files from a
booted unit). Blessing can optionally add one tagged 802.1Q VLAN (trunk mode).
Python 3, stdlib-first, no build step; shells out to `diskutil`, `openssl`, `dd`,
`xz`, and `rpi-imager`.

| Component | Tech/Location | Purpose | Notes |
|-----------|---------------|---------|-------|
| cardbless script | Python 3 / `src/cardbless-v1.py` | Interactive entry point: flash, bless, SSH cleanup | Single file, stdlib + PyYAML |
| cloud-init rendering | Python / `render_user_data()`, `render_network_config()` | Builds `user-data` and `network-config` per unit | Rendered per unit, not stored as static files |
| Run log | Text / `cardbless.log` | One line per blessed card (OK/FAIL, hostname, IP) | Gitignored; never commit |
| Full spec | Markdown / `doc/CLAUDE.md` | Detailed requirements, known-good file examples, gotchas | **Source of truth for the cardbless spec** |

### Agent Rules (for this repo)

**Before ANY code change:**
1. Work from `main` branch; create feature branch: `feature/brief-description`
2. Bump VERSION file per semantic versioning (AGENTS.md §1)
3. Create git commit for version bump, tag it: `git tag vX.Y.Z`

**While coding:**
- macOS (Apple Silicon) is the primary platform; Linux support is nice-to-have, not required for v1
- stdlib-first Python; PyYAML is the one acceptable extra dependency
- Shell out to `diskutil`, `openssl`, `dd`, `xz`, `rpi-imager`; never use Python's
  `crypt` module (removed in 3.13) for password hashing
- Never guess at image, rpi-imager, or macOS disk behavior; verify against a real
  card or say so explicitly
- No em dashes in output text or docs; use semicolons, colons, or commas
- Keep changes small, test before committing
- File header per AGENTS.md §3

**When done:**
- Update CHANGELOG.md with feature list
- Create PR per AGENTS.md §1 template
- For anything touching disk detection or writing to a boot partition, verify
  against an actual mounted card before calling it done

### Critical Constraints

**MUST HAVE:**
- ✅ Boot-partition writes only happen after confirming the volume looks like a
  real Pi boot partition (`cmdline.txt`, `config.txt`, `meta-data` present)
- ✅ Every rendered `user-data`/`network-config` is validated with PyYAML and then
  read back and diffed against what was intended before considering the write done
- ✅ Disk safety checks (external, physical, under max size, typed confirmation)
  before any flash operation

**DO NOT:**
- ❌ Never rely on rpi-imager's `--cloudinit-userdata` / `--cloudinit-networkconfig`
  flags; there is a known macOS bug (rpi-imager issue #1385) where those files
  never get copied. Bless ourselves afterward regardless.
- ❌ Never `open` or otherwise let anything double-click files on the boot
  partition (FAT has no permission bits; macOS/Finder will try to execute them)
- ❌ Never use username `companion` for the admin user; that is Companion's own
  service account
- ❌ Never add a `ip=` kernel parameter to `cmdline.txt`; cloud-init's
  `network-config` is the correct mechanism on this OS, not kernel networking

### Key Decisions

- **Flash backend: rpi-imager CLI + `dd` fallback, no balena Etcher/balena-cli:**
  rpi-imager is officially maintained by the Raspberry Pi Foundation and scriptable
  via `--cli`. balena Etcher's own CLI has been unmaintained since ~2021 and its
  GUI can't be scripted for batch use; balena-cli's `local flash` targets
  balenaOS/balenaCloud devices, not generic Raspberry Pi OS images like CompanionPi.
- **Subnet is per-show, not fixed:** the `/15`-style example in `doc/CLAUDE.md` is
  a real config from one show network, not a placeholder. The tool asks for IP,
  prefix length and gateway per card rather than hardcoding any of them.
- **VLAN trunk mode keeps the main network untagged:** the main network stays on
  raw `eth0` (untagged) so a card works on an access port or on a trunk whose
  native VLAN is the main one. The optional extra VLAN is a tagged `eth0.<id>`
  netplan subinterface, link-local (self-assigned 169.254.x.x, what Dante uses) or
  static, with no gateway or DNS. Never create a tagged twin of the main VLAN
  (duplicate connected routes), and the tool rejects overlapping subnets. A single
  IP cannot serve both tagged and untagged 196; the switch port must send the main
  VLAN native.
- **DNS defaults to the gateway address:** these are local/offline show networks;
  no separate upstream resolver is assumed.
- **Sudo stays passwordless (`NOPASSWD:ALL`):** matches the known-good `user-data`
  example; onsite techs swapping cards under time pressure shouldn't be blocked on
  a sudo password prompt.
- **Image source is local today:** the tool lists `.img` / `.img.xz` files found
  recursively under `~/Downloads` (or takes a typed path). A remote picker that
  lists CompanionPi builds from Bitfocus's stable release tree and defaults to the
  latest stable build is planned, not implemented.

### Gotchas & Landmines

1. **FAT executable-bit trap:** Never double-click files on the boot partition in
   Finder. FAT has no permission bits, so macOS treats every file as executable;
   Finder hands it to Terminal and runs it. A second attempt gets blocked by
   Gatekeeper with a misleading "damaged" message. The tool must never `open` these
   files.
2. **`$6$` hash quoting:** Writing a `$6$...` password hash through a shell
   expands it unless single-quoted or written via a quoted heredoc (`<< 'EOF'`).
   Write the hash from Python directly; don't shell out for this step.
3. **Kernel `ip=` is a dead end:** was tried first and is the wrong approach on
   this OS; cloud-init's `network-config` (rendered to netplan, then
   NetworkManager) is the correct mechanism. See `doc/CLAUDE.md` for the full
   networking chain.
4. **YAML paste errors are real:** an earlier manual attempt pasted heredoc
   wrapper lines (`cat > ... << 'EOF'` / `EOF`) directly into `network-config`,
   producing invalid YAML that booted with no network config. PyYAML validation
   plus read-back verification catches this class of error; don't skip either step.

### Common Operations

**Run it:** `python3 src/cardbless-v1.py` (needs `pip3 install pyyaml`). No flags;
the first menu picks the job.

**Flash + bless:** pick the image (numbered list from `~/Downloads`), pick the disk
(external, physical, size-capped, typed confirmation), flash via rpi-imager (or
`dd` + `xz` fallback), then answer the bless prompts: username, password (hashed
with `openssl passwd -6`, falling back to Homebrew `openssl@3`), hostname, IP,
prefix, gateway, and optionally a tagged VLAN. The tool validates with PyYAML,
writes, reads back, and ejects.

**Bless an already-flashed card:** same bless prompts, no flashing; the boot
partition is found and sanity-checked first.

**Clean up a booted unit (over SSH):** removes the leftover `user-data` and
`network-config` from the unit's `/boot/firmware`.

**Not implemented yet:** batch mode driven by a show config (units list), a
standalone `hash-password` command, and the remote image picker.

### Reference

See `doc/CLAUDE.md` for:
- Full spec, including verified facts about the CompanionPi image and boot process
- Known-good `user-data`/`network-config`/`cmdline.txt` examples
- The original YAML config schema and batch design (planned, not built)
- Card detection/safety rules, flash mode details, bless mode step-by-step
- Nice-to-have features deferred past v1 (post-boot verify, SSH key auth, Linux
  support)

### Development Philosophy

This is show/broadcast infrastructure tooling: reliability and being paranoid
about not wiping the wrong disk matter more than features. Prefer stdlib Python
over extra dependencies. Verify against a real card rather than assuming image or
OS behavior. Design for onsite techs working quickly under time pressure, prepping
a stack of cards back-to-back.

---
## 0. Mid-Session Issue Triage (MANDATORY)
**Default: log it, don't fix it mid-session.**

- If something broken or wanted comes up, file a GitHub issue and move on
- Only fix immediately if you explicitly say *"fix this"* or *"fix it now"*
- Start of session: `gh issue list --repo <owner>/<repo>`
- End of session: `gh issue create --repo <owner>/<repo> --title "..." --body "..."`

The rationale: prevents mid-session context-switching that breaks working code.

## 1. Automatic Workflow (MANDATORY)

These actions are **required** and must happen automatically. **NEVER ask permission** for these workflow steps.

### Branching Strategy

**BEFORE starting ANY code changes:**

1. Create a new branch from the default branch (master/main)
2. Never work directly on default branch
3. Branch naming conventions:

| Type | Format | Example |
|------|--------|---------|
| New feature | `feature/brief-description` | `feature/mqtt-decoder` |
| Bug fix | `fix/issue-description` | `fix/telegraf-timeout` |
| Documentation | `docs/what-changed` | `docs/update-architecture` |
| Refactor | `refactor/component-name` | `refactor/docker-volumes` |

### Version Bumping

**BEFORE the first code change:**

Bump the version number according to semantic versioning:

| Change Type | Version Bump | Example |
|-------------|--------------|---------|
| Bug fix, typo fix, documentation update | Patch (0.0.X) | 1.2.3 → 1.2.4 |
| New feature, new endpoint, new capability | Minor (0.X.0) | 1.2.3 → 1.3.0 |
| Breaking change, API removal, incompatible change | Major (X.0.0) | 1.2.3 → 2.0.0 |

#### Semantic Versioning Principles

**Format:** `MAJOR.MINOR.PATCH` (e.g., `2.4.7`)

- **MAJOR**: Incompatible API changes, breaking existing functionality
- **MINOR**: New functionality added in a backwards-compatible manner  
- **PATCH**: Backwards-compatible bug fixes, docs, typos

**Pre-1.0 versions (0.x.y):**
- Anything goes - breaking changes allowed in minor bumps
- Common for projects in initial development
- Move to 1.0.0 when API is stable and production-ready

**Pre-release versions:**
- Alpha: `1.0.0-alpha.1` (early testing, unstable)
- Beta: `1.0.0-beta.2` (feature-complete, testing for bugs)
- Release Candidate: `1.0.0-rc.1` (final testing before release)

#### Version Bumping Decision Tree

**When multiple changes occur, use the highest level:**
- Bug fix + new feature → Minor bump (not patch)
- New feature + breaking change → Major bump (not minor)

**Edge cases:**

| Scenario | Bump Type | Reasoning |
|----------|-----------|-----------|
| Internal refactor, no API change | Patch | No external impact |
| New optional parameter with default | Minor | Backwards-compatible addition |
| Changed parameter order | Major | Breaks existing calls |
| Deprecated feature (still works) | Minor | Deprecation warning added |
| Removed deprecated feature | Major | Functionality removed |
| Performance improvement | Patch | Implementation detail |
| New dependency added | Minor | Expands capabilities |
| Security fix | Patch | Even if behavior changes slightly |
| Database schema change | Major | Requires migration |
| Config file format change | Major | Breaking existing configs |

#### Version Bump Workflow

1. **Determine bump type** based on changes planned
2. **Update version number** in code/config files
3. **Create git commit**: `bump version to X.Y.Z`
4. **Tag the commit**: `git tag vX.Y.Z` (note the `v` prefix)
5. **Push with tags**: `git push && git push --tags`
6. **Update CHANGELOG** (if present) with version and changes
7. **Proceed with feature/fix implementation**

**Version commit should be standalone** - don't mix version bump with other changes.

#### Changelog Integration

If project has CHANGELOG.md, update it with version bump:

```markdown
## [1.2.0] - 2026-02-13

### Added
- New feature description

### Fixed
- Bug fix description

### Changed
- Breaking change description
```
**If no CHANGELOG.MD file exists:** Create one in the root of the project.

**Where to bump version:**
- Python: `__version__` in `__init__.py` or `pyproject.toml`
- Node.js: `version` field in `package.json`
- General: `VERSION` file or constant in main entry point
- Docker: Version tag in `docker-compose.yml` or `Dockerfile` labels

**If no version file exists:** Create one in an appropriate location for the project.

### Version File Standards & Location

To ensure consistent version identification across projects, follow these standards:

#### Python Projects

**Preferred location: `app/__init__.py` or `src/__init__.py`**

```python
"""Project description."""

__version__ = "1.0.0"
__author__ = "dubpixel"
```

**Alternative: `pyproject.toml` (for modern Python packaging)**

```toml
[project]
name = "project-name"
version = "1.0.0"
```

**Alternative: `VERSION` file in project root**

```
1.0.0
```

Then read it in your module:
```python
from pathlib import Path
__version__ = (Path(__file__).parent / "VERSION").read_text().strip()
```

#### Node.js/JavaScript Projects

**Location: `package.json`** (standard)

```json
{
  "name": "project-name",
  "version": "1.0.0",
  "description": "Project description"
}
```

#### Docker Projects

**Location: `docker-compose.yml` labels AND `Dockerfile`**

`docker-compose.yml`:
```yaml
services:
  app:
    build: .
    labels:
      - "org.opencontainers.image.version=1.0.0"
      - "org.opencontainers.image.created=${BUILD_DATE}"
```

`Dockerfile`:
```dockerfile
LABEL org.opencontainers.image.version="1.0.0"
LABEL org.opencontainers.image.title="Project Name"
```

#### Bash Scripts/Utilities

**Location: Top of main script or separate `VERSION` file**

```bash
#!/bin/bash
VERSION="1.0.0"
SCRIPT_NAME="manage.sh"

# Or read from VERSION file:
# VERSION=$(cat VERSION)
```

#### Version Display (REQUIRED)

**Always provide a way to display the version:**

- Python CLI: `python -m myapp --version`
- Node.js: `npm run version` or built into CLI
- Docker: `docker inspect <image> | grep version`
- Bash: `./script.sh --version`

**Example implementations:**

```python
# In your main.py or CLI entry point
import argparse
from app import __version__

parser = argparse.ArgumentParser()
parser.add_argument('--version', action='version', version=f'%(prog)s {__version__}')
```

```bash
# In bash script
if [[ "$1" == "--version" ]] || [[ "$1" == "-v" ]]; then
    echo "$SCRIPT_NAME version $VERSION"
    exit 0
fi
```

#### Multi-Component Projects

For projects with multiple components (e.g., frontend + backend + Docker):

1. **Synchronized versioning**: All components share the same version
2. **Central `VERSION` file** in project root
3. **Scripts/tools read from central file**

Example structure:
```
project-root/
├── VERSION              # 1.0.0
├── backend/
│   └── __init__.py      # Reads ../VERSION
├── frontend/
│   └── package.json     # Reads ../VERSION via build script
└── docker-compose.yml   # Reads VERSION via envsubst or build args
```

### Pull Request Creation

**AFTER completing the task:**

Create a pull request with this format:

```markdown
## Changes
- [Brief list of what changed]
- [One item per significant change]

## Testing
- [How to verify the changes work]
- [Commands to run or steps to follow]

## User Prompt
[The original request from the user - verbatim]
```

**PR Title Format:** `[Component] Brief description`

Examples:
- `[MQTT] Add BLE decoder support`
- `[Docs] Consolidate architecture documentation`
- `[Telegraf] Fix enum processor deprecation`

**NEVER ask permission to create the PR - just do it.**

### Build Artifact Naming Convention

Non-`main` builds append the branch slug to the filename so artifacts are
self-identifying without opening the run log.

| Branch | Filename |
|--------|----------|
| `main` | `<name>-vX.Y.Z.<ext>` |
| anything else | `<name>-vX.Y.Z-<branch-slug>.<ext>` |

```bash
if [ "$BRANCH" = "main" ]; then
  OUT="myapp-v${VERSION}.ext"
else
  BRANCH_SLUG=$(echo "$BRANCH" | sed 's|/|-|g' | sed 's|[^a-zA-Z0-9._-]|-|g')
  OUT="myapp-v${VERSION}-${BRANCH_SLUG}.ext"
fi
```


## 2. Progress Tracking for Multi-Step Work

When working on tasks that span **more than 3 files** OR **more than 30 minutes of work**:

### Checkpoint Progress

Provide a status update using this template:

```markdown
## Progress Checkpoint

✅ **Completed:**
- Item 1 description
- Item 2 description

⬜ **Remaining:**
- Item 3 description
- Item 4 description

→ **Next Action:** [Specific next step you will take]
```

### When to Checkpoint

- After completing a logical phase of work
- Before switching to a different component
- When encountering a blocker or decision point
- Every 3-5 file edits in large refactors

### Resuming from Checkpoint

When continuing work after a checkpoint:
1. Read the last checkpoint status
2. Start with the "Next Action" item
3. Update checkpoint when that phase completes

**Purpose:** Prevents agents from getting lost in complex multi-step tasks and provides visibility to the user.

---

## 3. File Header Standards

All code files must include a comprehensive header comment section:

```
# ================================================================================
# [FILE TYPE] - [FILE PURPOSE]
# ================================================================================
# you can maybe write some stuff here - tagline etc.
# ================================================================================
# PROJECT: [project_name]
# ================================================================================
#
# File: [filename]
# Purpose: [what this file does]
# Dependencies: [key dependencies if any]
#
# CHANGE LOG: (if needed but should really be in the changelog for the git)
# 
# 2026-03-06: Complete rewrite - Interactive wizard (v2.1.0)
#
# ================================================================================
```

### Header Guidelines

- Use consistent separator lines (80 characters of `=`)
- Adjust comment syntax for the language (`#` for Python/bash, `//` for JS/C++, etc.)


---

## 4. Documentation Standards

### Project Context Documentation

Project-specific architecture, decisions, and operational knowledge should live in the **PROJECT section at the top of this file**. This keeps rules and context unified in one scannable document.

**When to use a separate CONTEXT.md:**
Only create a separate `CONTEXT.md` if reference data becomes large enough to be noisy:
- Long IP/VLAN tables
- Full API response examples  
- Hardware pinout references
- Extensive data schemas

If you create CONTEXT.md for overflow, add a reference in the PROJECT section at the top: "See CONTEXT.md for full network topology."

### How to Document Project Context

**DO:**
- ✅ Keep it clean, factual, and scannable
- ✅ Update when architecture changes
- ✅ Add information when you learn important project details
- ✅ Use tables, code blocks, and clear headings
- ✅ Think: "What does the next agent need to know?"
- ✅ Write in present tense, authoritative voice

**DON'T:**
- ❌ Append conversation transcripts
- ❌ Include timestamps like "On Feb 12 we discussed..."
- ❌ Make it a session log or diary
- ❌ Duplicate content from README.md (link instead)
- ❌ Let it become verbose or messy

**Update frequency:** Whenever you make architectural changes or learn critical project information.

---

## 5. Core Principles


### No Modifications to Working Code

- Do not refactor, optimize, or "improve" code that is working unless explicitly requested
- Avoid drive-by refactors when implementing a feature
- If you see potential improvements, mention them but don't implement without approval

### Comprehensive Commenting

- Document all code with clear, meaningful comments
- Preserve existing comments unless they become obsolete
- Remove or update comments that are no longer accurate
- Document WHY, not just WHAT (the code shows what, comments explain why)

### Small, Incremental Changes

- Make one logical change per commit
- Break large tasks into smaller steps
- Test each change before moving to the next
- Make it easy to review and roll back if needed

### Stay Focused

- Complete the current task before suggesting next steps
- Answer only what is asked
- Don't anticipate or propose additional work unless requested

### Document Everything

- README.md must be updated and maintaned when appropriate as per these guidelines
- CONTEXT.md must be updated and maintaned when appropriate as per these guidelines
- a comprehensive CHANGELOG.md must be kept updated and maintaned when appropriate as per these guidelines
- you can maintain a small changelog in the header if you wish but main changelog should be in the MD

---

## 6. Documentation Standards

### Inline Documentation

- Maintain comprehensive inline documentation
- Update comments when code changes (keep them in sync)
- Document all function parameters and return values
- Include usage examples for complex functions
- Explain algorithms and business logic


### README Files

- Keep README.md current and accurate
- README is user-facing - focus on how to USE the project
- **Confirm all changes to README with the user before committing**
- README should not duplicate CONTEXT.md (different audiences)

### CHANGELOG.MD

- Keep CHANGELOG.md current and accurate
- CHANGELOG is user-facing - focus on changes, version numbers, dates and git hashes if needed
- **keep this automated in background**
- changelog could be retroactively updated to reflect git commit names if that adds clariy
- **any changes to existing changelog line items should be confirmed with user**

### Markdown Style

- Use consistent heading hierarchy (don't skip levels)
- Use tables for structured information
- Include code blocks with language tags
- Use relative links to other project files
- Keep line length reasonable (~80-100 chars for prose)

---

## 7. Code Quality Guidelines

### General Principles

- Write clear, readable code with meaningful names
- Follow established coding patterns within the project
- Implement proper error handling (don't use bare `except:` or `catch`)
- Write testable code with clear interfaces
- Maintain consistent formatting and style

### Language-Specific

Agents should infer and follow the conventions of the language they're working in:
- Python: Follow PEP 8
- JavaScript: Follow project's ESLint config if present
- Bash: Follow Google Shell Style Guide principles
- Other languages: Use community-standard style guides

### Testing

- Add tests alongside new logic when appropriate
- Use deterministic inputs for tests (inject time/randomness, don't read system state)
- Name tests by behavior (e.g., `test_early_finish_extends_break`)
- Include both positive and negative test cases
- if the process includes: using ssh into a remote server, or user input in any way. open a terminal first for the user so you both can read it. 

---

## 8. Change Management

### Commit Practices

- **Commit message format:** Short, plain English, lowercase verb
  - Examples: `add mqtt decoder`, `fix telegraf config`, `update documentation`
- Make one logical change per commit
- Commit functional units (don't commit broken code)

### Before Committing

- Verify the code works (run/test it)
- Update all relevant documentation
- Update file change logs
- Remove debug code and console.log/print statements
- Check that no credentials or secrets are included

### After Committing

- Push to the feature branch
- Create PR (as described in Section 1)
- Include verification steps in PR description

---

## 9. Collaboration Standards

### Respect Existing Architecture

- Understand existing architectural decisions before changing them
- Ask for clarification when requirements are ambiguous
- Suggest alternatives when appropriate, but don't insist
- Consider the impact of changes on the broader codebase

### Maintain Backwards Compatibility

- Don't break existing APIs unless explicitly requested
- Provide migration paths for breaking changes
- Document any compatibility changes in PR description

### Communication

- Explain the reasoning behind suggested changes
- Provide rollback information when making significant changes
- Be transparent about limitations or uncertainties
- Keep responses concise and focused

---

## 10. Configuration & Secrets

### Environment Variables

- Use `.env` files for local development
- Provide `.env.example` with all required variables (use placeholder values)
- **NEVER commit** `.env` files or actual credentials to git
- Document all environment variables in CONTEXT.md or README.md

### Sensitive Data

- Keep credentials in environment variables, not hardcoded
- Use service account files in standard locations (e.g., `~/.config/gcloud/`)
- Add sensitive files to `.gitignore` immediately
- If secrets are accidentally committed, notify the user immediately

---

## Summary: Agent Checklist

Before starting work:
- [ ] Create feature branch
- [ ] Bump version appropriately

While working:
- [ ] Follow file header standards
- [ ] Update change logs in modified files
- [ ] Keep changes small and focused
- [ ] Checkpoint progress if task is large
- [ ] Update PROJECT section if architecture changes

After completing work:
- [ ] Test/verify the changes
- [ ] Update relevant documentation
- [ ] Create PR with proper format
- [ ] No credentials committed

---

*These standards ensure consistent, high-quality AI assistance across all Dubpixel projects.*
