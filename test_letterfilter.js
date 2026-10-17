#!/usr/bin/env node
/* Headless tests for the voice-spelling letter filter.
 * Run: node test_letterfilter.js */
"use strict";
const assert = require("assert");
const { parseTranscript, LETTER_MAP, COMMAND_MAP } = require("./meshbee/static/letters.js");

function letters(text) {
  return parseTranscript(text).ops
    .filter((o) => o.type === "letter")
    .map((o) => o.ch)
    .join("");
}
function commands(text) {
  return parseTranscript(text).ops
    .filter((o) => o.type === "command")
    .map((o) => o.cmd);
}

let n = 0;
function check(label, fn) {
  fn();
  n += 1;
  console.log("PASS  " + label);
}

check("single letters", () => {
  assert.strictEqual(letters("a"), "a");
  assert.strictEqual(letters("Q"), "q");
  assert.strictEqual(letters("z"), "z");
});

check("homophones: bee->b, why->y, are->r", () => {
  assert.strictEqual(letters("bee"), "b");
  assert.strictEqual(letters("why"), "y");
  assert.strictEqual(letters("are"), "r");
  assert.strictEqual(letters("sea"), "c");
  assert.strictEqual(letters("eye"), "i");
  assert.strictEqual(letters("cue"), "q");
  assert.strictEqual(letters("zed"), "z");
  assert.strictEqual(letters("tea"), "t");
});

check("'a b c' splits into three letters", () => {
  assert.strictEqual(letters("a b c"), "abc");
});

check("glued token 'abc' splits per char", () => {
  assert.strictEqual(letters("abc"), "abc");
});

check("recognizer gluing a spelled word: 'cat' -> c,a,t", () => {
  assert.strictEqual(letters("cat"), "cat");
});

check("double u -> w (bigram beats splitting)", () => {
  assert.strictEqual(letters("double u"), "w");
  assert.strictEqual(letters("double you"), "w");
});

check("hyphen / dash / apostrophe", () => {
  assert.strictEqual(letters("hyphen"), "-");
  assert.strictEqual(letters("dash"), "-");
  assert.strictEqual(letters("apostrophe"), "'");
});

check("command words", () => {
  assert.deepStrictEqual(commands("submit"), ["submit"]);
  assert.deepStrictEqual(commands("done"), ["submit"]);
  assert.deepStrictEqual(commands("enter"), ["submit"]);
  assert.deepStrictEqual(commands("undo"), ["backspace"]);
  assert.deepStrictEqual(commands("backspace"), ["backspace"]);
  assert.deepStrictEqual(commands("delete"), ["backspace"]);
  assert.deepStrictEqual(commands("clear"), ["clear"]);
  assert.deepStrictEqual(commands("start over"), ["clear"]);
  assert.deepStrictEqual(commands("repeat"), ["replay"]);
  assert.deepStrictEqual(commands("again"), ["replay"]);
});

check("letters and commands interleave in order", () => {
  const ops = parseTranscript("b e e submit").ops;
  assert.deepStrictEqual(ops, [
    { type: "letter", ch: "b" },
    { type: "letter", ch: "e" },
    { type: "letter", ch: "e" },
    { type: "command", cmd: "submit" },
  ]);
});

check("unmappable tokens rejected silently", () => {
  const r = parseTranscript("hello123 ?! 42");
  assert.strictEqual(r.ops.length, 0);
  assert.deepStrictEqual(r.unmatched, ["hello123", "42"]);
});

check("over-long all-letter token not split", () => {
  const r = parseTranscript("supercalifragilistic");
  assert.strictEqual(r.ops.length, 0);
  assert.strictEqual(r.unmatched.length, 1);
});

check("punctuation and case normalized", () => {
  assert.strictEqual(letters("Bee, Ee! Ee?"), "bee");
});

check("empty / blank input", () => {
  assert.deepStrictEqual(parseTranscript("").ops, []);
  assert.deepStrictEqual(parseTranscript("   ").ops, []);
});

check("every letter a-z reachable", () => {
  const reachable = new Set(Object.values(LETTER_MAP));
  for (const ch of "abcdefghijklmnopqrstuvwxyz") assert.ok(reachable.has(ch), ch);
});

check("no collision between command and letter tokens", () => {
  for (const tok of Object.keys(COMMAND_MAP)) assert.ok(!LETTER_MAP[tok], tok);
});

console.log(`\nALL ${n} LETTER-FILTER TESTS PASSED`);
