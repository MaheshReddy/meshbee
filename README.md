# MeshBee — Spelling Practice for Grades 4–5

A self-contained spelling bee practice website: hear a word, type it, get
friendly letter-by-letter feedback. One Python process serves everything —
no database, no runtime pip dependencies, no build step, no CDN assets.

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
| `--corpus` | `./corpus.json` | Word corpus file. If missing, the app runs with a friendly "no words installed" notice (custom lists still work). The literal value `sample` uses the small bundled test corpus. |
| `--host` | `127.0.0.1` | Bind address (use `0.0.0.0` to serve the LAN) |

Requirements: Python 3.9+ (stdlib only at runtime). Frontend: Chrome / Edge / Safari.

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
  static/            index.html, app.js, style.css (vanilla, no frameworks)
  corpus.sample.json small 15-word corpus, bundled; selected with --corpus sample
corpus.json          full word corpus (authored separately; schema below; NOT bundled)
data/                runtime JSON data (gitignored): profiles.json, lists.json,
                     progress-<profileId>.json — all written atomically
test_server.py       unittest suite
e2e_check.py         scripted end-to-end API verification
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
(temp file + `os.replace`).

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

- Voice pick is deterministic: English voices sorted by (en-US first, local
  voices first, name) — overridable in the voice settings dialog (persisted in
  `localStorage`).
- Failure detection: missing `speechSynthesis` API, zero voices after a 3s
  grace period, utterance `onerror`, or an utterance that never starts within
  2.5s → a kid-friendly banner appears and the "Show word to a grown-up" peek
  button is enabled (not star-penalized while TTS is broken).

## Roadmap

- **Voice-spelling input** (kid says letters aloud; letters appear in the
  answer box). Planned approach: Web Speech Recognition
  (`webkitSpeechRecognition` / `SpeechRecognition`) in continuous mode with a
  letter-name grammar/post-filter (A–Z plus "hyphen" and "apostrophe"),
  confidence thresholding, and a visible push-to-talk toggle. The frontend is
  already structured for it: the answer box is owned by a small
  `answerInput` controller in `app.js` (`appendLetter` / `backspace` /
  `clear` / `submit`), so a recognition engine feeds letters through the same
  API the keyboard uses — no practice-flow changes needed. Recognition is
  Chrome/Edge-only and usually cloud-backed, so it will ship as an optional
  enhancement with the typed path always available.

## PRD deviations / flags for the Product Owner

1. **Star thresholds on short sessions** (FR-11 vs FR-10): the PRD defines
   stars as 10/10, ≥8, ≥5 but allows 3–9-word custom-list sessions. I scaled
   proportionally (all / ≥80% / ≥50%), which is identical at 10 words.
2. **Peek button visibility**: the grown-up peek button only appears when TTS
   is detected as broken (the PRD only defines its behavior in that state).
   Working TTS + peek would be a star-costing hint; that path exists in the
   API (`kind:"peek"`, `ttsBroken:false` counts as a hint) but has no UI.
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
