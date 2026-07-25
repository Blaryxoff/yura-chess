# Switching the canonical host to `yurachess.ru`

Run this once, in order. Steps 1–4 are safe at any time and change nothing for
visitors; step 5 is the cutover. Do the whole thing **before** Google Search
Console verification and before the outreach push — every link published against
the old host has to be re-earned otherwise.

## 0. Why HTTP-01 and not Cloudflare

`*.waxim.ru` uses the `dns-cloudflare` authenticator because a wildcard
certificate has no other option. `yurachess.ru` is a single host plus `www`, so
the webroot authenticator works — the same way `hire.jzazik.com` already does on
this box. **Cloudflare is not required for this domain.**

If Cloudflare is used anyway, keep every record **DNS-only (grey cloud)**. The
orange-cloud proxy terminates TLS at Cloudflare, and Firebat's `:443` is an
`ssl_preread` SNI demux that forwards raw TLS with the PROXY protocol — proxying
breaks both that demux and the Alice webhook.

## 1. DNS at the registrar

| Type | Name | Value |
| --- | --- | --- |
| A | `@` | `95.84.228.243` |
| A | `www` | `95.84.228.243` |

That is the inbound address the other vhosts already answer on. Do not use the
box's egress address — it differs.

Wait for propagation before step 3:

```bash
host yurachess.ru        # must print 95.84.228.243
host www.yurachess.ru
```

## 2. Install the vhost (HTTP only, so ACME can answer)

```bash
install -m 0644 deploy/nginx/yurachess.ru.conf /etc/nginx/sites-available/yurachess.ru.conf
ln -sf /etc/nginx/sites-available/yurachess.ru.conf /etc/nginx/sites-enabled/yurachess.ru.conf
```

The file also contains the TLS servers, and `nginx -t` fails until the
certificate exists. Comment out the two `listen 127.0.0.1:8443` blocks for this
step, or run step 3 first with the HTTP block alone.

## 3. Issue the certificate

```bash
certbot certonly --webroot -w /var/www/letsencrypt \
  -d yurachess.ru -d www.yurachess.ru
nginx -t && systemctl reload nginx
```

Renewal is picked up by the existing certbot timer; no extra unit is needed.

## 4. Verify the new host serves the site

```bash
for path in / /robots.txt /sitemap.xml /how-to-play /commands /coach /puzzles \
            /accessibility /blindfold /favicon.svg; do
  printf '%s -> %s\n' "$path" "$(curl -s -o /dev/null -w '%{http_code}' "https://yurachess.ru$path")"
done
curl -sI https://www.yurachess.ru/ | head -2   # expect 301 to https://yurachess.ru/
```

Both hosts answer at this point. Nothing is canonical yet — the pages still name
`chess.waxim.ru` in their `<link rel="canonical">`, which is correct until step 5.

## 5. Cutover

Only now flip the application's canonical host, in one release:

1. `PUBLIC_SITE_URL` in `src/yura_chess/presentation/website.py` →
   `https://yurachess.ru/`. Canonicals, `hreflang`, sitemap, robots, the JSON-LD
   graph and the IndexNow submitter all derive from it.
2. Update the host literals in `tests/test_health.py` and
   `tests/e2e/test_deployed_webhook.py`.
3. Replace the body of `chess.waxim.ru.conf`'s TLS server with
   `return 301 https://yurachess.ru$request_uri;`, keeping the Yandex
   verification file reachable so the old property stays verified during the move.
4. Deploy, reinstall both vhosts, reload nginx.
5. Point the Alice skill's webhook URL at `https://yurachess.ru/alice/webhook`
   in the Dialogs console, then re-run the deployed smoke.

## 6. Afterwards

- **Yandex.Webmaster**: add `yurachess.ru` as a new site — the verification file
  is per-host, so it needs its own. Then use *Смена адреса сайта* (site move) so
  the old host's signals transfer instead of being lost.
- **Google Search Console**: verify `yurachess.ru` by DNS TXT and submit the
  sitemap. Use *Change of Address* only if `chess.waxim.ru` was ever verified.
- `uv run python scripts/submit_indexnow.py` — the key is served on the new host
  by the same application, so the submission is accepted immediately.
- Update the site URL in `README.md`, `docs/yandex-skill-description.md`, the
  GitHub repository homepage, and the drafts in `docs/seo/`.
- Keep the redirect **permanently**. It is what carries the old host's history.
