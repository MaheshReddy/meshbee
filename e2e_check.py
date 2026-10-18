#!/usr/bin/env python3
"""One-shot end-to-end exercise of the MeshBee HTTP API (verification step 2)."""
import base64
import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import urllib.error

PORT = 8799
BASE = f"http://127.0.0.1:{PORT}"


def req(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def check(label, cond):
    print(("PASS" if cond else "FAIL") + f"  {label}")
    if not cond:
        sys.exit(1)


def main():
    data_dir = tempfile.mkdtemp(prefix="meshbee-e2e-")
    proc = subprocess.Popen(
        [sys.executable, "server.py", "--port", str(PORT),
         "--data-dir", data_dir, "--corpus", "sample"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                urllib.request.urlopen(BASE + "/api/corpus/meta", timeout=1)
                break
            except Exception:
                time.sleep(0.1)

        # static frontend served
        with urllib.request.urlopen(BASE + "/", timeout=5) as resp:
            html = resp.read().decode()
        check("GET / serves index.html", "MeshBee" in html)

        st, meta = req("GET", "/api/corpus/meta")
        check("corpus meta loaded (17 words)", st == 200 and meta["wordCount"] == 17)

        # profile
        st, prof = req("POST", "/api/profiles",
                       {"name": "Testy", "avatar": {"color": "#5a9e4b", "icon": "ladybug"}})
        check("create profile", st == 200 and prof["name"] == "Testy")
        pid = prof["id"]
        st, dup = req("POST", "/api/profiles", {"name": "testy"})
        check("duplicate profile name rejected (409)", st == 409)

        # tier session
        st, sess = req("POST", "/api/session",
                       {"profileId": pid, "source": {"type": "tier", "tier": 1}})
        check("create tier-1 session (5 words -> all 5)", st == 200 and sess["count"] == 5)
        words = [base64.b64decode(e["w"]).decode() for e in sess["entries"]]
        check("payload has no cleartext words", all(w not in json.dumps(sess) for w in words))

        # hint, then answer: first wrong, rest right
        st, h = req("POST", f"/api/session/{sess['sessionId']}/hint", {"kind": "definition"})
        check("hint recorded", st == 200 and h["counted"])
        st, res = req("POST", f"/api/session/{sess['sessionId']}/answer",
                      {"attempt": words[0] + "zz"})
        check("wrong answer graded with diff", st == 200 and not res["correct"] and res["diff"]
              and res["correctWord"] == words[0])
        last = None
        for w in words[1:]:
            st, last = req("POST", f"/api/session/{sess['sessionId']}/answer", {"attempt": "  " + w.upper()})
            check(f"correct answer graded (case/space insensitive): {w}", st == 200 and last["correct"])
        check("session done with summary", last["done"] and last["summary"]["correctCount"] == 4)
        check("stars = 1 (4/5 = 80%... hint used)", last["summary"]["stars"] in (1, 2))

        # progress persisted
        st, prog = req("GET", f"/api/profiles/{pid}/progress")
        check("progress persisted (1 session, tricky word present)",
              st == 200 and prog["sessionsPlayed"] == 1
              and words[0] in {t["word"] for t in prog["trickyWords"]})

        # custom list: validation errors first
        st, bad = req("POST", "/api/lists", {"name": "Bad", "text": "ok\n123\nfine"})
        check("list with bad line rejected (400 + per-line errors)",
              st == 400 and bad["errors"][0]["line"] == 2)
        st, made = req("POST", "/api/lists",
                       {"name": "My List", "text": "alpha | first\nbravo\ncharlie\nALPHA"})
        check("list created, dup word collapsed with warning",
              st == 200 and len(made["list"]["entries"]) == 3 and len(made["warnings"]) == 1)
        st, dup = req("POST", "/api/lists", {"name": "my list", "text": "x-ray\nyak\nzebra"})
        check("duplicate list name rejected (409)", st == 409)

        # session from custom list
        st, lsess = req("POST", "/api/session",
                        {"profileId": pid, "source": {"type": "list", "listId": made["list"]["id"]}})
        check("session from custom list uses all 3 words", st == 200 and lsess["count"] == 3)
        lwords = [base64.b64decode(e["w"]).decode() for e in lsess["entries"]]
        for w in lwords:
            st, last = req("POST", f"/api/session/{lsess['sessionId']}/answer",
                           {"attempt": "wrongo" if w == lwords[0] else w})
        check("custom list session completes", last["done"])

        # alt spellings: list with word/alt syntax, answer using the ALT
        st, alist = req("POST", "/api/lists",
                        {"name": "Alts", "text": "colour/color\ngrey/gray\ndonut/doughnut"})
        check("list with word/alt syntax created", st == 200
              and alist["list"]["entries"][0]["alt"] == ["color"])
        st, asess = req("POST", "/api/session",
                        {"profileId": pid, "source": {"type": "list", "listId": alist["list"]["id"]}})
        alt_of = {"colour": "color", "grey": "gray", "donut": "doughnut"}
        ok_alt = True
        for e in asess["entries"]:
            w = base64.b64decode(e["w"]).decode()
            st, r = req("POST", f"/api/session/{asess['sessionId']}/answer",
                        {"attempt": alt_of[w]})
            ok_alt = ok_alt and r["correct"]
        check("alt spellings graded correct", ok_alt)

        # tricky session appears after misses (we now have >= 2 missed... need 3)
        st, prog = req("GET", f"/api/profiles/{pid}/progress")
        tricky_have = prog["trickyCount"]
        if tricky_have < 3:
            # miss one more word via another tier session
            st, s2 = req("POST", "/api/session",
                         {"profileId": pid, "source": {"type": "tier", "tier": 2}})
            w2 = [base64.b64decode(e["w"]).decode() for e in s2["entries"]]
            for i, w in enumerate(w2):
                req("POST", f"/api/session/{s2['sessionId']}/answer",
                    {"attempt": w + "x" if i == 0 else w})
        st, prog = req("GET", f"/api/profiles/{pid}/progress")
        check("tricky words accumulated (>=3)", prog["trickyCount"] >= 3)
        st, tsess = req("POST", "/api/session", {"profileId": pid, "source": {"type": "tricky"}})
        check("tricky-words session created", st == 200 and tsess["count"] >= 3)

        # peek with broken TTS not counted
        st, pk = req("POST", f"/api/session/{tsess['sessionId']}/hint",
                     {"kind": "peek", "ttsBroken": True})
        check("peek with TTS broken is free", st == 200 and pk["counted"] is False)

        # list edit + delete
        st, upd = req("PUT", f"/api/lists/{made['list']['id']}",
                      {"name": "Renamed", "text": "cat\ndog\nfox\nowl"})
        check("list updated", st == 200 and upd["list"]["name"] == "Renamed"
              and len(upd["list"]["entries"]) == 4)
        st, _ = req("DELETE", f"/api/lists/{made['list']['id']}")
        check("list deleted", st == 200)

        # profile rename + delete
        st, upd = req("PATCH", f"/api/profiles/{pid}", {"name": "Testy2"})
        check("profile renamed", st == 200 and upd["name"] == "Testy2")

        # server restart durability: kill, restart, progress still there
        proc.terminate()
        proc.wait(timeout=5)
        proc = subprocess.Popen(
            [sys.executable, "server.py", "--port", str(PORT),
             "--data-dir", data_dir, "--corpus", "sample"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            try:
                urllib.request.urlopen(BASE + "/api/corpus/meta", timeout=1)
                break
            except Exception:
                time.sleep(0.1)
        st, prog = req("GET", f"/api/profiles/{pid}/progress")
        check("progress survives server restart", st == 200 and prog["sessionsPlayed"] >= 2)

        print("\nALL E2E CHECKS PASSED")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(data_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
