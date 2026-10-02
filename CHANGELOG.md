# Changelog

All notable changes to lab-portal (the shared run engine, the Lab Hub and the monitoring stack) are recorded here,
newest first.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). The current version is in [`VERSION`](VERSION) and
`pyproject.toml`, and in git as a `v<version>` tag.

## [0.15.1] — 2026-10-01

### Changed
- evpn-fabric's management network moved from 10.6.0.0/24 to 10.106.0.0/24 (the old range clashed with another
  network): Prometheus discovers the fabric at `http://10.106.0.1:8095/api/sd`; the NMS's eth6 is 10.106.0.10.

## [0.15.0] — 2026-10-01

### Added
- **Monitoring for evpn-fabric** (the 5th lab, EVPN/VXLAN leaf-spine on VyOS).
  - Prometheus job `evpn-fabric`, discovered from the lab portal's `/api/sd` on 10.6.0.1:8095 — 27 targets: 15
    node-exporters, 11 frr-exporters, the portal.
  - Alert group `evpn-fabric`: `EvpnFabricSessionDown`, `EvpnVtepMissingOnVni`, `EvpnSegmentDfNotUnique`,
    `EvpnDefaultRouteDegraded`, `EvpnEdgeSessionDown`, `EvpnServerBondDegraded`, `EvpnNodeUnhealthy` — all gated on
    the lab running.
  - Log rules `evpn-fabric-events` (BGP Down, BFD changes, FRR restarts, commits, the leaves' protodown watcher),
    labelled `lab: evpn-fabric`.
  - Dashboard **EVPN fabric: overview** (`evpn-fabric-overview`).

### Changed
- `deploy.sh` also reloads vmalert-logs: it does not watch its rule file, so new log rules were not picked up.

### Fixed
- The generic log rules (`routing-events`, `operations-events`) would have raised evpn-fabric's events labelled
  `lab=srv6-core` (the evaluator's external label); they now exclude its hostnames.
- The IPsec lab's firewall log rules matched `hostname:fw-*`, which also catches evpn-fabric's `fw-ext`; they now match
  `fw-(east|central|west)`.

## [0.14.0] — 2026-09-30

### Added
- **Lab Hub: a brand and a banner.**
  - The top bar has a hexagonal hub logo mark and a gradient "Lab **Hub**" wordmark.
  - Above the meters, a "network console" banner (dark in both themes, with a faint grid and glow) has:
    - the title "Lab Hub" in a gradient, a tagline, and a pulsing "network lab control plane" eyebrow;
    - live chips: labs running, VMs up, the NMS's services, failing count, memory in use.
- **Lab Hub: a live hub-and-spoke map** in the banner, drawn from the current state.
  - The hub sits at the centre, the NMS above it, and each lab on an arc below, with its VM count.
  - Each lab node is coloured like its health dot; a running lab glows and pulses.
  - Links to running labs (and to a running NMS) carry animated packets; links to labs that are off are dim and
    dashed.
  - Circuit traces sit in the background.
  - Hovering a lab shows its health reasons. Animation stops under prefers-reduced-motion, and on a phone the banner
    stacks.

## [0.13.0] — 2026-09-30

### Changed
- **Lab Hub: VM states from one libvirt query.**
  - Each lab's nodes (names, roles, libvirt domains) change only when a lab is rebuilt, so they are read from
    `lab.sh status` once every 10 minutes, and again after a power operation.
  - The live state of every VM comes from a single `virsh list --all` (about 40 ms), shared by the cards, the power
    plans and the scheduler.
  - Measured: `/api/labs` 4.06 s → 0.5 s; the plan behind a power dialog 4.72 s → 0.016 s.
- **Lab Hub: a lab whose `lab.sh status` lists nothing** is read from the `ROLE` table in its `lab.conf` instead, so
  vyos-dmvpn's 18 VMs (12 GiB) show again. The card says where the list came from (`vm_source`).

### Added
- **Lab Hub: skeletons.** The page shows grey placeholder shapes of the meters, the services line and the lab cards
  until the first data arrives, instead of an empty grid. They shimmer gently, or stay still under
  prefers-reduced-motion.

## [0.12.0] — 2026-09-30

### Changed
- **Lab Hub: equal-height cards.** The cards in a row line up, and each card's links sit at its bottom.
- **Lab Hub: one button language.** Solid primary for constructive actions (⏻ up, Run CI, Start, Save); a red outline
  for risky ones (⏻ down, Stop CI, Remove); quiet for the rest. The same applies in every dialog.
- **Lab Hub: a type scale.** Five sizes (11 / 12 / 13 / 15 / 20 px), and tabular figures, so numbers do not jitter on
  refresh.
- **Lab Hub: more breathing room.** Bigger gaps between cards and sections, more padding, and softer dividers.
- **Lab Hub: long text truncates.** Commit names, changelog items and scheduler events end in an ellipsis, with the
  full text on hover, instead of wrapping.
- **Lab Hub: a two-zone toolbar.**
  - Finding (search, show, sort) on the left; the view (cards / compact, fold, count) on the right.
  - Below 900 px the finding controls fold behind a **Filters** button, which shows how many are active.
  - The sticky toolbar re-measures the header whenever it changes height.
- **Lab Hub: matching meters.** CPU, memory and disk bars have the same height and shape, and read inline: "12% of
  12 cores", "11 of 57.7 GiB · 19%".

### Added
- **Lab Hub: the browser tab shows the state.**
  - The favicon is green when labs are running and all is well, amber when something needs a look, red with "!" when
    something is failing, and grey when everything is off.
  - The tab title reads e.g. "⚠ (1) Lab hub". It counts the same red health dots, plus the shared services and the
    NMS backup.

## [0.11.0] — 2026-09-30

### Changed
- **Lab Hub: an off lab no longer looks broken.**
  - A lab with every VM shut down shows a grey "off · N VMs" pill, not red; partly up is amber, fully up is green.
  - Shut-off VM chips are neutral grey. Only crashed or undefined VMs are red, and paused or shutting-down ones amber.
- **Lab Hub: one colour language.**
  - Colour means state: green good, amber needs attention, red failing, grey off or neutral.
  - Informational pills (uncommitted files, commits to pull, no VERSION / CHANGELOG, "busy") are neutral.
- **Lab Hub: shared services on one line when all is well.** It reads "NMS running · 6/6 services up · backup ok 1 h
  ago", with a details toggle (remembered).
  - It opens to the full card by itself when the NMS or a service is down, the backup is old or failed, or a power
    operation is running.
- **Lab Hub: two-tier lab cards.**
  - **Header strip:** health dot, name (the directory on hover), VM / portal / problem pills, and ⏻ up / ⏻ down on the
    right.
  - **Key facts:** Tests, CI, Version, Memory (in use, or what it takes to run) and Next (scheduled action).
  - **Details**, in a lighter, smaller style: portal, version, CI, schedule, needs, VMs, links.
  - A folded card keeps the header strip and the facts.
- **Lab Hub: short test summaries.** "68 passed · 0 failed · 23 Sep", with failed suites named and the full suite list
  on hover, in place of every suite name wrapped over several lines.

## [0.10.0] — 2026-09-30

### Added
- **Lab Hub: health dot per lab**, on cards and compact rows, with the reasons on hover:
  - green: running, tests passing, nothing failing;
  - amber: partly up, a power operation running, tests over a week old, or a skipped schedule;
  - red: failed tests or CI, portal down, a failed power operation;
  - grey: off.
- **Lab Hub: one dialog per power action**, replacing the chains of `confirm()` pop-ups.
  - Starting a lab shows its memory need as a bar against the host's use and reserve, offers to start its dependencies
    first, and recomputes as that choice changes.
  - Shutting down lists the running labs that need it.
  - Risky choices need a ticked box before the button enables: memory override, dependents, or a portal or CI run in
    progress.
  - The NMS dialog lists the labs that lose it. CI run / stop and removing a schedule use the same dialog.
- **Lab Hub: toasts.** Results and errors appear in a corner (errors stay longer and use `role="alert"`). No
  `alert()` is left.
- **Lab Hub: large host chart.** Clicking a sparkline opens the last 24 hours:
  - CPU, memory and disk, each switchable;
  - a hover readout of the values at any moment;
  - power markers and a list of power operations, either of which jumps to that moment.
- **Lab Hub: memory by lab.** The memory meter is a stacked bar: the resident memory of each running lab's VMs (summed
  from their QEMU processes, not their maximum), the NMS, and the host itself. The API adds `memory_used_gib` per lab
  and for the NMS.

## [0.9.0] — 2026-09-30

### Added
- **Lab Hub: sticky header and toolbar.** The sign-out, theme, view, filter and sort controls stay in reach while you
  scroll. The toolbar follows the header's height as it wraps.
- **Lab Hub: sort and filter.**
  - A search box matches lab names, descriptions and VM names.
  - Show All / Running / Stopped / **Problems** (failed tests or CI, portal down, a failed power operation, a skipped
    schedule); each card shows a ⚠ count of its problems.
  - Sort by your order, name, running first, problems first, or memory.
  - "Showing N of M" with a one-click reset.
- **Lab Hub: drag to reorder.** A ⠿ handle on each card and compact-table row; dropping saves the order (and switches
  to "your order"). A refresh never redraws under a drag.
- **Lab Hub: phone layout.**
  - Below 640 px: one column, label-above-value rows, and bigger tap targets for buttons and VM chips.
  - The host meters become a compact three-up strip, and only the header stays pinned.
  - No sideways scrolling at 375 px.

## [0.8.0] — 2026-09-30

### Added
- **Lab Hub: collapsible cards and a compact view.** A toolbar above the labs offers **Cards / Compact**, **Fold all**
  and **Unfold all**.
  - A folded card keeps its title and status pills, plus a one-line summary: tests, CI, version, memory, the next
    scheduled action, uncommitted files, and ⏻ up / ⏻ down.
  - The compact view is one table row per lab, with VMs, portal, tests, CI, version, memory, schedule and the power
    buttons. A lab's name opens its card.
  - The view and the folded cards are remembered per browser. Power buttons in both go through the same guarded checks.

## [0.7.0] — 2026-09-30

### Added
- **Lab Hub: host history.** A 24-hour sparkline under the CPU, memory and disk meters, with the day's peak and the 75% /
  90% marks.
  - Every power operation on a lab or the NMS (button, schedule or dependency start) is marked on the graphs: green
    for a start, red for a shutdown, with details on hover.
  - The hub samples once a minute itself, so the graphs work with the NMS off. Samples are saved to
    `~/.local/state/lab-hub/host-history.json` and survive restarts.
  - On start it backfills the missing hours from VictoriaMetrics (`job="lab-host"`).
  - API: `GET /api/host/history`.
- **Lab Hub: lab versions.** A Version row on each card:
  - the `VERSION` (linked to its tag), with the date and headline items of the newest `CHANGELOG.md` entry, and the
    full entry on hover;
  - "not tagged", or "no VERSION / CHANGELOG" when missing;
  - the branch and commit, commits since the last tag, uncommitted files, and how far it is ahead of or behind
    GitHub as of the last fetch (the hub never fetches by itself).

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
