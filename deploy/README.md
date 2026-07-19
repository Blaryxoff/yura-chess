# Deploying «Шахматы с Юрой»

Operational runbook. The topology, ports and secrets themselves are described in
[INFRASTRUCTURE.md](INFRASTRUCTURE.md).

## Layout

| File | Purpose |
| --- | --- |
| `compose.staging.yml` | Firebat staging; joins the existing `staging-mariadb` |
| `compose.production.yml` | Firebat production; own MariaDB 11.4 in the `yura-chess` Incus stack |
| `deploy.sh` | Idempotent deploy of one immutable tag, with migrations and health smoke |
| `rollback.sh` | Put the previous application image back |
| `nginx/chess.waxim.ru.conf` | Host nginx vhost: TLS, limits, rate limiting |
| `mariadb/backup.sh` | Scheduled dump, off-host copy, retention, alerting |
| `mariadb/restore-smoke.sh` | Restore the latest dump into a temporary database and verify it |
| `systemd/` | Daily backup and weekly restore-smoke units for the production Incus container |

## Build and publish

```bash
TAG="$(git rev-parse --short HEAD)"
docker build --tag "ghcr.io/blaryxoff/yura-chess:$TAG" .
docker push "ghcr.io/blaryxoff/yura-chess:$TAG"
```

Pushes to `main` also publish `ghcr.io/blaryxoff/yura-chess:<40-character-git-sha>`
through `.github/workflows/publish.yml`.

Only immutable tags are deployable. `deploy.sh` refuses `latest`.

## Deploy

```bash
deploy/deploy.sh staging "$TAG"
deploy/deploy.sh production "$TAG"
```

The script always runs the same steps, in this order:

1. validate the Compose file and pull the image;
2. bring the database up (production only) and wait for its health check;
3. run `alembic upgrade head` as a one-shot `migrate` container **to completion**;
4. start the application and wait for its health check;
5. poll `/health/ready` — on failure it puts the previously recorded image back and exits non-zero.

Re-running it with the same tag is safe: every step is idempotent.

Because the schema is migrated before the new code starts, **each migration must
stay compatible with the previous release**. That is what makes an application-only
rollback safe.

## Rollback

```bash
deploy/rollback.sh production            # the tag deploy.sh recorded as previous
deploy/rollback.sh production 1a2b3c4    # or an explicit one
```

Only the application is rolled back. Migrations are never run downwards; if a
release must lose a schema change, restore the pre-release backup instead:

```bash
deploy/mariadb/restore-smoke.sh /srv/yura-chess/backups/yura_chess-<stamp>.sql.gz  # verify first
# then restore into the live database during an announced outage
```

## Backups

`deploy/mariadb/backup.sh` runs from a systemd timer (daily). It refuses to
report success when the archive is missing, corrupt, implausibly small, or when
free space is below the configured floor, and it alerts through
`YURA_CHESS_BACKUP_ALERT_COMMAND`. A missing off-host target is itself an alert.
The S3-compatible bucket must also have a lifecycle expiration matching
`YURA_CHESS_BACKUP_RETENTION_DAYS`; local pruning cannot remove remote objects.

Verify restorability regularly and always before a cutover:

```bash
deploy/mariadb/restore-smoke.sh
```

It restores into `yura_chess_restore_smoke`, checks every canonical table and the
Alembic revision, then drops it. It refuses to touch the live database.

Install the units during provisioning, but enable them only after
`YURA_CHESS_BACKUP_S3_TARGET` and the matching credentials are configured:

```bash
install -m 0644 deploy/systemd/yura-chess-* /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now yura-chess-backup.timer yura-chess-restore-smoke.timer
```

## Cutover checklist

1. `deploy/mariadb/backup.sh` and confirm the off-host copy exists.
2. `deploy/mariadb/restore-smoke.sh` passes.
3. `deploy/deploy.sh staging "$TAG"` and `/health/ready` is `ready` on staging.
4. `deploy/deploy.sh production "$TAG"`.
5. External check through nginx: `curl -sS https://chess.waxim.ru/alice/webhook -X POST -d '{}'`
   returns 422 (the endpoint is reachable and validating), not 502.
6. Voice-only and screen-device QA in the Alice console before submitting for moderation.
