# Deploying «Шахматы с Юрой»

Operational runbook. The topology, ports and secrets themselves are described in
[INFRASTRUCTURE.md](INFRASTRUCTURE.md).

Unless a step says otherwise, every command here runs inside the production
container — `ssh firebat`, then `sudo -n incus exec yura-chess -- bash -lc '…'` —
where this repository is unpacked at `/srv/yura-chess/repo`. See
[*Reaching production*](INFRASTRUCTURE.md#reaching-production).

## Layout

| File | Purpose |
| --- | --- |
| `compose.production.yml` | Firebat production; own MariaDB 11.4 in the `yura-chess` Incus stack |
| `deploy.sh` | Idempotent deploy of one immutable tag, with migrations and health smoke |
| `rollback.sh` | Put the previous application image back |
| `nginx/yurachess.ru.conf` | Canonical host nginx vhost: TLS, limits, rate limiting |
| `nginx/chess.waxim.ru.conf` | Permanent redirects from the retired host |
| `mariadb/backup.sh` | Scheduled dump, off-host copy, retention, alerting |
| `mariadb/restore-smoke.sh` | Restore the latest dump into a temporary database and verify it |
| `systemd/` | Daily backup and weekly restore-smoke units for the production Incus container |

## Build and publish

```bash
TAG="$(git rev-parse HEAD)"
docker build --tag "ghcr.io/blaryxoff/yura-chess:$TAG" .
docker push "ghcr.io/blaryxoff/yura-chess:$TAG"
```

Pushes to `master` also publish `ghcr.io/blaryxoff/yura-chess:<40-character-git-sha>`
through `.github/workflows/publish.yml`.

Only immutable tags are deployable. `deploy.sh` refuses `latest`.

## Deploy

```bash
ssh firebat
sudo -n incus exec yura-chess -- bash -lc \
  '/srv/yura-chess/repo/deploy/deploy.sh production <40-character-git-sha>'
```

Use the full sha from `git rev-parse HEAD`. `deploy.sh` accepts any sha of seven
characters or more, but `publish.yml` tags the image `${{ github.sha }}` only, so
a short tag pulls nothing and the deploy fails at step 1.

The script always runs the same steps, in this order:

1. validate the Compose file and pull the image;
2. bring the database up and wait for its health check;
3. run `alembic upgrade head` as a one-shot `migrate` container **to completion**;
4. start the application and wait for its health check;
5. poll `/health/ready` — on failure it puts the previously recorded image back and exits non-zero.

Re-running it with the same tag is safe: every step is idempotent.

Because the schema is migrated before the new code starts, **each migration must
stay compatible with the previous release**. That is what makes an application-only
rollback safe.

The human-like experience and analytics releases add `0007_player_preferences` …
`0015_reclassify_ping_monitors` on top of `0006_alice_response_replay`. They are additive and run
in that order as one `alembic upgrade head`, so the previously deployed image
keeps working against the migrated schema.

## Deployed smoke

There is no separately maintained staging environment. Before a release, CI runs
the complete local and MariaDB suites. After deployment, an opt-in smoke talks to
the public webhook with throwaway Alice identities:

```bash
YURA_CHESS_DEPLOYED_URL=https://yurachess.ru \
  uv run pytest tests/e2e/test_deployed_webhook.py
```

Run this from a workstation checkout, not inside the container: it talks to the
public name and needs the test suite. It creates disposable production games, so
it is a release check rather than part of every local test run.

## Rollback

```bash
/srv/yura-chess/repo/deploy/rollback.sh production          # the tag deploy.sh recorded as previous
/srv/yura-chess/repo/deploy/rollback.sh production 1a2b3c4  # or an explicit one
```

Only the application is rolled back. Migrations are never run downwards; if a
release must lose a schema change, restore the pre-release backup instead:

```bash
/srv/yura-chess/repo/deploy/mariadb/restore-smoke.sh \
  /srv/yura-chess/backups/yura_chess-<stamp>.sql.gz  # verify first
# then restore into the live database during an announced outage
```

## Backups

`deploy/mariadb/backup.sh` runs from a systemd timer (daily). It refuses to
report success when the archive is missing, corrupt, implausibly small, or when
free space is below the configured floor, and it alerts through
`YURA_CHESS_BACKUP_ALERT_COMMAND`. A missing off-host target is itself an alert.
The S3-compatible bucket must also have a lifecycle expiration matching
`YURA_CHESS_BACKUP_RETENTION_DAYS`; local pruning cannot remove remote objects.

Verify restorability regularly as an independent operations check:

```bash
/srv/yura-chess/repo/deploy/mariadb/restore-smoke.sh
```

It restores into `yura_chess_restore_smoke`, checks every canonical table and the
Alembic revision, then drops it. It refuses to touch the live database.

Backup availability, off-host copy status and restore-smoke results do not gate an
application deploy. Their failures remain alerts that operators should resolve
separately; `deploy.sh` relies on immutable images, health checks and automatic
application rollback for the release path.

Install the units during provisioning, but enable them only after
`YURA_CHESS_BACKUP_S3_TARGET` and the matching credentials are configured:

```bash
install -m 0644 deploy/systemd/yura-chess-* /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now yura-chess-backup.timer yura-chess-restore-smoke.timer
```

## Host nginx vhost

`deploy.sh` never touches nginx: the vhost is a host file, installed by hand on
the Firebat host itself — these commands are the exception that does not run
inside the container. The
allowlist there names every public path one by one, so adding a page or a crawler
file to the application is not enough — the vhost must be reinstalled in the same
release, or nginx keeps answering 404 while every unit test passes.

```bash
sudo install -m 0644 deploy/nginx/yurachess.ru.conf /etc/nginx/sites-available/yurachess.ru.conf
sudo install -m 0644 deploy/nginx/chess.waxim.ru.conf /etc/nginx/sites-available/chess.waxim.ru.conf
sudo nginx -t && sudo systemctl reload nginx
```

Verify the whole crawlable surface afterwards:

```bash
for path in / /robots.txt /sitemap.xml /how-to-play /commands /coach /puzzles \
            /accessibility /blindfold /favicon.svg \
            /3e123263cd3a154a8aa32da5bc28cebd.txt; do
  printf '%s -> %s\n' "$path" "$(curl -s -o /dev/null -w '%{http_code}' "https://yurachess.ru$path")"
done

curl -sI https://chess.waxim.ru/how-to-play | grep -i '^location: https://yurachess.ru/how-to-play$'
```

Anything other than `200` means the deployed vhost is older than this repository.
`sudo nginx -T | grep -n -B3 -A8 'robots\|sitemap'` shows the effective configuration,
including snippets, when a path 404s despite being listed here.

## Cutover checklist

1. Confirm green CI and the published immutable image for `$TAG`.
2. `/srv/yura-chess/repo/deploy/deploy.sh production "$(git rev-parse HEAD)"` inside the container.
3. From a workstation checkout: `YURA_CHESS_DEPLOYED_URL=https://yurachess.ru uv run pytest tests/e2e/test_deployed_webhook.py`.
4. External check through nginx: `curl -sS https://yurachess.ru/webhooks/alice -X POST -d '{}'`
   returns 422 (the endpoint is reachable and validating), not 502.
   Reinstall the vhost too whenever a public path was added; see *Host nginx vhost*.
5. From a workstation checkout: `uv run python scripts/submit_indexnow.py` — tells Yandex and Bing the pages changed.
   It exits non-zero when an endpoint rejects the submission; skipping it only delays the crawl.
6. Voice-only and screen-device QA in the Alice console before submitting for moderation.
7. Open `https://yurachess.ru/#statistics` and confirm real/test and period filters render aggregate counts without identifiers.
