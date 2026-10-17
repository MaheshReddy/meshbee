#!/usr/bin/env python3
"""MeshBee — spelling bee practice server.

Python 3 stdlib only. One process serves the static frontend and the JSON API.

Usage:
    python3 server.py [--port 8765] [--data-dir ./data] [--corpus ./corpus.json]
"""
from __future__ import annotations

import argparse
import base64
import difflib
import json
import logging
import os
import random
import re
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

logger = logging.getLogger("meshbee")

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

SESSION_SIZE = 10
MIN_SESSION_WORDS = 3
MAX_WORD_LEN = 40
MAX_NAME_LEN = 30
MAX_LIST_NAME_LEN = 50
MAX_SESSIONS_KEPT = 200
MASTERY_STREAK = 2  # consecutive correct answers (after a miss) to master a word

VALID_TIERS = (1, 2, 3)
TIER_NAMES = {1: "Warm-up", 2: "Challenger", 3: "Bee-Ready"}

# word | definition | sentence  — word may contain letters, internal
# apostrophes/hyphens/spaces, must start and end with a letter.
WORD_RE = re.compile(r"^[A-Za-z](?:[A-Za-z' -]*[A-Za-z])?$")

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested)
# ---------------------------------------------------------------------------

def normalize_answer(text: str) -> str:
    """Normalize a spelling attempt: trim, lowercase, straighten curly
    apostrophes, collapse internal runs of whitespace to one space.
    Internal hyphens and apostrophes are preserved (they must match)."""
    t = (text or "").strip().lower()
    t = t.replace("\u2019", "'").replace("\u2018", "'")
    t = re.sub(r"\s+", " ", t)
    return t


def grade_attempt(attempt: str, correct: str) -> bool:
    return normalize_answer(attempt) == normalize_answer(correct)


_DIFF_OP_NAMES = {
    "equal": "match",
    "replace": "wrong",
    "delete": "extra",    # letters in the attempt that shouldn't be there
    "insert": "missing",  # letters missing from the attempt
}


def letter_diff(attempt: str, correct: str) -> list:
    """Aligned letter diff between the (normalized) attempt and correct word.
    Returns segments: {op: match|wrong|extra|missing, attempt: str, correct: str}.
    """
    a = normalize_answer(attempt)
    c = normalize_answer(correct)
    sm = difflib.SequenceMatcher(a=a, b=c, autojunk=False)
    segments = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        segments.append({
            "op": _DIFF_OP_NAMES[op],
            "attempt": a[i1:i2],
            "correct": c[j1:j2],
        })
    return segments


def compute_stars(correct_count: int, total: int, hints_used: bool) -> int:
    """3 stars = perfect session with no definition/sentence hints
    (replay is always free). 2 = >=80%% correct, 1 = >=50%%, else 0.
    At the standard 10-word session this is exactly 10/10, >=8, >=5."""
    if total <= 0:
        return 0
    if correct_count == total and not hints_used:
        return 3
    ratio = correct_count / total
    if ratio >= 0.8:
        return 2
    if ratio >= 0.5:
        return 1
    return 0


def stars_message(stars: int) -> str:
    return {
        3: "Perfect! You're Bee-Ready royalty!",
        2: "Fantastic flying! So close to perfect!",
        1: "Great effort! Keep practicing those tricky ones!",
        0: "Keep buzzing! Every bee gets better with practice!",
    }[stars]


def parse_list_text(text: str):
    """Parse custom-list textarea content. One entry per line:
        word | definition | sentence
    (definition and sentence optional). Returns (entries, errors, warnings).
    errors: [{line, text, message}] — any error blocks saving.
    warnings: [{line, text, message}] — duplicates collapsed, save allowed.
    """
    entries = []
    errors = []
    warnings = []
    seen = {}
    for lineno, raw in enumerate((text or "").splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) > 3:
            errors.append({"line": lineno, "text": raw,
                           "message": "Too many | separators (use: word | definition | sentence)"})
            continue
        word = parts[0].replace("\u2019", "'").replace("\u2018", "'")
        if not word:
            errors.append({"line": lineno, "text": raw, "message": "Missing word before the first |"})
            continue
        if len(word) > MAX_WORD_LEN:
            errors.append({"line": lineno, "text": raw,
                           "message": f"Word is too long (max {MAX_WORD_LEN} characters)"})
            continue
        if not WORD_RE.match(word):
            errors.append({"line": lineno, "text": raw,
                           "message": "Word may only use letters, hyphens, and apostrophes, "
                                      "and must start and end with a letter"})
            continue
        key = normalize_answer(word)
        if key in seen:
            warnings.append({"line": lineno, "text": raw,
                             "message": f'Duplicate word "{word}" (first used on line {seen[key]}) — removed'})
            continue
        seen[key] = lineno
        definition = parts[1] if len(parts) > 1 and parts[1] else None
        sentence = parts[2] if len(parts) > 2 and parts[2] else None
        entries.append({"word": word, "definition": definition, "sentence": sentence})
    return entries, errors, warnings


def atomic_write_json(path: str, data) -> None:
    """Write JSON atomically: temp file in the same directory, then os.replace."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_json(path: str, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as exc:
        logger.warning("Failed to read %s (%s); using default", path, exc)
        return default


def new_word_record(display_word: str):
    return {
        "word": display_word,
        "correct": 0,
        "incorrect": 0,
        "lastResult": None,
        "missedEver": False,
        "consecutiveCorrectSinceMiss": 0,
        "hints": 0,
        "definition": None,
        "sentence": None,
        "lastSeen": None,
    }


def is_word_mastered(rec) -> bool:
    """A previously-missed word is mastered once spelled correctly
    MASTERY_STREAK times in a row after the miss."""
    if not rec:
        return False
    return bool(rec.get("missedEver")) and rec.get("consecutiveCorrectSinceMiss", 0) >= MASTERY_STREAK


def is_entry_preferred(rec) -> bool:
    """Session selection prefers unmastered words: never answered correctly,
    or previously missed and not yet re-mastered."""
    if rec is None or rec.get("correct", 0) == 0:
        return True
    return bool(rec.get("missedEver")) and not is_word_mastered(rec)


def select_session_entries(pool: list, progress_words: dict, count: int = SESSION_SIZE,
                           rng: random.Random | None = None) -> list:
    """Pick up to `count` entries from pool: random, no repeats (deduped by
    normalized word), preferring unmastered words. Raises ApiError if the
    pool has fewer than MIN_SESSION_WORDS unique words."""
    rng = rng or random
    unique = {}
    for entry in pool:
        key = normalize_answer(entry["word"])
        if key and key not in unique:
            unique[key] = entry
    pool = list(unique.values())
    if len(pool) < MIN_SESSION_WORDS:
        raise ApiError(400, f"That list needs at least {MIN_SESSION_WORDS} words to practice "
                            f"(it has {len(pool)}).")
    count = min(count, len(pool))
    preferred, rest = [], []
    for entry in pool:
        rec = progress_words.get(normalize_answer(entry["word"]))
        (preferred if is_entry_preferred(rec) else rest).append(entry)
    picked = rng.sample(preferred, min(count, len(preferred)))
    if len(picked) < count:
        picked.extend(rng.sample(rest, count - len(picked)))
    rng.shuffle(picked)
    return picked


def b64_word(word: str) -> str:
    return base64.b64encode(word.encode("utf-8")).decode("ascii")


WORD_MASK = "_____"


def mask_word_in_text(text, word):
    """Replace occurrences of the target word inside its definition/sentence
    with a mask so the session payload never carries the word in cleartext
    (NFR-6). The client substitutes the decoded word back in for TTS only."""
    if not text:
        return text
    return re.sub(r"(?i)\b" + re.escape(word) + r"\b", WORD_MASK, text)


# ---------------------------------------------------------------------------
# Store + application
# ---------------------------------------------------------------------------

class ApiError(Exception):
    def __init__(self, status: int, message: str, extra=None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.extra = extra or {}


class Store:
    """JSON-flat-file persistence with atomic writes."""

    def __init__(self, data_dir: str):
        self.data_dir = os.path.abspath(data_dir)
        os.makedirs(self.data_dir, exist_ok=True)
        self.lock = threading.RLock()

    def _path(self, name: str) -> str:
        return os.path.join(self.data_dir, name)

    # profiles -------------------------------------------------------------
    def load_profiles(self) -> list:
        return load_json(self._path("profiles.json"), {"profiles": []}).get("profiles", [])

    def save_profiles(self, profiles: list) -> None:
        atomic_write_json(self._path("profiles.json"), {"profiles": profiles})

    # lists ----------------------------------------------------------------
    def load_lists(self) -> list:
        return load_json(self._path("lists.json"), {"lists": []}).get("lists", [])

    def save_lists(self, lists: list) -> None:
        atomic_write_json(self._path("lists.json"), {"lists": lists})

    # per-profile progress ---------------------------------------------------
    def _progress_path(self, profile_id: str) -> str:
        safe = re.sub(r"[^a-zA-Z0-9-]", "", profile_id)
        return self._path(f"progress-{safe}.json")

    def load_progress(self, profile_id: str) -> dict:
        return load_json(self._progress_path(profile_id), {
            "sessionsPlayed": 0, "totalStars": 0, "bestStreak": 0, "words": {},
        })

    def save_progress(self, profile_id: str, progress: dict) -> None:
        atomic_write_json(self._progress_path(profile_id), progress)

    def delete_progress(self, profile_id: str) -> None:
        try:
            os.unlink(self._progress_path(profile_id))
        except FileNotFoundError:
            pass


class ServerApp:
    def __init__(self, data_dir: str, corpus_path: str):
        self.store = Store(data_dir)
        self.corpus_path = corpus_path
        self.corpus_words: list = []
        self.sessions: dict = {}
        self.sessions_lock = threading.RLock()
        self.load_corpus()

    # corpus -----------------------------------------------------------------
    def load_corpus(self) -> None:
        if not os.path.exists(self.corpus_path):
            logger.warning("Corpus file %s not found — serving an empty corpus. "
                           "Tier practice will be unavailable until it is installed.",
                           self.corpus_path)
            self.corpus_words = []
            return
        data = load_json(self.corpus_path, {})
        words = []
        for item in data.get("words", []):
            word = (item.get("word") or "").strip()
            tier = item.get("tier")
            if not word or tier not in VALID_TIERS:
                continue
            words.append({
                "word": word,
                "tier": tier,
                "definition": item.get("definition") or None,
                "sentence": item.get("sentence") or None,
                "category": item.get("category") or None,
            })
        self.corpus_words = words
        logger.info("Loaded corpus: %d words from %s", len(words), self.corpus_path)

    def corpus_meta(self) -> dict:
        tiers = {str(t): 0 for t in VALID_TIERS}
        categories = set()
        for w in self.corpus_words:
            tiers[str(w["tier"])] += 1
            if w["category"]:
                categories.add(w["category"])
        return {
            "loaded": bool(self.corpus_words),
            "wordCount": len(self.corpus_words),
            "tiers": tiers,
            "tierNames": {str(k): v for k, v in TIER_NAMES.items()},
            "categories": sorted(categories),
        }

    # profiles -----------------------------------------------------------------
    def list_profiles(self) -> list:
        return self.store.load_profiles()

    def create_profile(self, data: dict) -> dict:
        name = (data.get("name") or "").strip()
        if not name:
            raise ApiError(400, "Please type a name.")
        if len(name) > MAX_NAME_LEN:
            raise ApiError(400, f"Name is too long (max {MAX_NAME_LEN} characters).")
        avatar = self._clean_avatar(data.get("avatar"))
        with self.store.lock:
            profiles = self.store.load_profiles()
            if any(p["name"].lower() == name.lower() for p in profiles):
                raise ApiError(409, f'There is already a bee named "{name}". Pick another name!')
            profile = {
                "id": uuid.uuid4().hex[:12],
                "name": name,
                "avatar": avatar,
                "createdAt": time.time(),
            }
            profiles.append(profile)
            self.store.save_profiles(profiles)
        return profile

    def update_profile(self, profile_id: str, data: dict) -> dict:
        with self.store.lock:
            profiles = self.store.load_profiles()
            profile = self._find(profiles, profile_id, "profile")
            if "name" in data:
                name = (data.get("name") or "").strip()
                if not name:
                    raise ApiError(400, "Please type a name.")
                if len(name) > MAX_NAME_LEN:
                    raise ApiError(400, f"Name is too long (max {MAX_NAME_LEN} characters).")
                if any(p["id"] != profile_id and p["name"].lower() == name.lower()
                       for p in profiles):
                    raise ApiError(409, f'There is already a bee named "{name}". Pick another name!')
                profile["name"] = name
            if "avatar" in data:
                profile["avatar"] = self._clean_avatar(data.get("avatar"))
            self.store.save_profiles(profiles)
        return profile

    def delete_profile(self, profile_id: str) -> dict:
        with self.store.lock:
            profiles = self.store.load_profiles()
            self._find(profiles, profile_id, "profile")
            profiles = [p for p in profiles if p["id"] != profile_id]
            self.store.save_profiles(profiles)
            self.store.delete_progress(profile_id)
        return {"ok": True}

    @staticmethod
    def _clean_avatar(avatar) -> dict:
        avatar = avatar if isinstance(avatar, dict) else {}
        color = str(avatar.get("color") or "#f6b73c")[:16]
        icon = str(avatar.get("icon") or "bee")[:24]
        return {"color": color, "icon": icon}

    @staticmethod
    def _find(items: list, item_id: str, kind: str) -> dict:
        for item in items:
            if item["id"] == item_id:
                return item
        raise ApiError(404, f"That {kind} was not found.")

    def get_progress(self, profile_id: str) -> dict:
        profiles = self.store.load_profiles()
        profile = self._find(profiles, profile_id, "profile")
        progress = self.store.load_progress(profile_id)
        tricky = []
        for key, rec in progress.get("words", {}).items():
            if rec.get("missedEver") and not is_word_mastered(rec):
                tricky.append({
                    "word": rec.get("word") or key,
                    "definition": rec.get("definition"),
                    "sentence": rec.get("sentence"),
                    "correct": rec.get("correct", 0),
                    "incorrect": rec.get("incorrect", 0),
                    "consecutiveCorrectSinceMiss": rec.get("consecutiveCorrectSinceMiss", 0),
                })
        tricky.sort(key=lambda r: r["word"])
        return {
            "profile": profile,
            "sessionsPlayed": progress.get("sessionsPlayed", 0),
            "totalStars": progress.get("totalStars", 0),
            "bestStreak": progress.get("bestStreak", 0),
            "trickyWords": tricky,
            "trickyCount": len(tricky),
        }

    # lists -----------------------------------------------------------------
    def get_lists(self) -> list:
        return self.store.load_lists()

    def validate_list_text(self, data: dict) -> dict:
        entries, errors, warnings = parse_list_text(data.get("text") or "")
        return {"entries": entries, "errors": errors, "warnings": warnings,
                "wordCount": len(entries)}

    def create_list(self, data: dict) -> dict:
        name, entries, warnings, raw_text = self._validate_list_payload(data)
        with self.store.lock:
            lists = self.store.load_lists()
            if any(x["name"].lower() == name.lower() for x in lists):
                raise ApiError(409, f'A list named "{name}" already exists. Pick another name!')
            new = {
                "id": uuid.uuid4().hex[:12],
                "name": name,
                "entries": entries,
                "rawText": raw_text,
                "createdAt": time.time(),
                "updatedAt": time.time(),
            }
            lists.append(new)
            self.store.save_lists(lists)
        return {"list": new, "warnings": warnings}

    def update_list(self, list_id: str, data: dict) -> dict:
        name, entries, warnings, raw_text = self._validate_list_payload(data)
        with self.store.lock:
            lists = self.store.load_lists()
            target = self._find(lists, list_id, "list")
            if any(x["id"] != list_id and x["name"].lower() == name.lower() for x in lists):
                raise ApiError(409, f'A list named "{name}" already exists. Pick another name!')
            target.update({"name": name, "entries": entries, "rawText": raw_text,
                           "updatedAt": time.time()})
            self.store.save_lists(lists)
        return {"list": target, "warnings": warnings}

    def delete_list(self, list_id: str) -> dict:
        with self.store.lock:
            lists = self.store.load_lists()
            self._find(lists, list_id, "list")
            lists = [x for x in lists if x["id"] != list_id]
            self.store.save_lists(lists)
        return {"ok": True}

    def _validate_list_payload(self, data: dict):
        name = (data.get("name") or "").strip()
        if not name:
            raise ApiError(400, "Please give the list a name.")
        if len(name) > MAX_LIST_NAME_LEN:
            raise ApiError(400, f"List name is too long (max {MAX_LIST_NAME_LEN} characters).")
        raw_text = data.get("text") or ""
        entries, errors, warnings = parse_list_text(raw_text)
        if errors:
            raise ApiError(400, "Some lines need fixing before this list can be saved.",
                           extra={"errors": errors, "warnings": warnings})
        if len(entries) < MIN_SESSION_WORDS:
            raise ApiError(400, f"A list needs at least {MIN_SESSION_WORDS} words "
                                f"(this one has {len(entries)}).",
                           extra={"warnings": warnings})
        return name, entries, warnings, raw_text

    # sessions -----------------------------------------------------------------
    def create_session(self, data: dict) -> dict:
        profile_id = data.get("profileId") or ""
        profiles = self.store.load_profiles()
        self._find(profiles, profile_id, "profile")
        source = data.get("source") or {}
        source_type = source.get("type")
        progress = self.store.load_progress(profile_id)
        progress_words = progress.get("words", {})

        if source_type == "tier":
            tier = source.get("tier")
            if tier not in VALID_TIERS:
                raise ApiError(400, "Unknown tier.")
            if not self.corpus_words:
                raise ApiError(409, "No word corpus is installed yet. "
                                    "Ask a grown-up to install corpus.json!")
            pool = [w for w in self.corpus_words if w["tier"] == tier]
            label = f"{TIER_NAMES[tier]} words"
        elif source_type == "list":
            lists = self.store.load_lists()
            lst = self._find(lists, source.get("listId") or "", "list")
            pool = lst["entries"]
            label = lst["name"]
        elif source_type == "tricky":
            pool = []
            for key, rec in progress_words.items():
                if rec.get("missedEver") and not is_word_mastered(rec):
                    pool.append({"word": rec.get("word") or key,
                                 "definition": rec.get("definition"),
                                 "sentence": rec.get("sentence")})
            if len(pool) < MIN_SESSION_WORDS:
                raise ApiError(400, f"Not enough tricky words yet (need {MIN_SESSION_WORDS}, "
                                    f"have {len(pool)}). Keep practicing — tricky words appear "
                                    "here after a miss!")
            label = "Tricky words"
        else:
            raise ApiError(400, "Unknown practice source.")

        entries = select_session_entries(pool, progress_words)
        session = {
            "id": uuid.uuid4().hex[:16],
            "profileId": profile_id,
            "source": source,
            "label": label,
            "entries": entries,
            "index": 0,
            "results": [],
            "streak": 0,
            "bestStreak": 0,
            "hintedWords": set(),
            "done": False,
            "summary": None,
            "createdAt": time.time(),
        }
        with self.sessions_lock:
            self._prune_sessions()
            self.sessions[session["id"]] = session
        payload_entries = [{
            # Base64-obfuscated word: the client needs it for TTS, but it never
            # appears in cleartext in the payload or the DOM pre-answer (NFR-6).
            # The word is also masked out of its own definition/sentence.
            "w": b64_word(e["word"]),
            "definition": mask_word_in_text(e.get("definition"), e["word"]),
            "sentence": mask_word_in_text(e.get("sentence"), e["word"]),
        } for e in entries]
        return {
            "sessionId": session["id"],
            "label": label,
            "count": len(entries),
            "entries": payload_entries,
        }

    def _prune_sessions(self) -> None:
        if len(self.sessions) < MAX_SESSIONS_KEPT:
            return
        by_age = sorted(self.sessions.values(), key=lambda s: s["createdAt"])
        for sess in by_age[: len(by_age) // 2]:
            del self.sessions[sess["id"]]

    def _get_session(self, session_id: str) -> dict:
        with self.sessions_lock:
            sess = self.sessions.get(session_id)
        if not sess:
            raise ApiError(404, "That practice session was not found (it may have expired). "
                                "Start a new one!")
        return sess

    def record_hint(self, session_id: str, data: dict) -> dict:
        sess = self._get_session(session_id)
        kind = data.get("kind")
        if kind not in ("definition", "sentence", "peek"):
            raise ApiError(400, "Unknown hint kind.")
        with self.sessions_lock:
            if sess["done"]:
                return {"ok": True, "counted": False}
            index = sess["index"]
            entry = sess["entries"][index]
            key = normalize_answer(entry["word"])
            # Peek while TTS is broken is a fallback, not a hint — no star cost.
            counted = kind in ("definition", "sentence") or (
                kind == "peek" and not data.get("ttsBroken"))
            if counted:
                sess["hintedWords"].add(key)
        if counted:
            with self.store.lock:
                progress = self.store.load_progress(sess["profileId"])
                rec = progress["words"].setdefault(key, new_word_record(entry["word"]))
                rec["hints"] = rec.get("hints", 0) + 1
                self.store.save_progress(sess["profileId"], progress)
        return {"ok": True, "counted": counted}

    def answer(self, session_id: str, data: dict) -> dict:
        sess = self._get_session(session_id)
        attempt = data.get("attempt") or ""
        with self.sessions_lock:
            if sess["done"]:
                raise ApiError(409, "This session is already finished.")
            index = sess["index"]
            entry = sess["entries"][index]
            correct_word = entry["word"]
            is_correct = grade_attempt(attempt, correct_word)
            diff = None if is_correct else letter_diff(attempt, correct_word)
            if is_correct:
                sess["streak"] += 1
                sess["bestStreak"] = max(sess["bestStreak"], sess["streak"])
            else:
                sess["streak"] = 0  # silent break — no shaming
            sess["results"].append({
                "word": correct_word,
                "attempt": attempt.strip(),
                "correct": is_correct,
                "definition": entry.get("definition"),
                "sentence": entry.get("sentence"),
            })
            sess["index"] += 1
            done = sess["index"] >= len(sess["entries"])
            sess["done"] = done
            streak = sess["streak"]

        self._record_word_result(sess["profileId"], entry, is_correct)

        summary = None
        if done:
            summary = self._finalize_session(sess)
        return {
            "correct": is_correct,
            "correctWord": correct_word,
            "attempt": attempt.strip(),
            "diff": diff,
            "streak": streak,
            "index": sess["index"],
            "total": len(sess["entries"]),
            "done": done,
            "summary": summary,
        }

    def _record_word_result(self, profile_id: str, entry: dict, is_correct: bool) -> None:
        key = normalize_answer(entry["word"])
        with self.store.lock:
            progress = self.store.load_progress(profile_id)
            rec = progress["words"].setdefault(key, new_word_record(entry["word"]))
            rec["word"] = entry["word"]
            if entry.get("definition"):
                rec["definition"] = entry["definition"]
            if entry.get("sentence"):
                rec["sentence"] = entry["sentence"]
            rec["lastSeen"] = time.time()
            if is_correct:
                rec["correct"] = rec.get("correct", 0) + 1
                rec["lastResult"] = "correct"
                if rec.get("missedEver"):
                    rec["consecutiveCorrectSinceMiss"] = \
                        rec.get("consecutiveCorrectSinceMiss", 0) + 1
            else:
                rec["incorrect"] = rec.get("incorrect", 0) + 1
                rec["lastResult"] = "incorrect"
                rec["missedEver"] = True
                rec["consecutiveCorrectSinceMiss"] = 0
            self.store.save_progress(profile_id, progress)

    def _finalize_session(self, sess: dict) -> dict:
        correct_count = sum(1 for r in sess["results"] if r["correct"])
        total = len(sess["entries"])
        hints_used = bool(sess["hintedWords"])
        stars = compute_stars(correct_count, total, hints_used)
        missed = [{"word": r["word"], "attempt": r["attempt"],
                   "definition": r.get("definition")}
                  for r in sess["results"] if not r["correct"]]
        with self.store.lock:
            progress = self.store.load_progress(sess["profileId"])
            progress["sessionsPlayed"] = progress.get("sessionsPlayed", 0) + 1
            progress["totalStars"] = progress.get("totalStars", 0) + stars
            progress["bestStreak"] = max(progress.get("bestStreak", 0), sess["bestStreak"])
            self.store.save_progress(sess["profileId"], progress)
        summary = {
            "correctCount": correct_count,
            "total": total,
            "bestStreak": sess["bestStreak"],
            "stars": stars,
            "starsMessage": stars_message(stars),
            "hintsUsed": hints_used,
            "missed": missed,
            "label": sess["label"],
            "source": sess["source"],
        }
        sess["summary"] = summary
        return summary


# ---------------------------------------------------------------------------
# HTTP layer
# ---------------------------------------------------------------------------

APP: ServerApp = None  # set in main()

ROUTES = []


def route(method: str, pattern: str):
    compiled = re.compile("^" + pattern + "$")

    def decorator(fn):
        ROUTES.append((method, compiled, fn))
        return fn
    return decorator


@route("GET", r"/api/corpus/meta")
def r_corpus_meta(match, body):
    return APP.corpus_meta()


@route("GET", r"/api/profiles")
def r_profiles_get(match, body):
    return {"profiles": APP.list_profiles()}


@route("POST", r"/api/profiles")
def r_profiles_post(match, body):
    return APP.create_profile(body)


@route("PATCH", r"/api/profiles/([a-zA-Z0-9-]+)")
def r_profiles_patch(match, body):
    return APP.update_profile(match.group(1), body)


@route("DELETE", r"/api/profiles/([a-zA-Z0-9-]+)")
def r_profiles_delete(match, body):
    return APP.delete_profile(match.group(1))


@route("GET", r"/api/profiles/([a-zA-Z0-9-]+)/progress")
def r_progress_get(match, body):
    return APP.get_progress(match.group(1))


@route("GET", r"/api/lists")
def r_lists_get(match, body):
    return {"lists": APP.get_lists()}


@route("POST", r"/api/lists/validate")
def r_lists_validate(match, body):
    return APP.validate_list_text(body)


@route("POST", r"/api/lists")
def r_lists_post(match, body):
    return APP.create_list(body)


@route("PUT", r"/api/lists/([a-zA-Z0-9-]+)")
def r_lists_put(match, body):
    return APP.update_list(match.group(1), body)


@route("DELETE", r"/api/lists/([a-zA-Z0-9-]+)")
def r_lists_delete(match, body):
    return APP.delete_list(match.group(1))


@route("POST", r"/api/session")
def r_session_post(match, body):
    return APP.create_session(body)


@route("POST", r"/api/session/([a-zA-Z0-9-]+)/answer")
def r_session_answer(match, body):
    return APP.answer(match.group(1), body)


@route("POST", r"/api/session/([a-zA-Z0-9-]+)/hint")
def r_session_hint(match, body):
    return APP.record_hint(match.group(1), body)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "MeshBee/1.0"

    def log_message(self, fmt, *args):
        logger.info("%s %s", self.address_string(), fmt % args)

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_PATCH(self):
        self._dispatch("PATCH")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def _dispatch(self, method: str):
        path = unquote(urlparse(self.path).path)
        if path.startswith("/api/"):
            self._dispatch_api(method, path)
        elif method == "GET":
            self._serve_static(path)
        else:
            self._send_json(405, {"error": "Method not allowed"})

    def _dispatch_api(self, method: str, path: str):
        for route_method, pattern, fn in ROUTES:
            if route_method != method:
                continue
            match = pattern.match(path)
            if not match:
                continue
            try:
                body = self._read_body() if method in ("POST", "PUT", "PATCH") else None
                result = fn(match, body)
                self._send_json(200, result)
            except ApiError as exc:
                payload = {"error": exc.message}
                payload.update(exc.extra)
                self._send_json(exc.status, payload)
            except Exception:
                logger.exception("Unhandled error on %s %s", method, path)
                self._send_json(500, {"error": "Something went wrong on the server."})
            return
        self._send_json(404, {"error": "Not found"})

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > 1_000_000:
            raise ApiError(413, "Request too large.")
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise ApiError(400, "Invalid JSON body.")
        if not isinstance(data, dict):
            raise ApiError(400, "JSON body must be an object.")
        return data

    def _send_json(self, status: int, payload) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _serve_static(self, path: str) -> None:
        if path in ("", "/"):
            path = "/index.html"
        candidate = os.path.realpath(os.path.join(STATIC_DIR, path.lstrip("/")))
        if not candidate.startswith(os.path.realpath(STATIC_DIR) + os.sep) \
                and candidate != os.path.realpath(STATIC_DIR):
            self._send_json(403, {"error": "Forbidden"})
            return
        if not os.path.isfile(candidate):
            # SPA: unknown non-file paths fall back to index.html
            candidate = os.path.join(STATIC_DIR, "index.html")
            if not os.path.isfile(candidate):
                self._send_json(404, {"error": "Not found"})
                return
        ext = os.path.splitext(candidate)[1].lower()
        ctype = CONTENT_TYPES.get(ext, "application/octet-stream")
        with open(candidate, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)


def main(argv=None) -> int:
    global APP
    parser = argparse.ArgumentParser(description="MeshBee spelling practice server")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", default="./data")
    parser.add_argument("--corpus", default="./corpus.json")
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    APP = ServerApp(data_dir=args.data_dir, corpus_path=args.corpus)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    logger.info("MeshBee buzzing at http://%s:%d  (data: %s)",
                args.host, args.port, os.path.abspath(args.data_dir))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down. Bye, bee!")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
