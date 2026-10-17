/* MeshBee frontend — vanilla JS, no dependencies.
 *
 * NFR-6 note: the target word arrives base64-obfuscated from the server and is
 * decoded only into JS memory for TTS. It is never written to the DOM before
 * the answer is submitted (except the explicit grown-up peek overlay when TTS
 * is unavailable).
 */
"use strict";

// ---------------------------------------------------------------------------
// Tiny helpers
// ---------------------------------------------------------------------------
const $ = (id) => document.getElementById(id);

async function api(method, path, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  let data = null;
  try { data = await res.json(); } catch (e) { /* no body */ }
  if (!res.ok) {
    const err = new Error((data && data.error) || `Request failed (${res.status})`);
    err.status = res.status;
    err.data = data || {};
    throw err;
  }
  return data;
}

function decodeWord(b64) {
  const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
  return new TextDecoder().decode(bytes);
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function svgIcon(name, className) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", className || "icon");
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", "#icon-" + name);
  svg.appendChild(use);
  return svg;
}

// ---------------------------------------------------------------------------
// Global state
// ---------------------------------------------------------------------------
const state = {
  profiles: [],
  profile: null,          // active profile object
  corpusMeta: null,
  lists: [],
  progress: null,         // active profile progress
  session: null,          // { id, entries:[{word,definition,sentence}], index, total, label, source }
  answeredEntry: null,    // entry just answered (feedback phase): replay/spell-out target
  editingProfileId: null,
  editingListId: null,
  lastMessageIdx: -1,
  autoAdvanceTimer: null,
  advanceArmed: false,    // feedback showing, waiting to advance
  pendingAnswer: false,
};

const AVATAR_COLORS = ["#f6b73c", "#e2873b", "#5a9e4b", "#5b9bd5", "#9a6cc0", "#d4566a", "#2aa6a1", "#8a6d4a"];
const AVATAR_ICONS = ["bee", "flower", "leaf", "sun", "butterfly", "mushroom", "ladybug", "star"];

const ENCOURAGEMENTS = [
  "Sweet as honey! You nailed it!",
  "Bzzz-rilliant spelling!",
  "You're on a roll, super speller!",
  "Wow! That word didn't stand a chance!",
  "Un-bee-lievable! Perfect!",
  "That's the way! Keep flying!",
  "Honey-sweet! Exactly right!",
  "You spelled it like a champion!",
  "Buzz buzz! Another one in the hive!",
  "Amazing! Your spelling wings are strong!",
  "Golden! Just like honey!",
  "Superstar speller spotted!",
];

const TIER_INFO = {
  1: { cls: "tier-1", sub: "Warm-up — easier words to get buzzing" },
  2: { cls: "tier-2", sub: "Challenger — trickier words ahead" },
  3: { cls: "tier-3", sub: "Bee-Ready — the toughest of the bunch" },
};

// ---------------------------------------------------------------------------
// Text-to-speech
// ---------------------------------------------------------------------------
const tts = {
  available: ("speechSynthesis" in window) && ("SpeechSynthesisUtterance" in window),
  broken: false,          // detected failure at runtime
  voices: [],
  chosenVoice: null,

  init() {
    if (!this.available) { this.markBroken(); return; }
    const load = () => {
      this.voices = speechSynthesis.getVoices().filter((v) => v.lang && v.lang.toLowerCase().startsWith("en"));
      this.pickVoice();
      populateVoiceSelect();
    };
    load();
    speechSynthesis.addEventListener("voiceschanged", load);
    // If no voices ever appear, flag as broken after a grace period.
    setTimeout(() => {
      if (speechSynthesis.getVoices().length === 0) this.markBroken();
    }, 3000);
  },

  // FR-20: deterministic voice pick — stable sort, prefer en-US + local voices.
  pickVoice() {
    const saved = localStorage.getItem("meshbee.voice");
    if (saved) {
      const match = this.voices.find((v) => v.name === saved);
      if (match) { this.chosenVoice = match; return; }
    }
    const sorted = [...this.voices].sort((a, b) => {
      const aUS = a.lang.toLowerCase() === "en-us" ? 0 : 1;
      const bUS = b.lang.toLowerCase() === "en-us" ? 0 : 1;
      if (aUS !== bUS) return aUS - bUS;
      if (a.localService !== b.localService) return a.localService ? -1 : 1;
      return a.name.localeCompare(b.name);
    });
    this.chosenVoice = sorted[0] || null;
  },

  speak(text, rate = 0.92) {
    if (!this.available || this.broken || !text) return;
    try {
      speechSynthesis.cancel();
      const utt = new SpeechSynthesisUtterance(text);
      if (this.chosenVoice) utt.voice = this.chosenVoice;
      utt.rate = rate;
      utt.pitch = 1.05;
      let started = false;
      utt.onstart = () => {
        started = true;
        $("btn-replay").classList.add("speaking");
      };
      utt.onend = () => $("btn-replay").classList.remove("speaking");
      utt.onerror = (e) => {
        $("btn-replay").classList.remove("speaking");
        if (e.error !== "canceled" && e.error !== "interrupted") this.markBroken();
      };
      speechSynthesis.speak(utt);
      setTimeout(() => {
        if (!started && !speechSynthesis.speaking && !speechSynthesis.pending) this.markBroken();
      }, 2500);
    } catch (e) {
      this.markBroken();
    }
  },

  /* "Spell it to me": speak the word, then each letter as its own utterance
   * so the queue produces natural pauses. Post-answer only (caller enforces). */
  spellOut(word) {
    if (!this.available || this.broken || !word) return;
    try {
      speechSynthesis.cancel();
      const pieces = [word];
      for (const ch of word) {
        if (ch === "-") pieces.push("hyphen");
        else if (ch === "'") pieces.push("apostrophe");
        else if (ch === " ") pieces.push("new word");
        else pieces.push(ch.toUpperCase() + ".");
      }
      pieces.push(word);
      for (const piece of pieces) {
        const utt = new SpeechSynthesisUtterance(piece);
        if (this.chosenVoice) utt.voice = this.chosenVoice;
        utt.rate = piece === word ? 0.9 : 0.75;
        utt.pitch = 1.05;
        speechSynthesis.speak(utt);
      }
    } catch (e) {
      this.markBroken();
    }
  },

  markBroken() {
    if (this.broken) return;
    this.broken = true;
    $("tts-banner").classList.remove("hidden");
    $("btn-peek").classList.remove("hidden");
  },
};

function populateVoiceSelect() {
  const sel = $("voice-select");
  sel.innerHTML = "";
  if (!tts.voices.length) {
    sel.appendChild(el("option", null, "No voices found"));
    return;
  }
  const auto = el("option", null, "Automatic (recommended)");
  auto.value = "";
  sel.appendChild(auto);
  for (const v of tts.voices) {
    const opt = el("option", null, `${v.name} (${v.lang})`);
    opt.value = v.name;
    if (localStorage.getItem("meshbee.voice") === v.name) opt.selected = true;
    sel.appendChild(opt);
  }
}

// ---------------------------------------------------------------------------
// Answer input controller — single owner of the answer box. Alternate input
// sources (e.g. the planned voice-spelling via Web Speech Recognition, see
// README Roadmap) feed letters through this API instead of touching the DOM.
// ---------------------------------------------------------------------------
const answerInput = {
  el: null,
  init(el) { this.el = el; },
  get value() { return this.el.value; },
  clear() { this.el.value = ""; },
  appendLetter(ch) {           // future: speech-recognition letters land here
    if (this.el.disabled || typeof ch !== "string") return;
    this.el.value += ch;
  },
  backspace() { if (!this.el.disabled) this.el.value = this.el.value.slice(0, -1); },
  setEnabled(on) {
    this.el.disabled = !on;
    $("btn-submit").disabled = !on;
  },
  focus() { this.el.focus(); },
  submit() { submitAnswer(); },
};

// ---------------------------------------------------------------------------
// Screen router
// ---------------------------------------------------------------------------
const SCREENS = ["screen-profiles", "screen-source", "screen-practice", "screen-summary", "screen-lists"];

function showScreen(id) {
  for (const s of SCREENS) $(s).classList.toggle("hidden", s !== id);
  if (id !== "screen-practice") clearAutoAdvance();
}

// ---------------------------------------------------------------------------
// Avatars
// ---------------------------------------------------------------------------
function avatarNode(avatar, extraClass) {
  const wrap = el("span", "avatar-dot" + (extraClass ? " " + extraClass : ""));
  wrap.style.background = (avatar && avatar.color) || "#f6b73c";
  wrap.appendChild(svgIcon((avatar && avatar.icon) || "bee"));
  return wrap;
}

// ---------------------------------------------------------------------------
// Profile picker
// ---------------------------------------------------------------------------
async function loadProfiles() {
  const data = await api("GET", "/api/profiles");
  state.profiles = data.profiles;
  renderProfiles();
}

function renderProfiles() {
  const grid = $("profile-grid");
  grid.innerHTML = "";
  for (const p of state.profiles) {
    const card = el("button", "profile-card");
    card.type = "button";
    card.appendChild(avatarNode(p.avatar));
    card.appendChild(el("span", "name", p.name));
    card.addEventListener("click", () => pickProfile(p));
    const edit = el("span", "edit-btn");
    edit.setAttribute("role", "button");
    edit.setAttribute("tabindex", "0");
    edit.setAttribute("aria-label", `Edit ${p.name}`);
    edit.appendChild(svgIcon("pencil"));
    const openEdit = (ev) => { ev.stopPropagation(); showProfileForm(p); };
    edit.addEventListener("click", openEdit);
    edit.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") openEdit(ev); });
    card.appendChild(edit);
    grid.appendChild(card);
  }
  const add = el("button", "profile-card new-card");
  add.type = "button";
  add.appendChild(svgIcon("plus", "icon-big"));
  add.appendChild(el("span", "name", "New bee"));
  add.addEventListener("click", () => showProfileForm(null));
  grid.appendChild(add);
}

let formAvatar = { color: AVATAR_COLORS[0], icon: AVATAR_ICONS[0] };

function showProfileForm(profile) {
  state.editingProfileId = profile ? profile.id : null;
  $("profile-form").classList.remove("hidden");
  $("profile-form-title").textContent = profile ? `Edit ${profile.name}` : "New bee";
  $("profile-name").value = profile ? profile.name : "";
  $("profile-form-error").textContent = "";
  $("profile-delete").classList.toggle("hidden", !profile);
  formAvatar = profile
    ? { color: profile.avatar.color, icon: profile.avatar.icon }
    : { color: AVATAR_COLORS[Math.floor(Math.random() * AVATAR_COLORS.length)], icon: AVATAR_ICONS[0] };
  renderSwatches();
  $("profile-name").focus();
}

function renderSwatches() {
  const colorRow = $("color-picker");
  colorRow.innerHTML = "";
  for (const c of AVATAR_COLORS) {
    const b = el("button", "swatch" + (formAvatar.color === c ? " selected" : ""));
    b.type = "button";
    b.style.background = c;
    b.setAttribute("aria-label", `Color ${c}`);
    b.setAttribute("aria-pressed", String(formAvatar.color === c));
    if (formAvatar.color === c) b.appendChild(svgIcon("check"));
    b.addEventListener("click", () => { formAvatar.color = c; renderSwatches(); });
    colorRow.appendChild(b);
  }
  const iconRow = $("icon-picker");
  iconRow.innerHTML = "";
  for (const ic of AVATAR_ICONS) {
    const b = el("button", "swatch" + (formAvatar.icon === ic ? " selected" : ""));
    b.type = "button";
    b.setAttribute("aria-label", `Buddy ${ic}`);
    b.setAttribute("aria-pressed", String(formAvatar.icon === ic));
    if (formAvatar.icon === ic) { b.style.background = formAvatar.color; b.style.color = "#fff"; }
    b.appendChild(svgIcon(ic));
    b.addEventListener("click", () => { formAvatar.icon = ic; renderSwatches(); });
    iconRow.appendChild(b);
  }
}

async function saveProfile() {
  const name = $("profile-name").value;
  try {
    if (state.editingProfileId) {
      await api("PATCH", `/api/profiles/${state.editingProfileId}`, { name, avatar: formAvatar });
    } else {
      const profile = await api("POST", "/api/profiles", { name, avatar: formAvatar });
      await loadProfiles();
      $("profile-form").classList.add("hidden");
      pickProfile(profile);
      return;
    }
    $("profile-form").classList.add("hidden");
    await loadProfiles();
  } catch (e) {
    $("profile-form-error").textContent = e.message;
  }
}

function deleteProfileFlow() {
  const p = state.profiles.find((x) => x.id === state.editingProfileId);
  if (!p) return;
  confirmDialog(`Delete ${p.name}?`,
    `This removes ${p.name}'s stars, streaks, and word history forever.`,
    async () => {
      await api("DELETE", `/api/profiles/${p.id}`);
      if (localStorage.getItem("meshbee.profileId") === p.id) {
        localStorage.removeItem("meshbee.profileId");
      }
      $("profile-form").classList.add("hidden");
      await loadProfiles();
    });
}

async function pickProfile(profile) {
  state.profile = profile;
  localStorage.setItem("meshbee.profileId", profile.id);
  const chip = $("btn-switch-profile");
  chip.innerHTML = "";
  chip.appendChild(avatarNode(profile.avatar));
  chip.appendChild(document.createTextNode(profile.name));
  chip.classList.remove("hidden");
  await openSourceScreen();
}

// ---------------------------------------------------------------------------
// Source select
// ---------------------------------------------------------------------------
async function openSourceScreen() {
  showScreen("screen-source");
  $("source-greeting").textContent = `Choose your words, ${state.profile.name}!`;
  const [meta, listsData, progress] = await Promise.all([
    api("GET", "/api/corpus/meta"),
    api("GET", "/api/lists"),
    api("GET", `/api/profiles/${state.profile.id}/progress`),
  ]);
  state.corpusMeta = meta;
  state.lists = listsData.lists;
  state.progress = progress;
  renderSourceCards();
}

function renderSourceCards() {
  const meta = state.corpusMeta;
  $("no-corpus-msg").classList.toggle("hidden", meta.loaded);
  const tierWrap = $("tier-cards");
  tierWrap.innerHTML = "";
  if (meta.loaded) {
    for (const t of [1, 2, 3]) {
      const count = meta.tiers[String(t)] || 0;
      const card = el("button", `source-card ${TIER_INFO[t].cls}`);
      card.type = "button";
      const title = el("span", "src-title");
      title.appendChild(svgIcon("bee"));
      title.appendChild(document.createTextNode(meta.tierNames[String(t)]));
      card.appendChild(title);
      card.appendChild(el("span", "src-sub", `${TIER_INFO[t].sub} · ${count} words`));
      card.disabled = count < 3;
      card.addEventListener("click", () => startSession({ type: "tier", tier: t }));
      tierWrap.appendChild(card);
    }
  }
  // Tricky words card
  const trickyCount = state.progress.trickyCount;
  const tricky = el("button", "source-card tricky");
  tricky.type = "button";
  const tTitle = el("span", "src-title");
  tTitle.appendChild(svgIcon("flame"));
  tTitle.appendChild(document.createTextNode("Tricky words"));
  tricky.appendChild(tTitle);
  tricky.appendChild(el("span", "src-sub",
    trickyCount >= 3
      ? `Review ${trickyCount} words you've missed`
      : `Words you miss land here (need 3, have ${trickyCount})`));
  tricky.disabled = trickyCount < 3;
  tricky.addEventListener("click", () => startSession({ type: "tricky" }));
  tierWrap.appendChild(tricky);

  // Custom lists
  const wrap = $("custom-source-cards");
  wrap.innerHTML = "";
  if (!state.lists.length) {
    wrap.appendChild(el("p", "src-sub", "No custom lists yet — make one in Manage lists!"));
  }
  for (const list of state.lists) {
    const card = el("button", "source-card custom");
    card.type = "button";
    const title = el("span", "src-title");
    title.appendChild(svgIcon("book"));
    title.appendChild(document.createTextNode(list.name));
    card.appendChild(title);
    card.appendChild(el("span", "src-sub", `${list.entries.length} words`));
    card.disabled = list.entries.length < 3;
    card.addEventListener("click", () => startSession({ type: "list", listId: list.id }));
    wrap.appendChild(card);
  }
}

// ---------------------------------------------------------------------------
// Practice session
// ---------------------------------------------------------------------------
async function startSession(source) {
  let data;
  try {
    data = await api("POST", "/api/session", { profileId: state.profile.id, source });
  } catch (e) {
    alert(e.message);
    return;
  }
  state.session = {
    id: data.sessionId,
    label: data.label,
    source,
    total: data.count,
    index: 0,
    results: [],
    streak: 0,
    entries: data.entries.map((e) => ({
      word: decodeWord(e.w),   // JS memory only — never rendered pre-answer
      definition: e.definition,
      sentence: e.sentence,
    })),
  };
  showScreen("screen-practice");
  renderProgressDots();
  updateStreak(0);
  presentWord();
}

function currentEntry() {
  return state.session.entries[state.session.index];
}

function renderProgressDots() {
  const wrap = $("progress-dots");
  wrap.innerHTML = "";
  const s = state.session;
  for (let i = 0; i < s.total; i++) {
    const dot = el("span", "dot");
    const r = s.results[i];
    if (r === true) { dot.classList.add("right"); dot.appendChild(svgIcon("check")); }
    else if (r === false) { dot.classList.add("wrong"); dot.appendChild(svgIcon("cross")); }
    else if (i === s.index) dot.classList.add("current");
    wrap.appendChild(dot);
  }
}

function updateStreak(n) {
  $("streak-count").textContent = String(n);
}

function presentWord() {
  const entry = currentEntry();
  state.advanceArmed = false;
  state.pendingAnswer = false;
  state.answeredEntry = null;
  $("feedback").classList.add("hidden");
  $("feedback").innerHTML = "";
  answerInput.clear();
  answerInput.setEnabled(true);
  $("btn-def").disabled = !entry.definition;
  $("btn-sentence").disabled = !entry.sentence;
  $("btn-def").classList.remove("used");
  $("btn-sentence").classList.remove("used");
  renderProgressDots();
  answerInput.focus();
  // NFR-7: speak promptly on presentation
  setTimeout(() => tts.speak(entry.word), 50);
}

function replayWord(rate) {
  // During feedback, replay targets the word just answered — not the next one.
  const entry = state.answeredEntry || currentEntry();
  if (!entry) return;
  tts.speak(entry.word, rate);
}

async function useHint(kind) {
  const entry = currentEntry();
  if (!entry) return;
  let text = kind === "definition" ? entry.definition : entry.sentence;
  if (!text) return;
  // The server masks the target word inside its own definition/sentence
  // (NFR-6); substitute it back in for speech only — never into the DOM.
  text = text.replace(/_{3,}/g, entry.word);
  tts.speak(text, 0.95);
  $(kind === "definition" ? "btn-def" : "btn-sentence").classList.add("used");
  try {
    await api("POST", `/api/session/${state.session.id}/hint`, { kind });
  } catch (e) { /* non-fatal */ }
}

async function openPeek() {
  const entry = currentEntry();
  if (!entry) return;
  $("peek-word").textContent = entry.word;  // explicit grown-up fallback (FR-19)
  $("peek-overlay").classList.remove("hidden");
  $("peek-close").focus();
  try {
    await api("POST", `/api/session/${state.session.id}/hint`,
      { kind: "peek", ttsBroken: tts.broken });
  } catch (e) { /* non-fatal */ }
}

async function submitAnswer() {
  const s = state.session;
  if (!s || state.advanceArmed || state.pendingAnswer) return;
  const attempt = answerInput.value.trim();
  if (!attempt) { answerInput.focus(); return; }
  const answered = currentEntry();
  state.pendingAnswer = true;
  answerInput.setEnabled(false);
  let res;
  try {
    res = await api("POST", `/api/session/${s.id}/answer`, { attempt });
  } catch (e) {
    state.pendingAnswer = false;
    answerInput.setEnabled(true);
    alert(e.message);
    return;
  }
  state.answeredEntry = answered;
  s.results[s.index] = res.correct;
  s.index = res.index;
  s.summary = res.summary;
  updateStreak(res.streak);
  renderProgressDots();
  state.pendingAnswer = false;
  if (res.correct) showCorrectFeedback(res);
  else showWrongFeedback(res);
}

function pickEncouragement() {
  let idx;
  do { idx = Math.floor(Math.random() * ENCOURAGEMENTS.length); }
  while (idx === state.lastMessageIdx && ENCOURAGEMENTS.length > 1);
  state.lastMessageIdx = idx;
  return ENCOURAGEMENTS[idx];
}

function spellButton(word) {
  const b = el("button", "btn btn-ghost btn-small");
  b.id = "btn-spell";
  b.type = "button";
  b.appendChild(svgIcon("abc"));
  b.appendChild(document.createTextNode("Spell it to me"));
  b.addEventListener("click", () => {
    clearAutoAdvance();          // don't cut the spelling off mid-letter
    tts.spellOut(word);
  });
  return b;
}

function showCorrectFeedback(res) {
  const fb = $("feedback");
  fb.innerHTML = "";
  const card = el("div", "feedback-card ok");
  const title = el("p", "feedback-title");
  title.appendChild(svgIcon("check"));
  title.appendChild(document.createTextNode(pickEncouragement()));
  card.appendChild(title);
  card.appendChild(el("p", "feedback-sub", "Press any key to keep going…"));
  const row = el("div", "feedback-actions");
  row.appendChild(spellButton(res.correctWord));
  card.appendChild(row);
  fb.appendChild(card);
  fb.classList.remove("hidden");
  $("mascot").classList.add("happy");
  setTimeout(() => $("mascot").classList.remove("happy"), 800);
  burstConfetti();
  state.advanceArmed = true;
  // auto-advance ~1.8s, skippable by any keypress/click (FR-4)
  state.autoAdvanceTimer = setTimeout(advance, 1800);
}

function showWrongFeedback(res) {
  const fb = $("feedback");
  fb.innerHTML = "";
  const card = el("div", "feedback-card nope");
  const title = el("p", "feedback-title");
  title.appendChild(svgIcon("bee"));
  title.appendChild(document.createTextNode("So close! Let's look together."));
  card.appendChild(title);
  card.appendChild(renderDiff(res.diff, res.attempt, res.correctWord));
  const row = el("div", "feedback-actions");
  row.appendChild(spellButton(res.correctWord));
  const gotIt = el("button", "btn btn-primary", "Got it!");
  gotIt.id = "btn-gotit";
  gotIt.type = "button";
  gotIt.addEventListener("click", advance);
  row.appendChild(gotIt);
  card.appendChild(row);
  fb.appendChild(card);
  fb.classList.remove("hidden");
  state.advanceArmed = true;   // manual advance; Enter also works via keydown handler
  gotIt.focus();
}

/* Letter-diff rendering. Icons/decoration + color, never color alone:
 * wrong/extra letters are struck through; missing/fixed letters are underlined;
 * gaps show a dashed placeholder. */
function renderDiff(segments, attempt, correctWord) {
  const wrap = el("div", "diff-rows");
  const rowA = el("div", "diff-row");
  rowA.appendChild(el("span", "diff-label", "You typed:"));
  const lettersA = el("span", "diff-letters");
  const rowC = el("div", "diff-row");
  rowC.appendChild(el("span", "diff-label", "Correct:"));
  const lettersC = el("span", "diff-letters");
  for (const seg of segments || []) {
    if (seg.op === "match") {
      for (const ch of seg.attempt) lettersA.appendChild(el("span", "dl", ch));
      for (const ch of seg.correct) lettersC.appendChild(el("span", "dl", ch));
    } else if (seg.op === "wrong") {
      for (const ch of seg.attempt) lettersA.appendChild(el("span", "dl wrong", ch));
      for (const ch of seg.correct) lettersC.appendChild(el("span", "dl fix", ch));
    } else if (seg.op === "extra") {
      for (const ch of seg.attempt) lettersA.appendChild(el("span", "dl extra", ch));
    } else if (seg.op === "missing") {
      for (const ch of seg.correct) {
        lettersA.appendChild(el("span", "dl gap", "·"));
        lettersC.appendChild(el("span", "dl missing", ch));
      }
    }
  }
  rowA.appendChild(lettersA);
  rowC.appendChild(lettersC);
  wrap.appendChild(rowA);
  wrap.appendChild(rowC);
  return wrap;
}

function clearAutoAdvance() {
  if (state.autoAdvanceTimer) {
    clearTimeout(state.autoAdvanceTimer);
    state.autoAdvanceTimer = null;
  }
}

function advance() {
  if (!state.advanceArmed) return;
  clearAutoAdvance();
  state.advanceArmed = false;
  const s = state.session;
  if (s.index >= s.total) {
    showSummary(s.summary);
  } else {
    presentWord();
  }
}

// ---------------------------------------------------------------------------
// Confetti
// ---------------------------------------------------------------------------
const CONFETTI_COLORS = ["#f6b73c", "#e2873b", "#5a9e4b", "#5b9bd5", "#d4566a", "#ffd54d"];

function burstConfetti(count = 30) {
  const layer = $("confetti-layer");
  const shapes = ["star", "round", "strip"];
  for (let i = 0; i < count; i++) {
    const p = el("span", "confetti " + shapes[Math.floor(Math.random() * shapes.length)]);
    p.style.left = Math.random() * 100 + "vw";
    p.style.background = CONFETTI_COLORS[Math.floor(Math.random() * CONFETTI_COLORS.length)];
    p.style.animationDuration = 1 + Math.random() * 1.5 + "s";
    p.style.width = p.style.height = 8 + Math.random() * 10 + "px";
    p.style.setProperty("--drift", (Math.random() * 120 - 60).toFixed(0) + "px");
    layer.appendChild(p);
    setTimeout(() => p.remove(), 2800);
  }
}

// ---------------------------------------------------------------------------
// Summary
// ---------------------------------------------------------------------------
function showSummary(summary) {
  showScreen("screen-summary");
  const starsWrap = $("summary-stars");
  starsWrap.innerHTML = "";
  for (let i = 0; i < 3; i++) {
    const star = svgIcon("star", "icon " + (i < summary.stars ? "star-on" : "star-off"));
    starsWrap.appendChild(star);
  }
  $("summary-title").textContent = summary.stars === 3 ? "Perfect round!" : "Great buzzing!";
  $("summary-message").textContent = summary.starsMessage;
  $("summary-score").textContent = `${summary.correctCount}/${summary.total}`;
  $("summary-streak").textContent = String(summary.bestStreak);
  const missedWrap = $("summary-missed");
  missedWrap.innerHTML = "";
  if (summary.missed.length) {
    missedWrap.appendChild(el("h4", null, "Words to practice:"));
    for (const m of summary.missed) {
      const item = el("div", "missed-item");
      item.appendChild(el("span", "missed-word", m.word));
      if (m.attempt) item.appendChild(el("span", "missed-attempt", m.attempt));
      const say = el("button", "btn btn-ghost btn-small");
      say.type = "button";
      say.appendChild(svgIcon("speaker"));
      say.appendChild(document.createTextNode("Hear it"));
      say.addEventListener("click", () => tts.speak(m.word));
      item.appendChild(say);
      const spell = el("button", "btn btn-ghost btn-small");
      spell.type = "button";
      spell.appendChild(svgIcon("abc"));
      spell.appendChild(document.createTextNode("Spell it"));
      spell.addEventListener("click", () => tts.spellOut(m.word));
      item.appendChild(spell);
      missedWrap.appendChild(item);
    }
  }
  if (summary.stars > 0) burstConfetti(40);
  $("btn-play-again").focus();
}

// ---------------------------------------------------------------------------
// List manager
// ---------------------------------------------------------------------------
async function openListManager() {
  showScreen("screen-lists");
  $("list-form").classList.add("hidden");
  const data = await api("GET", "/api/lists");
  state.lists = data.lists;
  renderListCards();
}

function renderListCards() {
  const wrap = $("list-cards");
  wrap.innerHTML = "";
  if (!state.lists.length) {
    wrap.appendChild(el("p", "src-sub", "No lists yet. Make your first one below!"));
  }
  for (const list of state.lists) {
    const card = el("div", "card list-card");
    const info = el("div", "list-info");
    info.appendChild(el("div", "list-name", list.name));
    info.appendChild(el("div", "list-count", `${list.entries.length} words`));
    card.appendChild(info);
    const editBtn = el("button", "btn btn-ghost btn-small");
    editBtn.type = "button";
    editBtn.appendChild(svgIcon("pencil"));
    editBtn.appendChild(document.createTextNode("Edit"));
    editBtn.addEventListener("click", () => showListForm(list));
    card.appendChild(editBtn);
    const delBtn = el("button", "btn btn-danger btn-small");
    delBtn.type = "button";
    delBtn.appendChild(svgIcon("trash"));
    delBtn.appendChild(document.createTextNode("Delete"));
    delBtn.addEventListener("click", () => {
      confirmDialog(`Delete "${list.name}"?`,
        "This list will be gone for every bee. This cannot be undone.",
        async () => {
          await api("DELETE", `/api/lists/${list.id}`);
          await openListManager();
        });
    });
    card.appendChild(delBtn);
    wrap.appendChild(card);
  }
}

function showListForm(list) {
  state.editingListId = list ? list.id : null;
  $("list-form").classList.remove("hidden");
  $("list-form-title").textContent = list ? `Edit "${list.name}"` : "New list";
  $("list-name").value = list ? list.name : "";
  $("list-text").value = list ? (list.rawText || rebuildListText(list)) : "";
  $("list-form-feedback").innerHTML = "";
  $("list-name").focus();
}

function rebuildListText(list) {
  return list.entries.map((e) => {
    let line = e.word;
    if (e.definition || e.sentence) line += " | " + (e.definition || "");
    if (e.sentence) line += " | " + e.sentence;
    return line;
  }).join("\n");
}

function renderListFeedback(errors, warnings, savedNote) {
  const box = $("list-form-feedback");
  box.innerHTML = "";
  for (const err of errors || []) {
    const row = el("div", "err");
    row.appendChild(svgIcon("cross"));
    row.appendChild(el("span", null, `Line ${err.line}: ${err.message}`));
    box.appendChild(row);
  }
  for (const warn of warnings || []) {
    const row = el("div", "warn");
    row.appendChild(svgIcon("bee"));
    row.appendChild(el("span", null, `Line ${warn.line}: ${warn.message}`));
    box.appendChild(row);
  }
  if (savedNote) box.appendChild(el("p", "ok-note", savedNote));
}

async function saveList() {
  const name = $("list-name").value;
  const text = $("list-text").value;
  // Pre-save validation pass so per-line errors show before anything is saved (FR-16)
  const check = await api("POST", "/api/lists/validate", { text });
  if (check.errors.length) {
    renderListFeedback(check.errors, check.warnings);
    return;
  }
  try {
    let result;
    if (state.editingListId) {
      result = await api("PUT", `/api/lists/${state.editingListId}`, { name, text });
    } else {
      result = await api("POST", "/api/lists", { name, text });
    }
    renderListFeedback([], result.warnings,
      `Saved! "${result.list.name}" has ${result.list.entries.length} words.`);
    state.editingListId = result.list.id;
    $("list-form-title").textContent = `Edit "${result.list.name}"`;
    const data = await api("GET", "/api/lists");
    state.lists = data.lists;
    renderListCards();
  } catch (e) {
    renderListFeedback(e.data.errors || [{ line: 0, message: e.message }], e.data.warnings);
  }
}

// ---------------------------------------------------------------------------
// Confirm dialog
// ---------------------------------------------------------------------------
let confirmAction = null;

function confirmDialog(title, text, onYes) {
  $("confirm-title").textContent = title;
  $("confirm-text").textContent = text;
  confirmAction = onYes;
  $("confirm-overlay").classList.remove("hidden");
  $("confirm-no").focus();
}

// ---------------------------------------------------------------------------
// Keyboard shortcuts
// ---------------------------------------------------------------------------
document.addEventListener("keydown", (ev) => {
  const practicing = !$("screen-practice").classList.contains("hidden");
  // overlay escapes
  if (ev.key === "Escape") {
    for (const id of ["peek-overlay", "confirm-overlay", "settings-overlay"]) {
      if (!$(id).classList.contains("hidden")) { $(id).classList.add("hidden"); ev.preventDefault(); return; }
    }
  }
  if (!practicing) return;

  if (ev.key === "F2" || (ev.altKey && (ev.key === "r" || ev.key === "R"))) {
    ev.preventDefault(); replayWord(); return;
  }
  if (ev.altKey && (ev.key === "s" || ev.key === "S")) { ev.preventDefault(); replayWord(0.55); return; }
  if (ev.altKey && (ev.key === "d" || ev.key === "D")) { ev.preventDefault(); useHint("definition"); return; }
  if (ev.altKey && (ev.key === "e" || ev.key === "E")) { ev.preventDefault(); useHint("sentence"); return; }

  // When feedback is showing, any key advances a correct answer; Enter advances
  // a wrong one. Keys aimed at the "Spell it to me" button pass through so it
  // stays keyboard-operable.
  if (state.advanceArmed) {
    if (ev.target && ev.target.id === "btn-spell") return;
    const wasWrong = state.session && state.session.results[state.session.index - 1] === false;
    if (wasWrong) {
      if (ev.key === "Enter") { ev.preventDefault(); advance(); }
    } else if (!ev.altKey && !ev.ctrlKey && !ev.metaKey && ev.key !== "Tab") {
      ev.preventDefault(); advance();
    }
  }
});

// ---------------------------------------------------------------------------
// Wire-up
// ---------------------------------------------------------------------------
function wireEvents() {
  answerInput.init($("answer-input"));
  $("profile-save").addEventListener("click", saveProfile);
  $("profile-cancel").addEventListener("click", () => $("profile-form").classList.add("hidden"));
  $("profile-delete").addEventListener("click", deleteProfileFlow);
  $("profile-name").addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") { ev.preventDefault(); saveProfile(); }
  });
  $("btn-switch-profile").addEventListener("click", () => {
    state.profile = null;
    localStorage.removeItem("meshbee.profileId");
    $("btn-switch-profile").classList.add("hidden");
    showScreen("screen-profiles");
    loadProfiles();
  });

  $("btn-manage-lists").addEventListener("click", openListManager);
  $("btn-lists-back").addEventListener("click", () =>
    state.profile ? openSourceScreen() : showScreen("screen-profiles"));
  $("btn-new-list").addEventListener("click", () => showListForm(null));
  $("list-save").addEventListener("click", saveList);
  $("list-cancel").addEventListener("click", () => $("list-form").classList.add("hidden"));

  $("answer-form").addEventListener("submit", (ev) => { ev.preventDefault(); submitAnswer(); });
  $("btn-replay").addEventListener("click", () => replayWord());
  $("btn-slow").addEventListener("click", () => replayWord(0.55));
  $("btn-def").addEventListener("click", () => useHint("definition"));
  $("btn-sentence").addEventListener("click", () => useHint("sentence"));
  $("btn-peek").addEventListener("click", openPeek);
  $("peek-close").addEventListener("click", () => {
    $("peek-overlay").classList.add("hidden");
    $("answer-input").focus();
  });
  $("btn-quit").addEventListener("click", () => {
    confirmDialog("Stop practicing?", "This round won't be finished.", () => openSourceScreen());
  });

  $("btn-play-again").addEventListener("click", () => startSession(state.session.source));
  $("btn-change-list").addEventListener("click", openSourceScreen);

  $("confirm-yes").addEventListener("click", async () => {
    $("confirm-overlay").classList.add("hidden");
    if (confirmAction) { const fn = confirmAction; confirmAction = null; await fn(); }
  });
  $("confirm-no").addEventListener("click", () => {
    confirmAction = null;
    $("confirm-overlay").classList.add("hidden");
  });

  $("btn-settings").addEventListener("click", () => {
    populateVoiceSelect();
    $("settings-overlay").classList.remove("hidden");
    $("voice-select").focus();
  });
  $("settings-close").addEventListener("click", () => $("settings-overlay").classList.add("hidden"));
  $("voice-select").addEventListener("change", () => {
    const name = $("voice-select").value;
    if (name) localStorage.setItem("meshbee.voice", name);
    else localStorage.removeItem("meshbee.voice");
    tts.pickVoice();
  });
  $("voice-test").addEventListener("click", () => tts.speak("Hello! Let's practice spelling together."));
}

async function init() {
  wireEvents();
  tts.init();
  await loadProfiles();
  const rememberedId = localStorage.getItem("meshbee.profileId");
  const remembered = state.profiles.find((p) => p.id === rememberedId);
  if (remembered) {
    pickProfile(remembered);
  } else {
    showScreen("screen-profiles");
  }
}

init().catch((e) => {
  console.error(e);
  alert("MeshBee could not start: " + e.message);
});
