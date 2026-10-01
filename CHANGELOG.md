# Changelog

All notable changes to lab-portal (the shared run engine, the Lab Hub and the monitoring stack) are recorded here,
newest first.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). The current version is in [`VERSION`](VERSION) and
`pyproject.toml`, and in git as a `v<version>` tag.

## [0.6.0] — 2026-09-30

### Added
- **NMS backup** (`lab-nms-backup`). It pulls Nautobot's database (`pg_dump -Fc`) and volumes, a `gitea dump`, an online
  copy of Grafana's database, and `/opt/nautobot` + `/opt/monitoring` (including the `.env`) to `~/backups/nms`.
  - Backups are private (0700 / 0600), and the last 14 are kept.
  - **Every backup is restore-checked**:
    - the dump is restored into a scratch database, counted, then dropped;
    - the Gitea dump must contain its database and repositories;
    - Grafana's copy must pass SQLite's integrity check.
  - Each backup has a manifest with checksums and image versions.
  - Nothing is left on the NMS, even when a step fails.
  - A nightly systemd user timer runs it (`nms/lab-nms-backup.{service,timer}`, 02:30, catching up after a missed
    run).
  - The restore procedures are in `nms/README.md`.
- **Lab Hub:** the Shared services card shows the last NMS backup, its age (amber after 36 h, red on failure) and what
  its restore check found.
- **One credential store** (`labportal/secrets.py`, `lab-secrets`): `~/.config/lab/secrets.env`, mode 0600.
  - Tools read the environment first, then the store; values are never printed.
  - `lab-secrets import-nms` copies the Gitea and Nautobot credentials from the NMS.

### Changed
- The Lab Hub's CI view reads the Gitea credentials from the store, and reads the NMS's `.env` over SSH only when they
  are not stored.

## [0.5.0] — 2026-09-30

### Added
- **Lab Hub: sign-in.** Every page and API now requires one: the hub can power labs, the NMS and CI, and it listens on
  the LAN.
  - Users are kept in `~/.config/lab-hub/auth.json` (mode 0600) as salted PBKDF2-SHA256 hashes. The file is created on
    the first start with **admin / admin**.
  - `lab-hub-passwd [user]` changes a password or adds a user.
  - Sessions are signed cookies, HttpOnly and SameSite=Strict, for 12 hours (`LAB_HUB_SESSION_HOURS`). Removing a user
    ends their sessions at once.
  - Without a session, pages redirect to `/login` and API calls get 401.
  - `next` only accepts a path on the hub, so the sign-in cannot redirect elsewhere.
  - Five failed attempts in five minutes lock out that address.
  - The header shows who is signed in, with a sign-out link; an expired session goes back to the sign-in page.
  - `GET /api/me` says who is signed in. `LAB_HUB_AUTH=off` turns sign-in off.

## [0.4.0] — 2026-09-30

### Added
- **Lab Hub: resource check before power-up.** Starting VMs is checked against the host's available memory (2 GiB
  reserve, `LAB_HUB_RESERVE_GIB`). The sizes come from libvirt.
  - When it does not fit, the hub says how much is missing and lists the largest VMs, and asks before going ahead
    (`override_memory`).
  - A lab whose VMs cannot be read counts as "memory unknown", not 0 GiB.
  - Each card shows the lab's total memory.
- **Lab Hub: dependencies.**
  - Every lab depends on the NMS (soft).
  - A lab depends on another lab whose VMs it attaches (hard, read from `EXT_LAB` in its `lab.conf`).
  - A power-up offers to start the dependencies first, in order (`start_deps`), and counts their memory too.
  - A shutdown names the running labs that need the lab (`override_dependents`).
  - Shutting down the NMS names the running labs that use it, and is refused while a portal run is in progress (`force`).
  - API: `GET /api/labs/{name}/plan`, `GET /api/services/plan`.
- **Lab Hub: schedules and idle shutdown.**
  - Start and stop times per lab on chosen days, and shutdown after N idle hours.
  - An "I'm using it" button restarts the idle clock.
  - Every scheduled action goes through the same checks, and its result or the reason it was skipped is kept on the card.
  - API: `GET/PUT/DELETE /api/labs/{name}/schedule`, `POST /api/labs/{name}/keepawake`.

### Changed
- A lab shutdown is also refused while a CI run is testing that lab (`force` overrides it, as for a portal run).
- Power operations from buttons and from the scheduler go through one function, so they apply the same checks.

## [0.3.0] — 2026-09-30

### Added
- **Lab Hub: shared services.** A card for the NMS VM and each service on it: Prometheus, VictoriaMetrics, VictoriaLogs,
  Grafana, Gitea and Nautobot.
  - Each service shows whether it answers, what it is used for, and a link.
  - A plain warning appears when the NMS is off.
  - **Start the NMS** waits until every service answers; **Shut down the NMS** is a clean ACPI shutdown.
  - API: `GET /api/services`, `POST /api/services/power`. Configured in `~/.config/lab-hub/services.json`, or the
    defaults in `labportal/hub/shared.py`.
- **Lab Hub: CI per lab**, for labs with a `ci` entry in `labs.json`:
  - the latest run on the local Gitea, with each job's result and the test counts CI committed back;
  - a strip of the last runs, the commits not tested yet, and the mirror's last sync.
  - **Run CI** syncs the mirror or dispatches the workflow.
  - **Stop CI** stops the job carefully: the step's shells are killed so nothing is committed, then Robot is
    interrupted once so its teardowns run. Gitea's own cancel skips the teardowns.
  - API: `GET /api/labs/{name}/ci`, `POST /api/labs/{name}/ci/run`, `POST /api/labs/{name}/ci/stop`.
- `labs.example.json`: srv6-core's `ci` entry.

### Fixed
- The hub page no longer polls twice: one schedule refreshes every 30 s, and every 4 s while a lab, the NMS or a CI run
  is changing.

## [0.2.0] — 2026-09-30

### Added
- **Lab Hub: light / dark mode.** A ☾ dark / ☀ light toggle in the header.
  - The hub follows the OS setting, live, until a choice is made; the choice is then remembered in the browser.
  - The theme is applied before the first paint, so there is no light flash.
  - Every colour is now a palette variable, the same palette the lab portals use. The cards, pills, VM chips, power
    buttons, host meters and operation log all switch.

## [0.1.0]

The state before this changelog: the run engine (`RunBase`, `RunRegistry`, `install_runs_api`), Grafana annotations,
the metrics helpers, the Lab Hub (labs, VMs, power control, host resources, last tests), the MCP server and the
monitoring stack (Prometheus, VictoriaMetrics, VictoriaLogs, Grafana, alert rules).
