/* MeshBee — voice-spelling letter filter.
 *
 * Pure token->letter/command mapping for the Web Speech Recognition input
 * path. No DOM, no state: loadable in the browser (global MeshBeeLetters)
 * and in node (module.exports) so it can be unit-tested headlessly.
 */
(function (global) {
  "use strict";

  /* Homophone map: what recognizers typically transcribe when a kid says a
   * letter name. Keys are lowercase normalized tokens. */
  const LETTER_MAP = {
    a: "a", ay: "a", hey: "a",
    b: "b", be: "b", bee: "b",
    c: "c", sea: "c", see: "c",
    d: "d", dee: "d",
    e: "e", ee: "e",
    f: "f", ef: "f", eff: "f",
    g: "g", gee: "g", jee: "g",
    h: "h", aitch: "h", aych: "h",
    i: "i", eye: "i", ai: "i",
    j: "j", jay: "j",
    k: "k", kay: "k",
    l: "l", el: "l", ell: "l",
    m: "m", em: "m",
    n: "n", en: "n",
    o: "o", oh: "o", owe: "o",
    p: "p", pee: "p", pea: "p",
    q: "q", cue: "q", queue: "q",
    r: "r", are: "r", arr: "r",
    s: "s", es: "s", ess: "s",
    t: "t", tee: "t", tea: "t",
    u: "u", you: "u", yu: "u",
    v: "v", vee: "v",
    w: "w", doubleyou: "w", doubleu: "w",
    x: "x", ex: "x",
    y: "y", why: "y",
    z: "z", zee: "z", zed: "z",
    hyphen: "-", dash: "-",
    apostrophe: "'",
  };

  /* Spoken commands — whole-token match on FINAL results only. */
  const COMMAND_MAP = {
    submit: "submit", done: "submit", enter: "submit",
    undo: "backspace", backspace: "backspace", delete: "backspace",
    clear: "clear",
    repeat: "replay", again: "replay",
  };

  /* Two-word phrases checked before single tokens. */
  const BIGRAMS = {
    "double u": { type: "letter", ch: "w" },
    "double you": { type: "letter", ch: "w" },
    "start over": { type: "command", cmd: "clear" },
  };

  const MAX_SPLIT_LEN = 10;  // longest all-letter token we split char-by-char

  function normalizeToken(tok) {
    return (tok || "").toLowerCase().replace(/[.,!?;:]/g, "").trim();
  }

  /* Parse one final transcript into an ordered op list.
   * Returns { ops: [{type:"letter",ch} | {type:"command",cmd}], unmatched: [tokens] }.
   * Unmappable tokens are dropped silently (caller may hint after a streak). */
  function parseTranscript(text) {
    const tokens = (text || "")
      .toLowerCase()
      .replace(/[.,!?;:]/g, " ")
      .split(/\s+/)
      .map(normalizeToken)
      .filter(Boolean);
    const ops = [];
    const unmatched = [];
    let i = 0;
    while (i < tokens.length) {
      const pair = i + 1 < tokens.length ? tokens[i] + " " + tokens[i + 1] : null;
      if (pair && BIGRAMS[pair]) {
        ops.push(Object.assign({}, BIGRAMS[pair]));
        i += 2;
        continue;
      }
      const tok = tokens[i];
      i += 1;
      if (COMMAND_MAP[tok]) {
        ops.push({ type: "command", cmd: COMMAND_MAP[tok] });
      } else if (LETTER_MAP[tok]) {
        ops.push({ type: "letter", ch: LETTER_MAP[tok] });
      } else if (/^[a-z]+$/.test(tok) && tok.length <= MAX_SPLIT_LEN) {
        // Recognizers often glue fast letter sequences into one token
        // ("a b c" -> "abc", or c-a-t -> "cat"): split char-by-char.
        for (const ch of tok) ops.push({ type: "letter", ch });
      } else {
        unmatched.push(tok);
      }
    }
    return { ops, unmatched };
  }

  const api = { LETTER_MAP, COMMAND_MAP, BIGRAMS, parseTranscript, normalizeToken };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else global.MeshBeeLetters = api;
})(typeof window !== "undefined" ? window : globalThis);
