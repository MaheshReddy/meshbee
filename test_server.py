#!/usr/bin/env python3
"""Unit tests for the MeshBee server. Run: python3 -m unittest test_server.py -v"""
from __future__ import annotations

import json
import os
import random
import shutil
import tempfile
import unittest

import meshbee.server as server
from meshbee.server import (
    ApiError,
    ServerApp,
    atomic_write_json,
    compute_stars,
    grade_attempt,
    is_word_mastered,
    letter_diff,
    mask_word_in_text,
    new_word_record,
    normalize_answer,
    parse_list_text,
    select_session_entries,
)


class TestWordMask(unittest.TestCase):
    def test_word_masked_in_sentence(self):
        out = mask_word_in_text("It is necessary to wear a helmet.", "necessary")
        self.assertNotIn("necessary", out)
        self.assertIn("_____", out)

    def test_case_insensitive_mask(self):
        out = mask_word_in_text("Necessary things are necessary.", "necessary")
        self.assertNotIn("necessary", out.lower())

    def test_word_boundary_respected(self):
        # "there" must not be masked inside "therefore"
        out = mask_word_in_text("Therefore, go there.", "there")
        self.assertIn("Therefore", out)
        self.assertNotIn("go there", out)

    def test_none_passthrough(self):
        self.assertIsNone(mask_word_in_text(None, "cat"))


class TestGrading(unittest.TestCase):
    def test_exact_match(self):
        self.assertTrue(grade_attempt("necessary", "necessary"))

    def test_case_insensitive(self):
        self.assertTrue(grade_attempt("NeCeSSary", "necessary"))
        self.assertTrue(grade_attempt("ISLAND", "island"))

    def test_whitespace_trimmed(self):
        self.assertTrue(grade_attempt("  rhythm  ", "rhythm"))
        self.assertTrue(grade_attempt("\trhythm\n", "rhythm"))

    def test_internal_whitespace_collapsed(self):
        self.assertTrue(grade_attempt("ice  cream", "ice cream"))

    def test_wrong_answer(self):
        self.assertFalse(grade_attempt("neccessary", "necessary"))
        self.assertFalse(grade_attempt("", "necessary"))

    def test_hyphens_must_match(self):
        self.assertTrue(grade_attempt("well-known", "well-known"))
        self.assertFalse(grade_attempt("wellknown", "well-known"))
        self.assertFalse(grade_attempt("well known", "well-known"))

    def test_apostrophes_must_match(self):
        self.assertTrue(grade_attempt("don't", "don't"))
        self.assertFalse(grade_attempt("dont", "don't"))

    def test_curly_apostrophe_normalized(self):
        self.assertTrue(grade_attempt("don\u2019t", "don't"))

    def test_normalize(self):
        self.assertEqual(normalize_answer("  Hello  World "), "hello world")


class TestLetterDiff(unittest.TestCase):
    def _ops(self, attempt, correct):
        return [(s["op"], s["attempt"], s["correct"]) for s in letter_diff(attempt, correct)]

    def test_perfect(self):
        self.assertEqual(self._ops("cat", "cat"), [("match", "cat", "cat")])

    def test_missing_letter(self):
        ops = self._ops("neccessary", "necessary")
        # extra 'c' somewhere
        self.assertIn("extra", [o[0] for o in ops])
        rebuilt_attempt = "".join(o[1] for o in ops)
        rebuilt_correct = "".join(o[2] for o in ops)
        self.assertEqual(rebuilt_attempt, "neccessary")
        self.assertEqual(rebuilt_correct, "necessary")

    def test_missing(self):
        ops = self._ops("rythm", "rhythm")
        self.assertIn("missing", [o[0] for o in ops])

    def test_substitution(self):
        ops = self._ops("cot", "cat")
        kinds = [o[0] for o in ops]
        self.assertIn("wrong", kinds)

    def test_case_normalized_before_diff(self):
        self.assertEqual(self._ops("CAT", "cat"), [("match", "cat", "cat")])


class TestStars(unittest.TestCase):
    def test_three_stars_perfect_no_hints(self):
        self.assertEqual(compute_stars(10, 10, hints_used=False), 3)

    def test_perfect_with_hints_is_two(self):
        self.assertEqual(compute_stars(10, 10, hints_used=True), 2)

    def test_two_stars_at_eight(self):
        self.assertEqual(compute_stars(8, 10, hints_used=False), 2)
        self.assertEqual(compute_stars(9, 10, hints_used=True), 2)

    def test_one_star_at_five(self):
        self.assertEqual(compute_stars(5, 10, hints_used=False), 1)
        self.assertEqual(compute_stars(7, 10, hints_used=False), 1)

    def test_zero_stars(self):
        self.assertEqual(compute_stars(4, 10, hints_used=False), 0)
        self.assertEqual(compute_stars(0, 10, hints_used=False), 0)

    def test_short_session_scales(self):
        self.assertEqual(compute_stars(5, 5, hints_used=False), 3)  # all correct
        self.assertEqual(compute_stars(4, 5, hints_used=False), 2)  # 80%
        self.assertEqual(compute_stars(3, 5, hints_used=False), 1)  # 60%


class TestListParser(unittest.TestCase):
    def test_full_lines(self):
        entries, errors, warnings = parse_list_text(
            "necessary | needed | It is necessary.\nrhythm")
        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])
        self.assertEqual(entries[0], {"word": "necessary", "definition": "needed",
                                      "sentence": "It is necessary."})
        self.assertEqual(entries[1], {"word": "rhythm", "definition": None, "sentence": None})

    def test_word_only_and_word_def(self):
        entries, errors, _ = parse_list_text("banana | a yellow fruit")
        self.assertEqual(errors, [])
        self.assertEqual(entries[0]["definition"], "a yellow fruit")
        self.assertIsNone(entries[0]["sentence"])

    def test_blank_lines_skipped(self):
        entries, errors, _ = parse_list_text("\n\ncat\n\n\ndog\n")
        self.assertEqual(errors, [])
        self.assertEqual(len(entries), 2)

    def test_error_too_many_pipes(self):
        entries, errors, _ = parse_list_text("a | b | c | d")
        self.assertEqual(entries, [])
        self.assertEqual(errors[0]["line"], 1)
        self.assertIn("Too many", errors[0]["message"])

    def test_error_missing_word(self):
        _, errors, _ = parse_list_text("| definition only")
        self.assertEqual(len(errors), 1)
        self.assertIn("Missing word", errors[0]["message"])

    def test_error_bad_characters(self):
        _, errors, _ = parse_list_text("c4t\nhello!\n-dash")
        self.assertEqual(len(errors), 3)
        self.assertEqual([e["line"] for e in errors], [1, 2, 3])

    def test_valid_hyphen_apostrophe_words(self):
        entries, errors, _ = parse_list_text("well-known\ndon't\nice cream")
        self.assertEqual(errors, [])
        self.assertEqual(len(entries), 3)

    def test_duplicates_collapsed_with_warning(self):
        entries, errors, warnings = parse_list_text("cat\ndog\nCAT | a feline")
        self.assertEqual(errors, [])
        self.assertEqual(len(entries), 2)
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["line"], 3)
        self.assertIn("Duplicate", warnings[0]["message"])

    def test_error_lines_reported_with_line_numbers(self):
        _, errors, _ = parse_list_text("good\n123\nalso-good\n!!!")
        self.assertEqual([e["line"] for e in errors], [2, 4])


class TestAtomicWrite(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def test_writes_and_replaces(self):
        path = os.path.join(self.dir, "x.json")
        atomic_write_json(path, {"a": 1})
        atomic_write_json(path, {"a": 2})
        with open(path) as f:
            self.assertEqual(json.load(f), {"a": 2})

    def test_no_temp_files_left_behind(self):
        path = os.path.join(self.dir, "x.json")
        atomic_write_json(path, {"a": 1})
        leftovers = [f for f in os.listdir(self.dir) if f.startswith(".tmp-")]
        self.assertEqual(leftovers, [])

    def test_unserializable_leaves_original_intact(self):
        path = os.path.join(self.dir, "x.json")
        atomic_write_json(path, {"a": 1})
        with self.assertRaises(TypeError):
            atomic_write_json(path, {"bad": object()})
        with open(path) as f:
            self.assertEqual(json.load(f), {"a": 1})
        leftovers = [f for f in os.listdir(self.dir) if f.startswith(".tmp-")]
        self.assertEqual(leftovers, [])


def make_pool(n, prefix="word"):
    return [{"word": f"{prefix}{i}", "definition": None, "sentence": None} for i in range(n)]


class TestSessionSelection(unittest.TestCase):
    def test_exactly_ten_from_large_pool(self):
        picked = select_session_entries(make_pool(50), {})
        self.assertEqual(len(picked), 10)

    def test_no_repeats(self):
        picked = select_session_entries(make_pool(50), {})
        words = [normalize_answer(e["word"]) for e in picked]
        self.assertEqual(len(words), len(set(words)))

    def test_small_pool_uses_all(self):
        picked = select_session_entries(make_pool(5), {})
        self.assertEqual(len(picked), 5)

    def test_under_three_blocked(self):
        with self.assertRaises(ApiError) as ctx:
            select_session_entries(make_pool(2), {})
        self.assertEqual(ctx.exception.status, 400)

    def test_pool_duplicates_removed(self):
        pool = make_pool(4) + make_pool(4)  # duplicated words
        picked = select_session_entries(pool, {})
        self.assertEqual(len(picked), 4)

    def test_prefers_unmastered(self):
        pool = make_pool(20)
        progress = {}
        # words 0..9 fully mastered-looking: correct many times, never missed
        for i in range(10):
            rec = new_word_record(f"word{i}")
            rec["correct"] = 5
            progress[f"word{i}"] = rec
        rng = random.Random(42)
        picked = select_session_entries(pool, progress, rng=rng)
        picked_words = {normalize_answer(e["word"]) for e in picked}
        # all 10 unseen words (word10..word19) must be chosen first
        expected = {f"word{i}" for i in range(10, 20)}
        self.assertEqual(picked_words, expected)

    def test_missed_words_count_as_unmastered(self):
        pool = make_pool(20)
        progress = {}
        for i in range(20):
            rec = new_word_record(f"word{i}")
            rec["correct"] = 5
            if i < 10:
                # these were missed and not yet re-mastered
                rec["missedEver"] = True
                rec["consecutiveCorrectSinceMiss"] = 1
            progress[f"word{i}"] = rec
        picked = select_session_entries(pool, progress, rng=random.Random(7))
        picked_words = {normalize_answer(e["word"]) for e in picked}
        self.assertEqual(picked_words, {f"word{i}" for i in range(10)})


class TestMastery(unittest.TestCase):
    def test_never_missed_is_not_mastered_tricky(self):
        rec = new_word_record("cat")
        rec["correct"] = 3
        self.assertFalse(is_word_mastered(rec))  # mastery only applies post-miss

    def test_mastered_after_two_consecutive(self):
        rec = new_word_record("cat")
        rec["missedEver"] = True
        rec["consecutiveCorrectSinceMiss"] = 2
        self.assertTrue(is_word_mastered(rec))

    def test_not_mastered_at_one(self):
        rec = new_word_record("cat")
        rec["missedEver"] = True
        rec["consecutiveCorrectSinceMiss"] = 1
        self.assertFalse(is_word_mastered(rec))


class AppTestBase(unittest.TestCase):
    """End-to-end tests at the application layer (no HTTP socket needed)."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        corpus_path = os.path.join(self.dir, "corpus.json")
        words = []
        for tier in (1, 2, 3):
            for i in range(12):
                words.append({"word": f"t{tier}word{i}", "tier": tier,
                              "definition": f"def {i}", "sentence": f"Sentence {i}."})
        with open(corpus_path, "w") as f:
            json.dump({"words": words}, f)
        self.app = ServerApp(data_dir=os.path.join(self.dir, "data"), corpus_path=corpus_path)
        self.profile = self.app.create_profile({"name": "Zoe", "avatar": {"color": "#abc", "icon": "bee"}})

    def _play_session(self, source, wrong_words=(), hint_words=()):
        """Create and fully play a session; wrong_words get a wrong attempt."""
        sess = self.app.create_session({"profileId": self.profile["id"], "source": source})
        import base64
        last = None
        for entry in sess["entries"]:
            word = base64.b64decode(entry["w"]).decode()
            if word in hint_words:
                self.app.record_hint(sess["sessionId"], {"kind": "definition"})
            attempt = "zzz" if word in wrong_words else word
            last = self.app.answer(sess["sessionId"], {"attempt": attempt})
        return sess, last


class TestAppFlow(AppTestBase):
    def test_profile_crud(self):
        p2 = self.app.create_profile({"name": "Max"})
        with self.assertRaises(ApiError):  # duplicate name
            self.app.create_profile({"name": "max"})
        self.app.update_profile(p2["id"], {"name": "Maxine"})
        names = [p["name"] for p in self.app.list_profiles()]
        self.assertIn("Maxine", names)
        self.app.delete_profile(p2["id"])
        self.assertNotIn("Maxine", [p["name"] for p in self.app.list_profiles()])

    def test_corpus_meta(self):
        meta = self.app.corpus_meta()
        self.assertTrue(meta["loaded"])
        self.assertEqual(meta["tiers"]["1"], 12)

    def test_missing_corpus_graceful(self):
        app = ServerApp(data_dir=os.path.join(self.dir, "data2"),
                        corpus_path=os.path.join(self.dir, "nope.json"))
        self.assertFalse(app.corpus_meta()["loaded"])
        p = app.create_profile({"name": "A"})
        with self.assertRaises(ApiError) as ctx:
            app.create_session({"profileId": p["id"], "source": {"type": "tier", "tier": 1}})
        self.assertEqual(ctx.exception.status, 409)

    def test_session_word_never_in_payload_cleartext(self):
        sess = self.app.create_session({"profileId": self.profile["id"],
                                        "source": {"type": "tier", "tier": 1}})
        payload = json.dumps(sess)
        self.assertNotIn("t1word", payload)  # only base64 forms present

    def test_perfect_session_three_stars(self):
        _, last = self._play_session({"type": "tier", "tier": 1})
        self.assertTrue(last["done"])
        summary = last["summary"]
        self.assertEqual(summary["correctCount"], 10)
        self.assertEqual(summary["stars"], 3)
        progress = self.app.get_progress(self.profile["id"])
        self.assertEqual(progress["sessionsPlayed"], 1)
        self.assertEqual(progress["totalStars"], 3)
        self.assertEqual(progress["bestStreak"], 10)

    def test_hint_costs_third_star(self):
        import base64
        sess = self.app.create_session({"profileId": self.profile["id"],
                                        "source": {"type": "tier", "tier": 1}})
        self.app.record_hint(sess["sessionId"], {"kind": "definition"})
        last = None
        for entry in sess["entries"]:
            word = base64.b64decode(entry["w"]).decode()
            last = self.app.answer(sess["sessionId"], {"attempt": word})
        self.assertEqual(last["summary"]["stars"], 2)
        self.assertTrue(last["summary"]["hintsUsed"])

    def test_peek_with_tts_broken_is_free(self):
        sess = self.app.create_session({"profileId": self.profile["id"],
                                        "source": {"type": "tier", "tier": 1}})
        res = self.app.record_hint(sess["sessionId"], {"kind": "peek", "ttsBroken": True})
        self.assertFalse(res["counted"])
        import base64
        last = None
        for entry in sess["entries"]:
            word = base64.b64decode(entry["w"]).decode()
            last = self.app.answer(sess["sessionId"], {"attempt": word})
        self.assertEqual(last["summary"]["stars"], 3)

    def test_wrong_answers_recorded_and_tricky_flow(self):
        # Miss two specific words
        sess = self.app.create_session({"profileId": self.profile["id"],
                                        "source": {"type": "tier", "tier": 2}})
        import base64
        words = [base64.b64decode(e["w"]).decode() for e in sess["entries"]]
        missed = set(words[:3])
        last = None
        for word in words:
            attempt = word + "x" if word in missed else word
            last = self.app.answer(sess["sessionId"], {"attempt": attempt})
        summary = last["summary"]
        self.assertEqual(summary["correctCount"], 7)
        self.assertEqual(summary["stars"], 1)
        self.assertEqual({m["word"] for m in summary["missed"]}, missed)

        progress = self.app.get_progress(self.profile["id"])
        tricky_words = {t["word"] for t in progress["trickyWords"]}
        self.assertEqual(tricky_words, missed)

        # Tricky session now available
        tricky_sess = self.app.create_session({"profileId": self.profile["id"],
                                               "source": {"type": "tricky"}})
        tricky_in_sess = {base64.b64decode(e["w"]).decode() for e in tricky_sess["entries"]}
        self.assertEqual(tricky_in_sess, missed)

    def test_mastery_removes_from_tricky_after_two_consecutive(self):
        import base64
        sess = self.app.create_session({"profileId": self.profile["id"],
                                        "source": {"type": "tier", "tier": 3}})
        words = [base64.b64decode(e["w"]).decode() for e in sess["entries"]]
        target = words[0]
        for word in words:
            self.app.answer(sess["sessionId"], {"attempt": word + ("x" if word == target else "")})
        self.assertIn(target, {t["word"] for t in
                               self.app.get_progress(self.profile["id"])["trickyWords"]})
        # Answer it correctly twice via tricky sessions (pool is 1 word < 3, so
        # directly exercise _record_word_result like a tier session would)
        entry = {"word": target, "definition": None, "sentence": None}
        self.app._record_word_result(self.profile["id"], entry, True)
        self.assertIn(target, {t["word"] for t in
                               self.app.get_progress(self.profile["id"])["trickyWords"]})
        self.app._record_word_result(self.profile["id"], entry, True)
        self.assertNotIn(target, {t["word"] for t in
                                  self.app.get_progress(self.profile["id"])["trickyWords"]})

    def test_wrong_answer_response_contains_diff_and_word(self):
        import base64
        sess = self.app.create_session({"profileId": self.profile["id"],
                                        "source": {"type": "tier", "tier": 1}})
        word = base64.b64decode(sess["entries"][0]["w"]).decode()
        res = self.app.answer(sess["sessionId"], {"attempt": word + "zz"})
        self.assertFalse(res["correct"])
        self.assertEqual(res["correctWord"], word)
        self.assertTrue(res["diff"])
        self.assertEqual(res["streak"], 0)

    def test_streak_tracking(self):
        import base64
        sess = self.app.create_session({"profileId": self.profile["id"],
                                        "source": {"type": "tier", "tier": 1}})
        words = [base64.b64decode(e["w"]).decode() for e in sess["entries"]]
        res = None
        for i, word in enumerate(words):
            attempt = word if i != 4 else "nope"
            res = self.app.answer(sess["sessionId"], {"attempt": attempt})
        # 4 correct, miss, then 5 correct -> best streak 5, final streak 5
        self.assertEqual(res["summary"]["bestStreak"], 5)

    def test_progress_persisted_across_app_restart(self):
        self._play_session({"type": "tier", "tier": 1})
        app2 = ServerApp(data_dir=self.app.store.data_dir, corpus_path=self.app.corpus_path)
        progress = app2.get_progress(self.profile["id"])
        self.assertEqual(progress["sessionsPlayed"], 1)

    def test_answer_on_finished_session_rejected(self):
        sess, last = self._play_session({"type": "tier", "tier": 1})
        self.assertTrue(last["done"])
        with self.assertRaises(ApiError) as ctx:
            self.app.answer(sess["sessionId"], {"attempt": "x"})
        self.assertEqual(ctx.exception.status, 409)


class TestLists(AppTestBase):
    def test_create_and_session_from_list(self):
        result = self.app.create_list({"name": "My words",
                                       "text": "alpha | first letter\nbravo\ncharlie | third | Charlie waved."})
        self.assertEqual(len(result["list"]["entries"]), 3)
        sess = self.app.create_session({"profileId": self.profile["id"],
                                        "source": {"type": "list", "listId": result["list"]["id"]}})
        self.assertEqual(sess["count"], 3)  # all words when 3-9 (FR-10)

    def test_duplicate_list_name_rejected(self):
        self.app.create_list({"name": "Animals", "text": "cat\ndog\nfox"})
        with self.assertRaises(ApiError) as ctx:
            self.app.create_list({"name": "animals", "text": "ant\nbee\nelk"})
        self.assertEqual(ctx.exception.status, 409)

    def test_invalid_lines_block_save(self):
        with self.assertRaises(ApiError) as ctx:
            self.app.create_list({"name": "Bad", "text": "ok\n123bad\nfine"})
        self.assertEqual(ctx.exception.status, 400)
        self.assertTrue(ctx.exception.extra["errors"])

    def test_too_few_words_blocked(self):
        with self.assertRaises(ApiError):
            self.app.create_list({"name": "Tiny", "text": "one\ntwo"})

    def test_duplicate_words_collapsed_with_warning(self):
        result = self.app.create_list({"name": "Dups", "text": "cat\ndog\ncat\nfox"})
        self.assertEqual(len(result["list"]["entries"]), 3)
        self.assertEqual(len(result["warnings"]), 1)

    def test_update_and_delete(self):
        made = self.app.create_list({"name": "Edit me", "text": "cat\ndog\nfox"})
        updated = self.app.update_list(made["list"]["id"],
                                       {"name": "Edited", "text": "cat\ndog\nfox\nowl"})
        self.assertEqual(updated["list"]["name"], "Edited")
        self.assertEqual(len(updated["list"]["entries"]), 4)
        self.assertEqual(updated["list"]["rawText"], "cat\ndog\nfox\nowl")
        self.app.delete_list(made["list"]["id"])
        self.assertEqual(self.app.get_lists(), [])

    def test_lists_shared_across_profiles(self):
        self.app.create_list({"name": "Shared", "text": "cat\ndog\nfox"})
        p2 = self.app.create_profile({"name": "Другой"})
        lists = self.app.get_lists()
        sess = self.app.create_session({"profileId": p2["id"],
                                        "source": {"type": "list", "listId": lists[0]["id"]}})
        self.assertEqual(sess["count"], 3)


if __name__ == "__main__":
    unittest.main()
