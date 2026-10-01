# NMS backup and restore

The NMS VM (10.0.0.10) runs what every lab depends on: Nautobot, Gitea (the CI mirror and runner, the configuration
backups), Prometheus, VictoriaMetrics, VictoriaLogs and Grafana. `lab-nms-backup` (labportal/nmsbackup.py) copies the
parts that cannot be rebuilt to the lab host.

## What is backed up

The lab host pulls one directory per backup, `~/backups/nms/nms-<time>/` (mode 0700), and keeps the last 14
(`LAB_NMS_KEEP`).

| File | What it holds |
|---|---|
| `nautobot.pgdump` | `pg_dump -Fc` of Nautobot's database |
| `nautobot-media.tar.gz`, `nautobot-git.tar.gz` | Nautobot's media and Git-repository volumes |
| `gitea-dump.tar.gz` | `gitea dump`: repositories, Gitea's database (`gitea-db.sql`), `app.ini` |
| `grafana.db` | Grafana's SQLite database, copied online: users, annotations, preferences |
| `nms-config.tar.gz` | `/opt/nautobot` (compose file, Dockerfile, `nautobot_config.py`, **`.env` with every service secret**) and `/opt/monitoring` |
| `manifest.json` | sizes, SHA-256 of each file, image versions, and the restore checks |

Grafana's dashboards are provisioned from `monitoring/` in this repo. The metrics and log stores are not backed up:
losing them loses history, not configuration.

## How each backup is checked

Every backup is restored before it counts as good:
- the database dump goes into a scratch database (`nautobot_restore_check`), which is counted and then dropped;
- the Gitea dump must contain its database and the repositories;
- the Grafana copy must pass SQLite's integrity check.

The hub's Shared services card shows the last backup: green, amber after 36 h, red if it failed.

## Running it

A systemd user timer runs it nightly at 02:30, catching up after a missed run. To install the timer:

```
cp nms/lab-nms-backup.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now lab-nms-backup.timer
```

Other commands:
- `lab-nms-backup` takes a backup now.
- `lab-nms-backup --list` lists the backups kept, with their checks.

## Restoring

> These are the procedures for each component. The automatic checks prove the dumps restore, but a full restore over
> the live NMS has not been exercised. Restore onto the same image versions (see `versions` in `manifest.json`).

Copy the backup to the NMS first: `scp -r ~/backups/nms/nms-<time> lab@10.0.0.10:/tmp/restore`. Delete `/tmp/restore`
when done, because it holds the `.env`.

**Nautobot's database.** Nautobot is unavailable while this runs.
```
cd /opt/nautobot && docker compose stop nautobot celery_worker celery_beat
docker exec nautobot-postgres-1 sh -c 'dropdb -U "$POSTGRES_USER" "$POSTGRES_DB" && createdb -U "$POSTGRES_USER" "$POSTGRES_DB"'
docker exec -i nautobot-postgres-1 sh -c 'pg_restore --no-owner -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < /tmp/restore/nautobot.pgdump
docker compose start nautobot celery_worker celery_beat
```

**Grafana.**
```
cd /opt/monitoring && docker compose stop grafana
sudo cp /tmp/restore/grafana.db /var/lib/docker/volumes/monitoring_grafana_data/_data/grafana.db
sudo chown 472:0 /var/lib/docker/volumes/monitoring_grafana_data/_data/grafana.db && docker compose start grafana
```

**Gitea.** This follows Gitea's documented restore from a `gitea dump`.
1. Stop Gitea, and unpack `gitea-dump.tar.gz` into a scratch directory.
2. Copy:
   - `repos/` to `/data/git/repositories/`;
   - `data/` to `/data/gitea/`;
   - `app.ini` to `/data/gitea/conf/app.ini`.

   These paths are inside the `nautobot_gitea_data` volume.
3. Load `gitea-db.sql` into a fresh database.
4. `chown -R git:git` those paths, then start Gitea.
5. Run `docker exec -u git nautobot-gitea-1 gitea admin regenerate hooks`.

**A whole new NMS.**
1. Unpack `nms-config.tar.gz` into `/opt`.
2. Run `docker compose up -d` in `/opt/nautobot` and in `/opt/monitoring`.
3. Restore the three components above.
