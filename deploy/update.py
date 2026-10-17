#!/usr/bin/env python3
"""MeshBee host auto-updater — Python STDLIB ONLY.

Polls the rolling GitHub release ("latest" prerelease), and when its commit
sha changes: downloads the wheel, backs up the data dir, installs into the
venv, restarts the service, health-checks /api/version, and auto-rolls-back
to the previous wheel on failure (marking the sha bad so it is never retried).

Installed ONCE by a human to /opt/meshbee/update.py and run by
meshbee-update.timer as root. This file is deliberately NOT self-updating:
changes to it must be reviewed and copied to the host by a human.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------
REPO = "MaheshReddy/meshbee"
RELEASE_TAG = "latest"
PORT = 8765

BASE_DIR = "/opt/meshbee"
VENV_PIP = os.path.join(BASE_DIR, "venv", "bin", "pip")
WHEELS_DIR = os.path.join(BASE_DIR, "wheels")      # wheels/<sha>/<file>.whl
CURRENT_SHA_FILE = os.path.join(BASE_DIR, "current_sha")
BAD_SHAS_FILE = os.path.join(BASE_DIR, "bad_shas")  # never retried

DATA_DIR = "/var/lib/meshbee"
BACKUP_DIR = "/var/lib/meshbee-backups"
BACKUPS_KEEP = 14
WHEEL_DIRS_KEEP = 2          # current + previous (rollback target)

SERVICE = "meshbee"
HEALTH_URL = f"http://127.0.0.1:{PORT}/api/version"
HEALTH_TIMEOUT_S = 30

API_URL = f"https://api.github.com/repos/{REPO}/releases/tags/{RELEASE_TAG}"
UA = {"User-Agent": "meshbee-updater"}   # GitHub API requires a User-Agent


def log(msg: str) -> None:
    print(f"[meshbee-update] {msg}", flush=True)


def fetch_json(url: str) -> dict:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def read_file(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def write_file(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def run(cmd: list) -> None:
    log("run: " + " ".join(cmd))
    subprocess.run(cmd, check=True)


def download(url: str, dest: str) -> None:
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=120) as resp, open(dest, "wb") as f:
        shutil.copyfileobj(resp, f)


def backup_data_dir() -> None:
    if not os.path.isdir(DATA_DIR):
        log(f"no data dir at {DATA_DIR} yet — skipping backup")
        return
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(BACKUP_DIR, stamp)
    shutil.copytree(DATA_DIR, dest)
    log(f"backed up data dir -> {dest}")
    backups = sorted(os.listdir(BACKUP_DIR))
    for old in backups[:-BACKUPS_KEEP]:
        shutil.rmtree(os.path.join(BACKUP_DIR, old), ignore_errors=True)


def install_wheel(wheel_path: str) -> None:
    run([VENV_PIP, "install", "--quiet", "--force-reinstall", wheel_path])


def restart_service() -> None:
    run(["systemctl", "restart", SERVICE])


def health_check(expect_sha: str | None) -> bool:
    """Healthy = /api/version answers 200 AND (when expect_sha is given) the
    reported build sha matches — i.e. the new code is actually running."""
    deadline = time.time() + HEALTH_TIMEOUT_S
    last_err = "no response"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(HEALTH_URL, timeout=3) as resp:
                info = json.loads(resp.read())
            if expect_sha is None or info.get("build") == expect_sha:
                log(f"health OK: version={info.get('version')} build={info.get('build')} "
                    f"corpusWords={info.get('corpusWords')}")
                return True
            last_err = f"build mismatch: running {info.get('build')}, expected {expect_sha}"
        except Exception as exc:
            last_err = str(exc)
        time.sleep(2)
    log(f"health check FAILED after {HEALTH_TIMEOUT_S}s: {last_err}")
    return False


def wheel_for_sha(sha: str) -> str | None:
    d = os.path.join(WHEELS_DIR, sha)
    if os.path.isdir(d):
        wheels = [f for f in os.listdir(d) if f.endswith(".whl")]
        if wheels:
            return os.path.join(d, wheels[0])
    return None


def prune_wheels(keep_shas: list) -> None:
    if not os.path.isdir(WHEELS_DIR):
        return
    for name in os.listdir(WHEELS_DIR):
        if name not in keep_shas:
            shutil.rmtree(os.path.join(WHEELS_DIR, name), ignore_errors=True)


def rollback(previous_sha: str) -> None:
    wheel = wheel_for_sha(previous_sha)
    if not wheel:
        log(f"!! ROLLBACK IMPOSSIBLE: no stored wheel for previous sha {previous_sha[:12]}. "
            f"Service may be down — manual intervention required.")
        return
    log(f"rolling back to {previous_sha[:12]} ...")
    install_wheel(wheel)
    restart_service()
    if health_check(None):
        log("rollback successful — service healthy on previous wheel")
    else:
        log("!! rollback did NOT restore health — manual intervention required.")


def main() -> int:
    try:
        release = fetch_json(API_URL)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            log("no 'latest' release published yet — nothing to do")
            return 0
        log(f"GitHub API error: {exc}")
        return 1
    except Exception as exc:
        log(f"could not reach GitHub: {exc}")
        return 1

    match = re.search(r"commit:\s*([0-9a-f]{7,40})", release.get("body") or "")
    if not match:
        log("release body has no 'commit: <sha>' line — refusing to act")
        return 1
    sha = match.group(1)

    assets = [a for a in release.get("assets", []) if a["name"].endswith(".whl")]
    if not assets:
        log("release has no wheel asset — refusing to act")
        return 1
    asset = assets[0]

    current = read_file(CURRENT_SHA_FILE)
    if sha == current:
        return 0  # up to date — stay quiet for the 10-minute timer
    if sha in read_file(BAD_SHAS_FILE).split():
        log(f"sha {sha[:12]} previously failed health check — skipping (fix it with a new commit)")
        return 0

    log(f"new build: {sha[:12]} (installed: {current[:12] if current else 'none'})")
    wheel_path = os.path.join(WHEELS_DIR, sha, asset["name"])
    download(asset["browser_download_url"], wheel_path)
    log(f"downloaded {asset['name']} ({os.path.getsize(wheel_path)} bytes)")

    backup_data_dir()
    install_wheel(wheel_path)
    restart_service()

    if health_check(sha):
        write_file(CURRENT_SHA_FILE, sha)
        prune_wheels([sha] + ([current] if current else []))
        log(f"update to {sha[:12]} complete")
        return 0

    # Unhealthy: never retry this sha, roll back to the previous wheel.
    write_file(BAD_SHAS_FILE, (read_file(BAD_SHAS_FILE) + "\n" + sha).strip() + "\n")
    log(f"!! UPDATE TO {sha[:12]} FAILED HEALTH CHECK — marked bad, attempting rollback")
    if current:
        rollback(current)
    else:
        log("!! no previous version to roll back to (first install failed)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
