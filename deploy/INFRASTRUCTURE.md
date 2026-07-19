# Infrastructure

Topology, configuration sources, secrets, ports and diagnostics for the two
Firebat environments. The step-by-step runbook is in [README.md](README.md).

## Topology

```
                    internet
                       │  443/tcp
                ┌──────▼──────────────────────┐
                │ Firebat host                │
                │  nginx  (TLS, SNI, limits)  │
                └──┬──────────────────┬───────┘
       127.0.0.1:8082         127.0.0.1:8081
                   │                  │
      Incus proxy-device   Incus proxy-device
                   │                  │
   ┌───────────────▼──────┐  ┌────────▼─────────────────┐
   │ container: yura-chess│  │ container: staging       │
   │  app  (compose)      │  │  app  (compose)          │
   │  mariadb 11.4        │  │  staging-mariadb (shared)│
   │  internal network    │  │  own db + own user       │
   └──────────────────────┘  └──────────────────────────┘
```

Host nginx owns TLS for `chess.waxim.ru` and is the only public listener.
Neither MariaDB is published beyond its container network.

## Environments

| | staging | production |
| --- | --- | --- |
| Incus container | `staging` (shared) | `yura-chess` (dedicated) |
| Compose file | `deploy/compose.staging.yml` | `deploy/compose.production.yml` |
| Compose project | `yura-chess-staging` | `yura-chess-production` |
| Database | existing `staging-mariadb`, own db `yura_chess_staging` and own user `yura-chess` | own MariaDB 11.4, volume `mariadb-data` |
| App port (container loopback) | 8000 → published `127.0.0.1:8081` | 8000 → published `127.0.0.1:8082` |
| Host port via proxy-device | `127.0.0.1:8081` | `127.0.0.1:8082` |
| Public name | none (host-local only) | `https://chess.waxim.ru` |
| `YURA_CHESS_ENVIRONMENT` | `test` | `production` |

Staging shares a MariaDB server with other projects; it therefore gets its own
database and its own user with rights on that database only. Production runs its
own server so a staging incident cannot reach real games.

The shared staging database and application join the existing external Docker
network `lemp-shared`. Override `STAGING_DB_NETWORK` only if the Firebat network
is deliberately renamed.

## Incus proxy-devices

The application never listens on a host interface. Each container forwards its
loopback port to the host loopback:

```bash
incus config device add yura-chess app-proxy proxy \
  listen=tcp:127.0.0.1:8082 connect=tcp:127.0.0.1:8082
incus config device add staging yura-chess-proxy proxy \
  listen=tcp:127.0.0.1:8081 connect=tcp:127.0.0.1:8081
```

## Configuration sources

| Source | Content | Location |
| --- | --- | --- |
| `.env.example` | names of every variable, no real values | repository |
| `/srv/yura-chess/production.env` | application settings and secrets | Firebat, `0600`, root |
| `/srv/yura-chess/production-db.env` | `MARIADB_*` for the production database | Firebat, `0600`, root |
| `/srv/yura-chess/staging.env` | staging application settings | Firebat, `0600`, root |
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
| 443/tcp | public | nginx TLS for `chess.waxim.ru` |
| 80/tcp | public | ACME challenge and redirect to 443 |
| 127.0.0.1:8082 | host loopback | production application via proxy-device |
| 127.0.0.1:8081 | host loopback | staging application via proxy-device |
| 3306/tcp | container network only | MariaDB; never published |

## Runtime guarantees

- Application containers run as uid 10001, `read_only: true`, `cap_drop: ALL`,
  `no-new-privileges`, with only a small `tmpfs` on `/tmp`. Board images are
  rendered in memory and never written to disk.
- CPU and memory limits and `restart: unless-stopped` are set per service.
- Logs use the `json-file` driver capped at 10 MB × 5 files per service.
- Health checks: the application polls `/health/ready` (database connection,
  schema and ready worker count); MariaDB uses `healthcheck.sh --connect
  --innodb_initialized`. The engine pool count is reported by readiness without
  ever starting a search.

## Deploy and rollback

`deploy/deploy.sh <env> <tag>` — validate, pull, migrate as a separate release
step, start, health smoke, auto-revert on failure.
`deploy/rollback.sh <env> [tag]` — restore the previous application image.
Details and the cutover checklist: [README.md](README.md).

## Backup and restore

- `deploy/mariadb/backup.sh` — daily `mariadb-dump --single-transaction`, gzip,
  copy to the S3-compatible target, prune by `YURA_CHESS_BACKUP_RETENTION_DAYS`,
  free-space floor, alert on any failure including a missing off-host copy.
- Backup commands use the explicit production Compose file from
  `YURA_CHESS_COMPOSE_FILE`, so timers do not depend on their working directory.
- `deploy/mariadb/restore-smoke.sh` — restore the latest archive into
  `yura_chess_restore_smoke`, assert every canonical table and the Alembic
  revision, then drop it. Run before every cutover.
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

```bash
# Is the public endpoint alive end to end?
curl -i -X POST https://chess.waxim.ru/alice/webhook -H 'Content-Type: application/json' -d '{}'

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

# nginx
nginx -t && systemctl reload nginx
tail -f /var/log/nginx/chess.waxim.ru.error.log

# Incus
incus list yura-chess
incus config device show yura-chess
```

`/health/ready` returns 503 while the database or schema check fails and reports
`engine: degraded: 0/2 workers` when Stockfish cannot start — a degraded engine
does not fail readiness, because the skill still answers position questions.
