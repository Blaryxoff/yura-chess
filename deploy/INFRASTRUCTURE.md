# Infrastructure

Topology, configuration sources, secrets, ports and diagnostics for the Firebat
production environment. The step-by-step runbook is in [README.md](README.md).

## Topology

```
                    internet
                       │  443/tcp
                ┌──────▼──────────────────────┐
                │ Firebat host                │
                │  nginx  (TLS, SNI, limits)  │
                └──────────┬───────────┘
                    127.0.0.1:8082
                           │
                  Incus proxy-device
                           │
              ┌────────────▼─────────┐
              │ container: yura-chess│
              │  app  (compose)      │
              │  mariadb 11.4        │
              │  internal network    │
              └──────────────────────┘
```

Host nginx owns TLS for canonical `yurachess.ru` and redirect-only `chess.waxim.ru`, and is the only public listener.
Neither MariaDB is published beyond its container network.

## Reaching production

Deploys, rollbacks, backups and container diagnostics all run **inside** the
container, not on the Firebat host and not from a workstation checkout:

```bash
ssh firebat
sudo -n incus exec yura-chess -- bash -lc '<command>'
```

Only nginx and Incus itself belong to the host; the exceptions are marked where
they appear.

`/srv/yura-chess/repo` is an extracted snapshot of the repository, not a git
checkout: `git fetch` and `git checkout` fail there. A release changes the image
tag only, so the snapshot does not have to track `master` — but `deploy.sh`,
`rollback.sh` and the Compose file are read from it, and a change to any of those
reaches production only when the snapshot is replaced.

## Environment

| Item | Production |
| --- | --- |
| Incus container | `yura-chess` (dedicated), on host `firebat` |
| Scripts and Compose file | `/srv/yura-chess/repo/deploy/` inside the container |
| Compose file | `deploy/compose.production.yml` (repository) |
| Compose project | `yura-chess-production` |
| Database | MariaDB 11.4, volume `mariadb-data` |
| App port | container `8000` → loopback `127.0.0.1:8082` |
| Public name | `https://yurachess.ru` |
| `YURA_CHESS_ENVIRONMENT` | `production` |

## Incus proxy-devices

The application never listens on a public host interface. Its dedicated
container forwards the app port to the host loopback:

```bash
# On the Firebat host, not inside the container
sudo -n incus config device add yura-chess app-proxy proxy \
  listen=tcp:127.0.0.1:8082 connect=tcp:127.0.0.1:8082
```

## Configuration sources

| Source | Content | Location |
| --- | --- | --- |
| `.env.example` | names of every variable, no real values | repository |
| `/srv/yura-chess/production.env` | application settings and secrets | Firebat, `0600`, root |
| `/srv/yura-chess/production-db.env` | `MARIADB_*` for the production database | Firebat, `0600`, root |
| `/srv/yura-chess/backup.env` | backup and restore credentials, S3 target | Firebat, `0600`, root |
| `/srv/yura-chess/*.current-image`, `*.previous-image` | tag recorded by `deploy.sh` | Firebat |

Secrets that exist only on Firebat and never in git:

- `YURA_CHESS_IDENTITY_SALT` — losing it makes every stored owner key unresolvable,
  so it is backed up separately from the database and never rotated casually.
- `YURA_CHESS_DATABASE_URL` — includes the database password.
- `YURA_CHESS_YANDEX_OAUTH_TOKEN` — Dialogs image upload; without it the skill stays voice-only.
- `MARIADB_PASSWORD`, `MARIADB_ROOT_PASSWORD`, `YURA_CHESS_BACKUP_*` credentials.

## Ports

| Port | Scope | Purpose |
| --- | --- | --- |
| 443/tcp | public | nginx TLS for `yurachess.ru` and the retired-host redirect |
| 80/tcp | public | ACME challenge and redirect to 443 |
| 127.0.0.1:8082 | host loopback | production application via proxy-device |
| 3306/tcp | container network only | MariaDB; never published |

## Runtime guarantees

- Application containers run as uid 10001, `read_only: true`, `cap_drop: ALL`,
  `no-new-privileges`, with only a small `tmpfs` on `/tmp`. Board images are
  rendered in memory and never written to disk.
- Dialogs board images use a bounded TTL/LRU cache. Maintenance deletes the
  remote Yandex resource before forgetting its MariaDB mapping, retries failed
  deletions on the next pass, and blocks new uploads above the configured quota
  threshold or hard cache ceiling. An evicted position is regenerated on demand.
- CPU and memory limits and `restart: unless-stopped` are set per service.
- Logs use the `json-file` driver capped at 10 MB × 5 files per service.
- Aggregate usage survives log rotation in `usage_users` and `usage_requests`.
  These tables contain only HMAC/hashed keys, timestamps and a real/test label;
  request rows also keep the immutable release id and bounded routing categories
  (`command_kind`, resolver status and outcome). Retained normalized transcripts
  link to them only through the hashed request key and keep their shorter retention
  window; raw payloads and command text never enter permanent analytics.
- Synthetic game state is retained for `YURA_CHESS_TEST_GAME_RETENTION_DAYS`
  (seven days by default) and then deleted with its game-scoped children. Durable
  aggregate `usage_users` and `usage_requests` rows remain available for release diagnostics.
- MariaDB and persisted timestamps stay in UTC. Public usage reports shift UTC
  timestamps to Moscow time before applying day, month and period boundaries.
- Health checks: the container healthcheck polls `/health/live` (liveness only —
  a restart cannot repair a degraded pool or a down database), while
  `/health/ready` (database connection, schema and ready worker count) is polled
  by `deploy.sh` and `rollback.sh` and gates the release; MariaDB uses
  `healthcheck.sh --connect --innodb_initialized`. The engine pool count is
  reported by readiness without ever starting a search.

## Deploy and rollback

`/srv/yura-chess/repo/deploy/deploy.sh production <40-character-git-sha>` — validate,
pull, migrate as a separate release step, start, health smoke, auto-revert on failure.
`/srv/yura-chess/repo/deploy/rollback.sh production [tag]` — restore the previous
application image.
Details and the cutover checklist: [README.md](README.md).

## Backup and restore

- `deploy/mariadb/backup.sh` — daily `mariadb-dump --single-transaction`, gzip,
  copy to the S3-compatible target, prune by `YURA_CHESS_BACKUP_RETENTION_DAYS`,
  free-space floor, alert on any failure including a missing off-host copy.
- Backup commands use the explicit production Compose file from
  `YURA_CHESS_COMPOSE_FILE`, so timers do not depend on their working directory.
- `deploy/mariadb/restore-smoke.sh` — restore the latest archive into
  `yura_chess_restore_smoke`, assert every canonical table and the Alembic
  revision, then drop it. Run on its schedule or manually as an independent
  operations check; backup and restore status never blocks an application deploy.
- `deploy/systemd/yura-chess-backup.timer` runs daily and
  `yura-chess-restore-smoke.timer` verifies the latest archive weekly. Install
  both during provisioning, but enable them only after the off-host target and
  credentials are present.

Full restore into the live database (announced outage):

```bash
docker compose --project-name yura-chess-production stop app
gunzip -c /srv/yura-chess/backups/yura_chess-<stamp>.sql.gz \
  | docker compose --project-name yura-chess-production exec -T mariadb \
      mariadb --user=root --password yura_chess
docker compose --project-name yura-chess-production start app
```

## Diagnostics

All of these run inside the container except the nginx and Incus commands, which
belong to the Firebat host:

```bash
# Is the public endpoint alive end to end?
curl -i -X POST https://yurachess.ru/webhooks/alice -H 'Content-Type: application/json' -d '{}'

# Landing page with aggregate usage statistics (real traffic by default)
curl -I https://yurachess.ru/

# Application readiness from the host (never exposed publicly)
curl -s http://127.0.0.1:8082/health/ready | jq

# Container state and logs
docker compose --project-name yura-chess-production ps
docker compose --project-name yura-chess-production logs --tail=200 app

# Which tag is deployed
cat /srv/yura-chess/production.current-image

# Database reachability from inside the stack
docker compose --project-name yura-chess-production exec mariadb \
  healthcheck.sh --connect --innodb_initialized

# On the Firebat host, not inside the container: nginx and Incus
sudo nginx -t && sudo systemctl reload nginx
sudo tail -f /var/log/nginx/yurachess.ru.error.log
sudo -n incus list yura-chess
sudo -n incus config device show yura-chess
```

`/health/ready` returns 503 while the database or schema check fails **or** while
no Stockfish worker is ready (`engine: degraded: 0/2 workers`): an instance that
cannot search has to leave rotation instead of accepting traffic. `deploy.sh` and
`rollback.sh` poll this endpoint, so a degraded engine fails the release smoke and
triggers the automatic rollback.
