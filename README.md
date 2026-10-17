# MeshBee — Spelling Practice for Grades 4–5

A self-contained spelling bee practice website: hear a word, type it, get
friendly letter-by-letter feedback. One Python process serves everything —
no database, no runtime pip dependencies, no build step, no CDN assets.

- Repo: **https://github.com/MaheshReddy/meshbee** · License: **MIT**
- Deploys: **main-on-green** — every green push to `main` refreshes a rolling
  GitHub release that the host auto-installs (see *Hosting & auto-deploy*).

MeshBee is a proper installable Python package (`meshbee`), managed with
[uv](https://docs.astral.sh/uv/). The runtime stays **stdlib-only** — the
packaging exists purely for build/deploy ergonomics.

## Run it

### From a checkout

```bash
uv run meshbee                        # http://127.0.0.1:8765 (uv manages a local venv)
uv run meshbee --corpus sample        # use the small bundled test corpus
# or, with no tooling at all (plain python3):
python3 server.py --port 9000 --data-dir ./data
```

### Deploy to a server (build once, install anywhere)

```bash
# on your machine
uv build                              # -> dist/meshbee-1.1.0-py3-none-any.whl

# copy the wheel to the server, then:
uv tool install ./meshbee-1.1.0-py3-none-any.whl     # or: pipx install ./meshbee-*.whl
meshbee --port 8765 --data-dir /var/lib/meshbee --corpus /var/lib/meshbee/corpus.json
```

No uv/pipx on the box? A plain venv works too:
`python3 -m venv v && v/bin/pip install meshbee-*.whl && v/bin/meshbee ...`

The wheel bundles the frontend (`static/`) and the sample corpus as package
data (served via `importlib.resources`), so a single `.whl` file is the whole
deploy artifact.

### Flags

| Flag | Default | Purpose |
|------|---------|---------|
| `--port` | `8765` | HTTP port |
| `--data-dir` | `./data` | Where profiles/lists/progress JSON files live |
| `--corpus` | *(auto)* | Corpus resolution: this flag (a path, or `sample` for the bundled test corpus) → `./corpus.json` in the working directory (dev convenience) → the corpus bundled in the package → empty with a friendly notice (custom lists still work). |
| `--host` | `127.0.0.1` | Bind address (use `0.0.0.0` to serve the LAN) |

Requirements: Python 3.9+ (stdlib only at runtime). Frontend: Chrome / Edge / Safari.

## Hosting & auto-deploy (main-on-green)

- **CI** (`.github/workflows/ci.yml`): every PR and push to `main` runs the
  unit tests (Python 3.9 + 3.12), the node letter-filter tests, and the
  end-to-end API check.
- **Rolling release**: on every *green* push to `main`, CI bakes the git sha
  into `meshbee/_build_info.py`, builds the wheel, and recreates the
  `latest` prerelease with the wheel attached and `commit: <sha>` in the
  body. No tag ceremony — merged means (about to be) deployed.
- **Host updater** (`deploy/update.py`, stdlib-only): a systemd timer polls
  the release every 10 minutes; on a new sha it backs up the data dir,
  installs the wheel into the venv, restarts the service, and health-checks
  `GET /api/version` (the reported `build` must equal the new sha). Failure →
  automatic rollback to the previous wheel and the sha is never retried.
- Full host setup, rollback, and the "deploy assets never self-update"
  policy: **`deploy/README.md`**.

## Tests

```bash
python3 -m unittest -v                  # 66 unit tests
python3 e2e_check.py                    # end-to-end HTTP exercise (spawns its own server)
```

## Project layout

```
pyproject.toml       packaging (setuptools backend, zero runtime deps, `meshbee` CLI)
server.py            thin checkout shim -> meshbee.server:main
meshbee/             the installable package
  __init__.py
  server.py          stdlib HTTP server: JSON API + static file serving
  static/            index.html, app.js, letters.js, style.css (vanilla, no frameworks)
  corpus.json        the real word corpus — bundled into the wheel (package data)
  corpus.sample.json small 15-word corpus, bundled; selected with --corpus sample
word_list.txt        corpus provenance (source word list)
data/                runtime JSON data (gitignored): profiles.json, lists.json,
                     progress-<profileId>.json — atomic writes, schemaVersion: 1
test_server.py       unittest suite
test_letterfilter.js node tests for the voice-spelling letter filter
e2e_check.py         scripted end-to-end API verification
.github/workflows/   CI: test (3.9 + 3.12) and the rolling-release job
deploy/              host assets (systemd units, stdlib updater, Caddyfile) —
                     installed once by a human, never self-updated
```

## Corpus schema

```json
{"words": [{"word": "necessary", "tier": 2,
            "definition": "needed; something you must have or do",
            "sentence": "It is necessary to wear a helmet when biking.",
            "category": "double-consonants"}]}
```

`tier`: 1 = Warm-up, 2 = Challenger, 3 = Bee-Ready.

## API

All request/response bodies are JSON.

| Method & path | Purpose |
|---|---|
| `GET /api/corpus/meta` | `{loaded, wordCount, tiers, tierNames, categories}` |
| `GET /api/version` | `{version, build, corpusWords, schemaVersion}` — used by the host updater's health check (`build` is the CI-baked git sha, `"dev"` in a checkout) |
| `GET /api/profiles` | List profiles |
| `POST /api/profiles` | Create: `{name, avatar:{color,icon}}` (duplicate names → 409) |
| `PATCH /api/profiles/{id}` | Rename / change avatar |
| `DELETE /api/profiles/{id}` | Delete profile + its progress |
| `GET /api/profiles/{id}/progress` | `{sessionsPlayed, totalStars, bestStreak, trickyWords, trickyCount}` |
| `GET /api/lists` | All custom lists (shared across profiles) |
| `POST /api/lists/validate` | Dry-run parse of textarea text → `{entries, errors, warnings}` |
| `POST /api/lists` | Create: `{name, text}`. 400 + per-line `errors` on invalid lines; duplicate names → 409; duplicate words collapsed with `warnings` |
| `PUT /api/lists/{id}` | Edit (same body/validation as create) |
| `DELETE /api/lists/{id}` | Delete list |
| `POST /api/session` | `{profileId, source}` where source is `{type:"tier",tier:1..3}`, `{type:"list",listId}`, or `{type:"tricky"}`. Returns `{sessionId, label, count, entries:[{w, definition, sentence}]}` |
| `POST /api/session/{id}/answer` | `{attempt}` → server grades: `{correct, correctWord, diff, streak, index, total, done, summary?}` |
| `POST /api/session/{id}/hint` | `{kind:"definition"\|"sentence"\|"peek", ttsBroken?}` — records hint usage (affects stars) |

Sessions live in server memory (an interrupted session simply isn't counted);
all progress, profiles, and lists are flat JSON files written atomically
(temp file + `os.replace`). Every persisted file carries
`"schemaVersion": 1`; files without the field are treated as version 1, and
files from a *newer* MeshBee are refused with a clear error (restore a
backup or update the app) — no silent corruption.

### Grading rules

Case-insensitive; leading/trailing whitespace trimmed; internal runs of spaces
collapsed; curly apostrophes normalized to straight. Internal hyphens and
apostrophes **must** match (`dont` ≠ `don't`). Wrong answers return an aligned
letter diff (`difflib.SequenceMatcher`) with segment ops:
`match`, `wrong` (substitution), `extra` (letters to remove), `missing`
(letters to add).

### Stars

- 3 stars — every word correct **and** no definition/sentence hints (replay,
  slow replay, and post-answer spell-out are always free)
- 2 stars — ≥ 80% correct (= 8/10 on a standard session)
- 1 star — ≥ 50% correct (= 5/10)
- otherwise: "Keep buzzing!"

For custom lists shorter than 10 words the thresholds scale proportionally
(all-correct / 80% / 50%) — flagged as a PRD interpretation below.

### Mastery / tricky words

A missed word becomes "tricky". It is mastered (leaves the tricky pool) after
being spelled correctly **2 times in a row** after the miss. The tricky-words
session becomes available once a profile has 3+ unmastered missed words.

## Hearing the word

- **Hear the word** (hero button) — unlimited, free, spoken on each
  presentation.
- **Slowly** — reduced-rate replay, also free.
- **Definition / Sentence** — spoken hints; using either costs the 3rd star.
- **Spell it to me** — *post-answer only* (feedback card and summary): speaks
  the word, then each letter with natural pauses, then the word again
  ("necessary… N, E, C, E, S, S, A, R, Y… necessary"). Free — the answer is
  already locked in. Hyphens/apostrophes are spoken by name.

## Voice spelling (Chrome/Edge)

Kids can **say letters out loud** instead of typing. Press the mic button
next to the answer box to toggle listening (it pulses while live); say
letters one at a time — "b", "ee", "see", "double u", "hyphen" — and they
appear in the answer box. Spoken commands: **"submit"/"done"/"enter"**
(check the answer), **"undo"/"backspace"/"delete"** (remove a letter),
**"clear"/"start over"**, **"repeat"/"again"** (hear the word once more).

Details and limits:

- **Chrome/Edge only** (Web Speech Recognition). The mic button is hidden on
  Firefox/Safari — typing always works everywhere.
- **Privacy note**: while the mic is on, Chrome streams the audio to Google's
  speech service for transcription. Typed mode is fully offline (TTS voices
  may be network voices, but no microphone audio is involved). The mic is
  push-to-talk: it only listens while the button is toggled on, and it
  switches itself off whenever an answer is submitted or a new word appears.
- **Remote deploys need HTTPS**: browsers only allow microphone access on
  secure origins (`https://…`) or `localhost`. A LAN deploy over plain HTTP
  will hide/fail the mic — typed mode is unaffected.
- Letter homophones are post-filtered deterministically ("bee"→B, "why"→Y,
  "are"→R, "cue"→Q, "zed"→Z, …; see `meshbee/static/letters.js`). Fast
  letter runs that the recognizer glues together ("abc") are split
  per-character. Only **final** recognition results commit letters — interim
  text shows as a faint "Hearing: …" indicator.
- **Echo protection**: recognition pauses automatically while MeshBee itself
  is speaking, so the TTS voice can't type into the box.
- Mic permission denied → a friendly note appears and the toggle turns off;
  typing is unaffected.
- Settings dialog has a **"Voice spelling (Chrome)"** toggle (default on
  where supported, hidden elsewhere; persisted per device in localStorage).

Headless tests for the letter filter: `node test_letterfilter.js`.

## Keyboard shortcuts

| Key | Action |
|---|---|
| `Enter` | Submit answer (or advance past feedback) |
| `F2` or `Alt+R` | Hear the word again (free, unlimited) |
| `Alt+S` | Hear it slowly |
| `Alt+D` | Hear the definition (counts as a hint) |
| `Alt+E` | Hear it in a sentence (counts as a hint) |
| any key | Skip the auto-advance after a correct answer |
| `Esc` | Close overlays |

`Alt+letter` was chosen over `Ctrl+letter` to avoid browser-critical
shortcuts (`Ctrl+R` reloads). `F2` is a convenient single-key replay that
browsers don't claim. During feedback, keys aimed at the "Spell it to me"
button activate it rather than advancing.

## NFR-6 tradeoff — "the word must not be visible before answering"

TTS is performed client-side (Web Speech API), so the browser **must** know
the word to speak it. True server-side secrecy is therefore impossible. The
implemented best-effort design:

1. The session payload carries each word **base64-obfuscated** (`w` field) —
   never cleartext JSON.
2. The word is additionally **masked out of its own definition and sentence**
   in the payload (`_____`); the client substitutes it back **for speech
   only**.
3. The frontend never writes the word into the DOM before the answer is
   submitted. The only exception is the explicit **"Show word to a grown-up"**
   peek overlay, which exists as the TTS-failure fallback (FR-19) and is
   not penalized when TTS is broken.
4. Grading is **server-side** (`POST .../answer`): the client never decides
   correctness, and progress records can't be forged by typo.

A determined kid with devtools can still decode base64 — this is documented,
accepted, best-effort protection per the PRD resolution.

## TTS notes (FR-19/FR-20)

### Voice selection

Voices are ranked by a quality heuristic and the best one is picked
automatically (deterministically — ties broken by name):

1. **Google voices** (bundled with Chrome, e.g. "Google US English") — best
2. **Microsoft Natural/Online voices** (bundled with Edge) — near-equal
3. **Known-good Apple voices** (Samantha, Alex, Siri, … on Safari/macOS/iOS)
4. Any **network voice** (`localService: false` — network voices usually
   sound far better than generic local ones)
5. Older local Microsoft desktop voices
6. Unknown local voices
7. **espeak-style robotic voices** (common on bare Linux) — last resort

Within each band, en-US is preferred, then en-GB, then other English.
`voiceschanged` is handled properly: browsers load voices asynchronously (and
sometimes in batches), so the list is re-ranked and re-picked every time it
changes — an early empty/partial list is never locked in. The utterance's
`lang` is always set from the chosen voice to avoid mismatched-language
mangling.

### Voice settings (gear button — top bar and practice screen)

- **Reading voice** dropdown (best-first, "— online" marks network voices;
  "Automatic" shows which voice it resolved to). Persisted in `localStorage`
  (a device-level preference — voices are hardware/browser-specific, not
  profile data).
- **Talking speed** slider: 0.6×–1.1×, default 0.9× (slightly slower than
  normal helps kids). The "Slowly" button scales relative to this base rate.
- **Test voice** speaks a sample word + sentence.

### Failure & quality fallbacks

- **Hard failure** (no `speechSynthesis`, zero voices after a 3s grace
  period, utterance errors, utterance never starts): kid-friendly banner +
  "Show word to a grown-up" peek button (star-free).
- **Poor-voices-only detection**: if every available voice is known-poor (no
  Google/Microsoft/Apple/network voices — the classic espeak-only Linux
  setup), a gentle one-time dismissible banner suggests Chrome/Edge and
  points at the grown-up peek, which is enabled and star-free in this state
  too. Play is never blocked.

### Voice troubleshooting ("the voice is not clear")

| Situation | Fix |
|---|---|
| Robotic/unintelligible voice on Linux | Use **Chrome or Edge** — they bundle high-quality Google/Microsoft voices. Firefox and bare-OS browsers fall back to espeak, which is the robotic voice. |
| Voice still unclear in Chrome/Edge | Open the gear (voice settings) and pick a "Google …" or "Microsoft … Online" voice explicitly; make sure the device is online (the best voices are network-backed). |
| Too fast / too slow | Drag the "Talking speed" slider (persists per device). |
| No sound at all | Check system volume/mute; the app shows a banner and enables "Show word to a grown-up" if speech is truly unavailable. |
| Word is still ambiguous | Use the free "Slowly" replay, or the definition/sentence hints (those cost the 3rd star). |

Best experience: **Chrome or Edge on desktop, online**. Safari on macOS/iOS
is also good (high-quality local voices, works offline).

## Roadmap

- **Smarter homophone handling** for voice spelling: letter-name grammar
  biasing and per-kid pronunciation calibration, building on the existing
  `letters.js` filter and the `answerInput` controller seam.

## PRD deviations / flags for the Product Owner

1. **Star thresholds on short sessions** (FR-11 vs FR-10): the PRD defines
   stars as 10/10, ≥8, ≥5 but allows 3–9-word custom-list sessions. I scaled
   proportionally (all / ≥80% / ≥50%), which is identical at 10 words.
2. **Peek button visibility**: the grown-up peek button appears when TTS is
   hard-broken **or** when only known-poor (robotic) voices exist; in both
   states peeks are star-free (TTS can't do its job either way — this extends
   the PRD's "when TTS is broken" wording to unintelligible-voice setups).
   Working, decent TTS + peek would be a star-costing hint; that path exists
   in the API (`kind:"peek"`, `ttsBroken:false` counts as a hint) but has no
   UI.
3. **In-session hint tracking granularity**: stars drop with *any*
   definition/sentence hint in the session (per FR-11 "no hints"); per-word
   hint counts are also persisted to the profile's word history for future
   reporting.
4. **Abandoned sessions** aren't counted in `sessionsPlayed` and in-flight
   answers of an abandoned session *are* recorded to word history (they
   happened). Sessions are held in memory, so a server restart mid-session
   drops that session (progress already answered is safe on disk).
5. **Multi-word entries** (e.g. "ice cream") are accepted in custom lists;
   internal spaces are collapsed when grading.
6. **Profile names must be unique** (case-insensitive) — the PRD doesn't say,
   but duplicate "pick your name" entries would be ambiguous for kids.
7. **"Spell it to me" is free** (doesn't cost stars): it is only available
   after the answer is locked in, so it can't help with the current word.
