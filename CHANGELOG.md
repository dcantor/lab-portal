# Changelog

All notable changes to lab-portal (the shared run engine, the Lab Hub and the monitoring stack) are recorded here,
newest first.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). The current version is in [`VERSION`](VERSION) and
`pyproject.toml`, and in git as a `v<version>` tag.

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
