# MeshBee host deployment

One small Linux host runs MeshBee behind Caddy, auto-updating from the
rolling GitHub release (`latest`) that CI refreshes on every green push to
`main`.

> **IMPORTANT — this directory never self-updates.**
> Everything in `deploy/` is installed **once, by a human**, after review.
> The auto-updater only ever installs application wheels into the venv; it
> never touches these units, `update.py`, or the Caddy config. If you change
> a file here, review it and copy it to the host yourself. This is a
> deliberate supply-chain boundary: a merged PR must not be able to rewrite
> the deploy path it ships through.

## Host requirements

- Linux with systemd
- `python3` (3.9+) with the `venv` module (`apt install python3-venv`)
- [Caddy](https://caddyserver.com/docs/install) for HTTPS
  (`apt install caddy` on Debian/Ubuntu with the Caddy repo)
- Outbound HTTPS to github.com / api.github.com. No git, node, uv, curl,
  or jq needed — the updater is a stdlib Python script.

## Setup (as root)

1. Create the service user and directories:

   ```sh
   useradd --system --home /var/lib/meshbee --shell /usr/sbin/nologin meshbee
   mkdir -p /var/lib/meshbee /opt/meshbee/wheels
   chown meshbee:meshbee /var/lib/meshbee
   ```

2. Create the venv the app runs from:

   ```sh
   python3 -m venv /opt/meshbee/venv
   ```

3. Copy the deploy assets (review them first):

   ```sh
   cp update.py /opt/meshbee/update.py
   cp meshbee.service meshbee-update.service meshbee-update.timer /etc/systemd/system/
   systemctl daemon-reload
   ```

4. First install + start (the timer does this too, but doing it by hand
   verifies everything):

   ```sh
   python3 /opt/meshbee/update.py     # downloads latest wheel, installs, starts health-checked
   systemctl enable --now meshbee
   systemctl enable --now meshbee-update.timer
   ```

5. Caddy for HTTPS (the voice-spelling mic requires a secure origin):

   ```sh
   cp Caddyfile.example /etc/caddy/Caddyfile   # edit the hostname first
   systemctl reload caddy
   ```

6. Check:

   ```sh
   curl -s http://127.0.0.1:8765/api/version
   journalctl -u meshbee-update -n 50
   ```

## How updates work

- `meshbee-update.timer` runs `update.py` every 10 minutes.
- It polls the `latest` release; the change signal is the `commit: <sha>`
  line in the release body (the wheel's version string stays constant
  between rolling releases).
- On change: back up `/var/lib/meshbee` to `/var/lib/meshbee-backups/<ts>`
  (14 kept) → install the wheel → restart → health-check
  `/api/version` (must report the new build sha within 30s).
- On a failed health check: the sha is recorded in `/opt/meshbee/bad_shas`
  (never retried — ship a fix as a new commit), and the previous wheel is
  reinstalled automatically.

## Rollback by hand

```sh
ls /opt/meshbee/wheels/                 # <sha>/ dirs: current + previous
/opt/meshbee/venv/bin/pip install --force-reinstall /opt/meshbee/wheels/<previous-sha>/*.whl
systemctl restart meshbee
echo <previous-sha> > /opt/meshbee/current_sha
```

Data restore: stop the service, copy a snapshot from
`/var/lib/meshbee-backups/<ts>/` over `/var/lib/meshbee/`, start it again.
