"use strict";
// Everything that came from TMDB or a file is inserted with textContent. The exceptions are the evidence grid and the
// flow's hover cards, which the server builds with every value HTML-escaped; the Content-Security-Policy also forbids
// inline script, so even a slip there could not run code.
const state = {
  token: null, hasKey: null, key: "", defaultFormats: null, files: [], tab: "input", cur: null, detail: null, working: false,
  history: [],         // what `u` undoes, newest last: {type: "move" | "decide" | "forget", ids, ...}
  sticky: new Set(),   // files that changed status here but stay in this tab's list until the tab changes, so the cursor doesn't jump
  posters: new Map(),  // "movie:603" -> poster URL, or null when TMDB has none; looked up once per title for the list thumbnails
  leftovers: [],       // release folders emptied of videos, with junk the person can send to the trash (shown in the Output tab)
  folders: {},         // { input, output } as the server has them, shown in the empty Input tab
  live: null,          // whether the server answered the last poll (null until it has been asked); lastCheck is when it did
  lastCheck: 0,
};
const $ = (selector) => document.querySelector(selector);
const post = (body) => ({ method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

const TABS = [
  { id: "input", label: "Input", statuses: ["decided", "review", "pending"], empty: ["Nothing waiting.", "Videos found in your input folder show up here, ready to rename."] },
  { id: "output", label: "Output", statuses: ["moved"], empty: ["Nothing renamed yet.", "Files you approve are moved into your library and listed here. They can be put back."] },
];
const tabFor = (status) => TABS.find((t) => t.statuses.includes(status)).id;

function h(tag, props = {}, ...kids) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (value !== null && value !== undefined && value !== false) node.setAttribute(key, value);
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    node.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return node;
}
const kbd = (text) => h("kbd", {}, text);

function say(text, isError = false) {
  const box = $("#message");
  box.hidden = !text;
  box.textContent = text || "";
  box.className = "rv-message" + (isError ? " error" : "");
}

let toastTimer = null;
function toast(text, { undo = false, error = false } = {}) {
  const box = $("#toast");
  $("#toast-text").textContent = text;
  $("#toast-undo").hidden = !undo;
  box.className = "rv-toast show" + (error ? " error" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => box.classList.remove("show"), error ? 8000 : 5000);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "X-Token": state.token, "Content-Type": "application/json" },
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || response.statusText);
  return body;
}

// Preferences (advanced view, open sections, player volume) are kept in this browser only; if storage is
// blocked they are simply not remembered.
function remembered(key, fallback = null) {
  try { return localStorage.getItem(key) ?? fallback; } catch { return fallback; }
}
function remember(key, value) {
  try { localStorage.setItem(key, value); } catch { /* not remembered */ }
}
const pref = (key, fallback = "0") => remembered(key, fallback) === "1";
// Text size is a scale on the root font size (the stylesheet sizes its text in rem), remembered like the other preferences.
const TEXT_SCALES = [0.75, 0.85, 1, 1.15, 1.3, 1.5];
function setTextSize(step, save = true) {
  const i = Math.min(TEXT_SCALES.length - 1, Math.max(0, step));
  document.documentElement.style.setProperty("--text-scale", String(TEXT_SCALES[i]));
  $("#text-smaller").disabled = i === 0;
  $("#text-larger").disabled = i === TEXT_SCALES.length - 1;
  if (save) remember("textScale", String(TEXT_SCALES[i]));
  return i;
}
function textSizeSetup() {
  let step = setTextSize(TEXT_SCALES.indexOf(Number(remembered("textScale"))) >= 0 ? TEXT_SCALES.indexOf(Number(remembered("textScale"))) : TEXT_SCALES.indexOf(1), false);
  $("#text-smaller").addEventListener("click", () => { step = setTextSize(step - 1); });
  $("#text-larger").addEventListener("click", () => { step = setTextSize(step + 1); });
}
const advanced = () => $("#advanced").checked;
// The switch lives in Settings, so like the rest of it the view changes only when Done is pressed.
function applyAdvanced() {
  remember("advanced", advanced() ? "1" : "0");
  $("#stats").hidden = !advanced();
  loadStats();
  rememberPosition();
  if (state.detail) renderDetail(state.detail);
}

// The details pane's width is dragged (or arrowed) at the splitter on its left edge and remembered; the grid takes the rest.
const LIST_MIN_W = 590; // keep in step with the calc() in .rv-body.with-detail
function detailSplitter() {
  const body = $("#results"), bar = $("#splitter");
  const clamp = (w) => Math.round(Math.min(Math.max(w, 360), Math.max(360, body.clientWidth - LIST_MIN_W)));
  const set = (w, save = true) => {
    body.style.setProperty("--detail-w", `${w}px`);
    bar.setAttribute("aria-valuenow", String(w));
    if (save) remember("detailWidth", String(w));
  };
  const saved = Number(remembered("detailWidth"));
  if (saved) set(Math.max(saved, 360), false); // #results may still be hidden here, so its width cannot clamp this
  bar.addEventListener("pointerdown", (e) => {
    bar.setPointerCapture(e.pointerId);
    bar.classList.add("dragging");
    document.body.classList.add("resizing");
  });
  bar.addEventListener("pointermove", (e) => {
    if (bar.hasPointerCapture(e.pointerId)) set(clamp(body.getBoundingClientRect().right - e.clientX));
  });
  const stop = () => { bar.classList.remove("dragging"); document.body.classList.remove("resizing"); };
  bar.addEventListener("pointerup", stop);
  bar.addEventListener("pointercancel", stop);
  bar.addEventListener("dblclick", () => { body.style.removeProperty("--detail-w"); remember("detailWidth", ""); });
  bar.addEventListener("keydown", (e) => {
    const step = { ArrowLeft: 20, ArrowRight: -20 }[e.key];  // the bar moves with the arrow, so left widens the pane
    if (step) { e.preventDefault(); set(clamp($("#detail").getBoundingClientRect().width + step)); }
  });
}

async function loadStats() {
  if (!advanced()) return;
  try {
    const s = await api("/api/stats");
    $("#stats").textContent = `TMDB requests: ${s.tmdb_requests}`;
  } catch { /* the counters are a convenience; never let them interrupt work */ }
}

// One action at a time; keys pressed meanwhile are ignored rather than queued, so nothing happens to the wrong file.
async function act(work) {
  if (state.working) return;
  state.working = true;
  try {
    return await work();
  } catch (error) {
    toast(error.message, { error: true });
  } finally {
    state.working = false;
    loadStats();
  }
}

// ----- the list ---------------------------------------------------------------------------------------------

const pad = (n) => String(n).padStart(2, "0");
const basename = (path) => path.split("/").pop();
const yearOf = (ident) => (ident.year ? ` (${ident.year})` : "");

function confidence(f) {
  if (f.status === "moved") return ["moved", "✓ Renamed"];
  if (f.status === "decided") {
    return f.source === "auto" ? ["sure", "Sure match"] : ["yours", "Matched by you"];
  }
  if (f.status === "pending") return ["none", "Not checked yet"];
  return ["guess", "Needs a match"];
}

// What each dot colour means (matched rows show no dot), shown when the dot is hovered.
const DOT_TIPS = {
  guess: "Needs a match: there are possible matches, but you need to pick the right one.",
  none: "Not checked yet: this file hasn't been looked up on TMDB.",
  moved: "Renamed: moved into the output folder.",
};

// Matched rows (sure or by you) and unmatched ones (they show a "?" thumbnail) get no dot; only renamed ones do.
function dot(f) {
  const kind = confidence(f)[0];
  if (kind === "sure" || kind === "yours" || !f.identity) return h("span", { class: "rv-dot blank" });
  return h("span", { class: "rv-dot " + kind, title: DOT_TIPS[kind], "aria-label": DOT_TIPS[kind] });
}

function groupOf(f) {
  const ident = f.identity;
  if (ident && ident.kind === "tv") return { key: "shows", title: "Shows", kind: "tv", order: 0, name: ident.title, show: ident.id };
  if (ident) return { key: "movies", title: "Movies", kind: "movie", order: 1, name: "" };
  return { key: "unmatched", title: "Not matched yet", kind: null, order: 2, name: "" };
}

// A row's thumbnail (the show's poster for an episode): its poster once known, a placeholder meanwhile (and for good if TMDB has none).
// A poster that arrives replaces its placeholders in place, so the open detail (and its player) is left alone.
const posterImg = (url) => h("img", { class: "rv-thumb", src: url, alt: "", loading: "lazy", referrerpolicy: "no-referrer" });
function thumb(f) {
  const ident = f.identity;
  if (!ident) return h("span", { class: "rv-thumb unknown", "aria-hidden": "true" }, "?");
  const kind = ident.kind;
  const key = `${kind}:${ident.id}`;
  const url = state.posters.get(key);
  if (url) return posterImg(url);
  if (url === undefined) {
    state.posters.set(key, null);
    api(`/api/poster/${kind}/${ident.id}`).then((body) => {
      if (!body.poster || !body.poster.startsWith("https://image.tmdb.org/")) return;
      state.posters.set(key, body.poster);
      for (const slot of document.querySelectorAll(`.rv-thumb[data-poster="${CSS.escape(key)}"]`)) slot.replaceWith(posterImg(body.poster));
    }).catch(() => {});
  }
  return h("span", { class: "rv-thumb noposter", "data-poster": key, "aria-hidden": "true" });
}

function rowTitle(f) {
  const ident = f.identity;
  if (ident && ident.kind === "tv") return ident.title + yearOf(ident) + (ident.season != null ? ` - S${pad(ident.season)}E${pad(ident.episode)}` : "");
  if (ident) return ident.title + yearOf(ident);
  return basename(f.path);
}

// Where each decided file would go, fetched for the rows a few at a time and kept until its match or the formats change.
// Each id maps to its answer from the server ({path, relative, subtitles, taken}, or {error}), or to the pending ask.
const dests = new Map();
let destQueue = [], destBusy = 0;
function askDest(id) {
  const ask = { pending: true };
  dests.set(id, ask);
  destQueue.push([id, ask]);
  pumpDests();
}
function pumpDests() {
  while (destBusy < 4 && destQueue.length) {
    const [id, ask] = destQueue.shift();
    if (dests.get(id) !== ask) continue;  // forgotten while it waited
    destBusy++;
    api(`/api/files/${id}/destination`, post(formats()))
      .catch((error) => ({ error: error.message }))
      .then((result) => { if (dests.get(id) === ask) { dests.set(id, result); updateRow(id); } })
      .finally(() => { destBusy--; pumpDests(); });
  }
}
function clearDests() { dests.clear(); destQueue = []; }
// A file can be accepted once its new name is known and free, and it is known to have finished downloading.
const canRename = (f) => { const dest = dests.get(f.id); return f.status === "decided" && dest && !dest.pending && !dest.error && !dest.taken && writing.get(f.id) === false; };

// The proposed name: the path under the output folder, or where it went once moved.
function proposed(f) {
  if (f.status === "moved") return splitPath(f.moved_to);
  if (f.status !== "decided") return [h("span", { class: "pending" }, writing.get(f.id) === true ? "Waiting for the download to finish…" : "Pick a match to see the new name")];
  if (!dests.has(f.id)) askDest(f.id);
  const dest = dests.get(f.id);
  if (dest.pending) return [h("span", { class: "pending" }, "Working out the new name…")];
  if (dest.error) return [h("span", { class: "error" }, dest.error)];
  const n = dest.subtitles.length;
  return [...splitPath(dest.relative),
    n ? h("span", { class: "subs", title: dest.subtitles.join("\n") }, `+ ${n} subtitle file${n === 1 ? "" : "s"}`) : null,
    dest.taken ? h("span", { class: "error" }, "A file with this name is already there. Choose another match.") : null];
}

function rowAction(f) {
  const stop = (fn) => (event) => { event.stopPropagation(); fn(); };
  if (f.status === "moved") return h("button", { type: "button", onclick: stop(() => restore(f.id)) }, "Put it back");
  if (f.status !== "decided") return writing.get(f.id) === true ? null  // a download isn't ready to be matched yet
    : h("button", { type: "button", onclick: stop(() => { select(f.id); search(); }) }, "Choose match…", kbd("/"));
  const waiting = writing.get(f.id) !== false ? (writing.has(f.id) ? "Still being written: wait until it's done." : "Checking whether it's still being written…") : null;
  return [h("button", { type: "button", class: "danger", disabled: writing.get(f.id) !== false, title: waiting, onclick: stop(() => remove(f.id)) }, "Delete"),
    h("button", { type: "button", class: "go", disabled: !canRename(f), title: waiting, onclick: stop(() => approve(f.id)) }, "Accept")];
}

// Whether each file still in the input folder looks like a download in progress, by id; asked for every few seconds.
// A file is only accepted once this says it's done (the server doesn't check yet).
const writing = new Map();
function writeState(f) {
  if (f.status === "moved" || !writing.has(f.id)) return null;
  return writing.get(f.id)
    ? h("span", { class: "rv-writing busy", title: "Still being written: a process has it open for writing, or it changed in the last few seconds." }, "In progress")
    : h("span", { class: "rv-writing done", title: "Nothing is writing to it, and it hasn't changed in the last few seconds." }, "Done");
}
// Not known to be done yet. A moved file is done; it isn't in the answer.
const downloading = (id) => state.files.find((f) => f.id === id)?.status !== "moved" && writing.get(id) !== false;
// A download just finished: the server dropped what it had read from the partial file, so work out what it is now.
async function rescanFinished(id) {
  try {
    state.files = await api("/api/scan", post());
    forget(id);
    renderTabs();
    renderList();
    if (state.cur === id) showCurrent();
  } catch (error) { /* the next Scan for new files does it */ }
}
function watchWriting() {
  const check = () => api("/api/files/writing").then((now) => {
    const listed = new Set(state.files.filter((f) => f.status !== "moved").map((f) => f.id));
    const same = Object.keys(now).length === listed.size && Object.keys(now).every((id) => listed.has(id));
    if (!same) refreshList();  // a file arrived in (or left) the input folder since the list was loaded
    for (const [id, busy] of Object.entries(now)) {
      if (writing.get(id) === busy) continue;
      const finished = writing.get(id) === true && !busy;
      writing.set(id, busy);
      if (finished) rescanFinished(id);
      updateRow(id);
      if (state.detail?.id === id) { renderPreview(state.detail); renderFrames(state.detail); }  // the player and frames wait for it
    }
    setLive(true);
  }).catch(() => setLive(false));
  check();
  setInterval(check, 3000);
  setInterval(showWatchStatus, 1000);
}

function row(f) {
  const title = f.identity ? h("span", { class: "t", title: rowTitle(f) }, rowTitle(f)) : h("span", { class: "t none" }, "Not matched yet");
  return h("div", { class: "rv-row", role: "option", "data-id": f.id, onclick: () => select(f.id) },
    h("span", { class: "c-title" }, thumb(f), dot(f), title),
    h("span", { class: "c-now", title: f.path }, basename(f.path), writeState(f)),
    h("span", { class: "c-new" }, proposed(f)),
    h("span", { class: "c-act" }, rowAction(f)));
}

// Redraw one row (its name arrived) without touching the rest of the list or the open detail, and without scrolling.
function updateRow(id) {
  const old = document.querySelector(`.rv-row[data-id="${CSS.escape(id)}"]`), f = state.files.find((x) => x.id === id);
  if (!old || !f) return;
  const fresh = row(f);
  fresh.classList.toggle("active", id === state.cur);
  fresh.setAttribute("aria-selected", String(id === state.cur));
  old.replaceWith(fresh);
  updateAcceptAll();
}

function episodeOrder(f) {
  const ident = f.identity || {};
  return (ident.season ?? 0) * 10000 + (ident.episode ?? 0);
}

function visible() {
  const tab = TABS.find((t) => t.id === state.tab);
  return state.files
    .filter((f) => tab.statuses.includes(f.status) || state.sticky.has(f.id))
    .map((f) => ({ f, g: groupOf(f) }))
    .sort((a, b) => a.g.order - b.g.order || a.g.name.localeCompare(b.g.name) || (a.g.show ?? 0) - (b.g.show ?? 0)
      || episodeOrder(a.f) - episodeOrder(b.f) || rowTitle(a.f).localeCompare(rowTitle(b.f)))
    .map(({ f }) => f);
}

function renderTabs() {
  const count = (tab) => state.files.filter((f) => tab.statuses.includes(f.status)).length;
  const tabs = TABS.map((tab) => h("button", {
    type: "button", role: "tab", class: "rv-tab " + (tab.cls || ""), "aria-selected": String(state.tab === tab.id), onclick: () => setTab(tab.id),
  }, tab.label, h("span", { class: "n" }, count(tab))));
  $("#tabs").replaceChildren(...tabs);
  $("#tabs").hidden = false;
}

// The list is one grid: a row per file (title, current name, proposed name, the button), under a heading per group. The
// selected file's details open in a pane beside it only when asked for (d).
function renderList() {
  const files = visible();
  if (!files.some((f) => f.id === state.cur)) state.cur = files.length ? files[0].id : null;
  const tab = TABS.find((t) => t.id === state.tab);
  const counts = {};
  for (const f of files) counts[groupOf(f).key] = (counts[groupOf(f).key] || 0) + 1;
  const nodes = [];
  if (state.tab === "output" && state.leftovers.length) nodes.push(leftoverPanel());
  if (files.length) {
    nodes.push(h("div", { class: "rv-colhead", "aria-hidden": "true" }, h("span", {}, "Title"), h("span", {}, "Current filename"),
      h("span", {}, state.tab === "output" ? "Moved to" : "Proposed filename"), h("span", {})));
  }
  let group = null;
  for (const f of files) {
    const g = groupOf(f);
    if (g.key !== group) {
      group = g.key;
      const n = counts[g.key];
      nodes.push(h("div", { class: "rv-ghead rv-section-head" }, h("h3", {}, g.title), h("small", {}, `${n} file${n === 1 ? "" : "s"}`)));
    }
    nodes.push(row(f));
  }
  if (!files.length) nodes.push(state.tab === "input" && !state.leftovers.length ? watchPanel() : h("div", { class: "rv-empty" }, h("b", {}, tab.empty[0]), tab.empty[1]));
  $("#results").classList.toggle("is-empty", !files.length);  // nothing to show details of, so the list takes the width
  $("#details-toggle").hidden = !files.length;
  if (files.length && state.tab === "input") nodes.push(h("div", { class: "rv-acceptall" }, h("button", { type: "button", class: "go", onclick: acceptAll })));
  $("#list").replaceChildren(...nodes);
  updateAcceptAll();
  markActive();
}

// The empty Input tab: the input folder is polled every few seconds (watchWriting), so say so, and when it last answered.
const INBOX_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 13h5l1.5 3h5L16 13h5"/><path d="M5.5 5h13L21 13v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-5z"/></svg>';
function watchPanel() {
  const radar = h("div", { class: "rv-radar", "aria-hidden": "true" }, h("span"), h("span"), h("span"));
  radar.insertAdjacentHTML("beforeend", INBOX_ICON);  // static markup, nothing from a file or the server
  const step = (n, text, path) => h("li", {}, h("i", {}, n), h("span", {}, text, path ? h("code", {}, path) : null));
  return h("div", { class: "rv-watch" },
    radar,
    h("h2", {}, "Watching for new videos"),
    h("p", {}, "Drop videos into your input folder. They appear here within a few seconds, identified and ready for you to approve."),
    state.folders.input ? h("code", { class: "rv-watch-path", title: "Input folder" }, state.folders.input) : null,
    h("div", { class: "rv-watch-status", role: "status" }, h("i", { class: "rv-pulse" }), h("span", { id: "watch-ago" }, watchStatus())),
    h("ol", { class: "rv-watch-steps" },
      step("1", "A video lands in the input folder"),
      step("2", "It is matched against TMDB and given a name"),
      step("3", state.folders.output ? "You approve it, and it moves to " : "You approve it, and it moves into your library", state.folders.output)));
}
function watchStatus() {
  if (state.live === false) return "Can't reach tidyarr";
  const ago = Math.round((Date.now() - state.lastCheck) / 1000);
  return !state.lastCheck || ago < 5 ? "Live \u00b7 checked just now" : `Live \u00b7 checked ${ago}s ago`;
}
// The header pill and the empty tab's status line follow every poll, and a once-a-second tick keeps "checked Ns ago" current.
function setLive(live) {
  if (live) state.lastCheck = Date.now();
  state.live = live;
  const pill = $("#live");
  pill.hidden = false;
  pill.classList.toggle("off", !live);
  pill.lastChild.textContent = live ? "Watching" : "Offline";
  pill.title = live ? `Checking ${state.folders.input || "the input folder"} every 3 seconds` : "tidyarr isn't answering. Run it again to carry on.";
  showWatchStatus();
}
function showWatchStatus() {
  const line = $("#watch-ago");
  if (!line) return;
  line.textContent = watchStatus();
  line.parentElement.classList.toggle("off", state.live === false);
}

// The button under the grid counts the rows that can be accepted; it follows each name and download state as they arrive.
const acceptable = () => visible().filter(canRename);
let bulk = null;  // { done, of } while Accept all is working through the list
function updateAcceptAll() {
  const button = $(".rv-acceptall button");
  if (!button) return;
  if (bulk) {
    button.textContent = `Moving ${Math.min(bulk.done + 1, bulk.of)} of ${bulk.of}…`;
    button.disabled = true;
    return;
  }
  const n = acceptable().length;
  button.textContent = n ? `Accept all ${n}` : "Accept all";
  button.disabled = !n;
}

// What the moves left behind in the input folder. Only the junk listed goes, to the system trash, and only on a click.
function leftoverPanel() {
  const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
  return h("div", { class: "rv-leftovers" },
    h("h3", {}, "Left behind in the input folder"),
    state.leftovers.map((l) => h("div", { class: "rv-leftover" },
      h("div", { class: "head" }, h("b", { title: l.folder }, l.folder),
        h("button", { type: "button", onclick: () => cleanLeftovers(l) }, l.junk.length ? `Move ${plural(l.junk.length, "file")} to trash` : "Remove empty folder")),
      l.junk.length ? h("ul", {}, l.junk.map((name) => h("li", {}, name))) : null,
      l.kept.length ? h("p", { class: "kept" }, `Kept, not a junk type: ${l.kept.join(", ")}`) : null)));
}

function cleanLeftovers(l) {
  return act(async () => {
    const done = await api(`/api/leftovers/${l.id}/clean`, post({ junk: l.junk }));
    await refreshList();
    toast(done.trashed ? `Moved ${done.trashed} file${done.trashed === 1 ? "" : "s"} from ${done.folder} to the trash` : `Removed ${done.folder}`);
  });
}

function markActive() {
  for (const row of document.querySelectorAll(".rv-row")) {
    const on = row.dataset.id === state.cur;
    row.classList.toggle("active", on);
    row.setAttribute("aria-selected", String(on));
    if (on) row.scrollIntoView({ block: "nearest" });
  }
}

// The details pane is closed unless asked for, and remembered; closed, nothing is fetched or played for the selected file.
const detailsOpen = () => pref("details");
function setDetails(open) {
  if (open === detailsOpen()) return;
  rememberPosition();
  remember("details", open ? "1" : "0");
  showDetailsPane();
  return showCurrent();
}
function showDetailsPane() {
  const open = detailsOpen();
  $("#results").classList.toggle("with-detail", open);
  $("#detail").hidden = $("#splitter").hidden = !open;
  const toggle = $("#details-toggle");
  toggle.setAttribute("aria-pressed", String(open));
  toggle.firstChild.textContent = open ? "Hide details " : "Show details ";
}

async function refreshList() {
  [state.files, state.leftovers] = await Promise.all([api("/api/files"), api("/api/leftovers")]);
  renderTabs();
  renderList();
}

const tabCursor = {};  // tab id -> the file that was selected when the person left it
function setTab(id) {
  if (id === state.tab) return;
  rememberPosition();  // the player's spot goes with the file, so coming back to this tab resumes it
  tabCursor[state.tab] = state.cur;
  state.tab = id;
  state.sticky.clear();
  state.cur = tabCursor[id] ?? null;  // renderList falls back to the first file if it is no longer listed here
  renderTabs();
  renderList();
  showCurrent();
}

function move(step) {
  const files = visible();
  if (!files.length) return;
  const index = files.findIndex((f) => f.id === state.cur);
  select(files[Math.max(0, Math.min(files.length - 1, index + step))].id);
}

function select(id) {
  if (id === state.cur) return;
  state.cur = id;
  markActive();
  showCurrent();
}

// The file after `ids` in the list as it is now (or the one before, at the end), for the cursor once they leave it.
function nextAfter(ids) {
  const files = visible();
  const last = Math.max(...ids.map((id) => files.findIndex((f) => f.id === id)));
  const rest = (f) => !ids.includes(f.id);
  return (files.slice(last + 1).find(rest) || files.slice(0, last).reverse().find(rest) || {}).id || null;
}

// Files that left this tab; the cursor moves on only if the selected one went (a row's own button can move another file).
async function leave(ids) {
  const was = state.cur;
  const next = ids.includes(was) ? nextAfter(ids) : was;
  ids.forEach((id) => { state.sticky.delete(id); forget(id); });
  await refreshList();
  state.cur = visible().some((f) => f.id === next) ? next : state.cur;
  markActive();
  if (ids.includes(was) || state.cur !== was) showCurrent();
}

// ----- the detail pane ----------------------------------------------------------------------------------------

// Details are fetched once and kept until the file changes, and the next file's is fetched in the background, so moving
// through the list with j/k doesn't wait on TMDB.
const details = new Map();
function fetchDetail(id) {
  if (!details.has(id)) details.set(id, api(`/api/files/${id}`).catch((error) => { details.delete(id); throw error; }));
  return details.get(id);
}
function forget(id) { details.delete(id); dests.delete(id); }

// The latest render, so something that needs the pane drawn (the search box focused) can wait for it rather than redraw it.
let shown = Promise.resolve();
function showCurrent() { shown = renderCurrent(); return shown; }
async function renderCurrent() {
  const id = state.cur;
  if (tip) tip.hidden = true;
  if (!id || !detailsOpen()) {
    state.detail = null;
    $("#detail").replaceChildren(...(detailsOpen() ? [h("div", { class: "rv-empty" }, h("b", {}, "Nothing selected"), "Pick a file, or use j and k.")] : []));
    return;
  }
  const slow = setTimeout(() => { if (state.cur === id) $("#detail").replaceChildren(h("p", { class: "rv-note" }, "Loading…")); }, 150);
  try {
    const d = await fetchDetail(id);
    if (state.cur !== id) return;
    state.detail = d;
    renderDetail(d);
    const files = visible();
    const after = files[files.findIndex((f) => f.id === id) + 1];
    if (after) fetchDetail(after.id).catch(() => {});
  } catch (error) {
    if (state.cur === id) $("#detail").replaceChildren(h("p", { class: "rv-message error" }, error.message));
  } finally {
    clearTimeout(slow);
  }
}

// Genres, age rating, season counts, director/creator and cast of a title, as short lines (empty when TMDB had none of it).
function titleFacts(f) {
  if (!f) return [];
  const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
  const line = [f.certification, f.genres && f.genres.join(", "),
    f.seasons ? `${plural(f.seasons, "season")}${f.episodes ? `, ${plural(f.episodes, "episode")}` : ""}` : null].filter(Boolean).join(" · ");
  const people = (label, names) => (names && names.length ? h("p", { class: "rv-people" }, h("b", {}, label), " ", names.join(", ")) : null);
  return [line ? h("p", { class: "rv-genres" }, line) : null,
    people(f.kind === "tv" ? "Created by" : "Directed by", f.director), people("Starring", f.cast)].filter(Boolean);
}

// Clicking the matched title is how you say "wrong one": it reopens the picker (same as pressing /).
function titleHeading(d) {
  const ident = d.identity;
  const text = [ident.title, h("span", { class: "y" }, yearOf(ident))];
  if (d.status === "moved") return h("h2", {}, ...text);
  const tip = `Wrong ${ident.kind === "movie" ? "movie" : "show"}? Choose a different one`;
  return h("h2", {}, h("button", { type: "button", class: "rv-title-btn", title: tip, "aria-label": tip, onclick: () => changeMatch(d) },
    ...text, h("span", { class: "rv-edit", "aria-hidden": "true" }, "✎")));
}

// For a match, its title (and episode) over the facts about it; with none yet, what to do.
// The row shows the title too, but its column can be scrolled out of sight when the pane is wide.
function heading(d) {
  const ident = d.identity;
  if (ident) {
    return [titleHeading(d),
      ident.kind === "tv" && ident.season != null ? h("p", { class: "rv-sub" }, h("b", {}, `Season ${ident.season}, Episode ${ident.episode}`)) : null,
      ...titleFacts(d.decision && d.decision.facts)].filter(Boolean);
  }
  if (d.candidates.length) return [h("h2", {}, "Which one is this?"), h("p", { class: "rv-sub" }, "Pick the right match below, or search for it.")];
  return [h("h2", {}, "We couldn't tell what this is"), h("p", { class: "rv-sub" }, "It might be a personal video. Search for the title below.")];
}

// The matched title's poster beside the facts; without one they stand alone (and with neither, nothing is shown).
function withPoster(d, ...content) {
  const url = d.decision && d.decision.poster;
  if (!url || !url.startsWith("https://image.tmdb.org/")) return content.length ? h("div", {}, ...content) : null;
  return h("div", { class: "rv-head" }, h("img", { class: "rv-poster", src: url, alt: "", referrerpolicy: "no-referrer" }), h("div", {}, ...content));
}

// The details pane, beside the grid; the selected row already shows the title, both names and the button.
function renderDetail(d) {
  const f = state.files.find((x) => x.id === d.id) || { ...d, source: d.decision && d.decision.source };
  const undecided = !d.decision;
  const [chipClass, chipText] = undecided ? (d.candidates.length ? ["guess", "Best guess needed"] : ["none", "No match yet"]) : confidence(f);
  const kind = d.identity ? (d.identity.kind === "tv" ? "TV episode" : "Movie") : "Not matched";

  const root = $("#detail");
  root.replaceChildren(
    withPoster(d, ...heading(d),
      h("div", { class: "rv-eyebrow rv-tags" }, h("span", { class: "rv-chip " + chipClass }, chipText), h("span", { class: "rv-kind" }, kind))),
  );
  const preview = h("div", { class: "rv-section", id: "preview-section" });
  if (undecided) root.append(preview, finder(d));  // the video above the picker, so it can be watched while choosing
  else root.append(preview);
  root.append(h("div", { class: "rv-section", id: "frames-section" }), h("div", { class: "rv-section", id: "images-section" }),
    h("div", { class: "rv-section", id: "why-section" }),
    h("div", { class: "rv-foot" }, copyButton(d)));  // niche, so last
  renderPreview(d);
  renderFrames(d);
  renderImages(d);
  renderWhy(d);
}

function splitPath(path) {
  const cut = path.lastIndexOf("/") + 1;
  return [h("span", { class: "dir" }, path.slice(0, cut)), h("span", { class: "file" }, path.slice(cut))];
}

const formats = () => ({ tv: $("#tv-format").value, movie: $("#movie-format").value });

function finder(d) {
  const query = h("input", { type: "search", value: d.hints.query, "aria-label": "Search shows and movies", maxlength: "200", id: "query", placeholder: "Search shows and movies" });
  const kindPick = h("select", { "aria-label": "Kind" }, h("option", { value: "tv" }, "TV show"), h("option", { value: "movie" }, "Movie"));
  kindPick.value = d.hints.kind;
  const cands = h("div", { class: "rv-cands" });
  const search = () => act(async () => {
    const result = await api(`/api/files/${d.id}/search`, post({ query: query.value, kind: kindPick.value }));
    showCandidates(cands, result.candidates, d);
    query.blur();
  });
  query.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); search(); } });
  showCandidates(cands, d.candidates, d);
  const length = d.duration ? h("p", { class: "rv-note" }, `This file is ${minutesText(Math.round(d.duration / 60))} long: compare it with the runtimes below.`) : null;
  if (length && d.runtime && d.runtime.ok === false) {
    length.textContent = `This file is ${minutesText(Math.round(d.runtime.actual))} long, but TMDB says the match it found runs ${minutesText(d.runtime.expected)}, so it was not accepted automatically.`;
    length.classList.add("bad");
  }
  return h("div", { class: "rv-section" }, h("h4", {}, d.candidates.length ? "Which one is it?" : "Find the right title"),
    h("div", { class: "rv-search" }, query, kindPick, h("button", { type: "button", onclick: search }, "Search")), length, cands);
}

function showCandidates(box, list, d) {
  state.candidates = list;
  box.replaceChildren(...(list.length ? list.slice(0, 9).map((c, i) => candidateCard(c, i, d)) : [h("p", { class: "rv-note" }, "No matches. Try another search.")]));
}

const runtimeMatch = (r) => Math.max(0, Math.round(100 * (1 - Math.abs(r.actual - r.expected) / r.expected)));
const minutesText = (minutes) => (minutes >= 60 ? `${Math.floor(minutes / 60)}h ${String(minutes % 60).padStart(2, "0")}m` : `${minutes}m`);

function candidateCard(c, index, d) {
  const poster = c.poster && c.poster.startsWith("https://image.tmdb.org/")
    ? h("img", { src: c.poster, alt: "", loading: "lazy" })
    : h("div", { class: "noposter" });
  let check = null;
  if (c.episode) {
    const label = `Season ${c.episode.season}, Episode ${c.episode.episode}`;
    if (c.episode.title) {
      const facts = [c.episode.air_date && `aired ${c.episode.air_date}`, c.episode.runtime && `${c.episode.runtime} min`].filter(Boolean).join(" · ");
      check = h("div", { class: "rv-check yes" }, `✓ ${label} exists: “${c.episode.title}”`, facts ? h("span", { class: "rv-facts" }, ` (${facts})`) : null);
      if (c.episode.overview) check = [check, h("p", { class: "rv-episode" }, c.episode.overview)];
    } else check = h("div", { class: "rv-check no" }, `✗ This show has no ${label}`);
  }
  const link = c.url && c.url.startsWith("https://www.themoviedb.org/")
    ? h("a", { href: c.url, target: "_blank", rel: "noopener noreferrer" }, "View on TMDB ↗") : null;
  const facts = [c.kind === "tv" ? "TV" : "Movie", c.countries.join(", "), c.language && `in ${c.language.toUpperCase()}`,
    c.date && (c.kind === "tv" ? `first aired ${c.date}` : `released ${c.date}`),
    c.runtime && !(c.episode && c.episode.runtime) && `${c.kind === "tv" ? "usually " : ""}${minutesText(c.runtime)}`,
    c.rating != null && `★ ${c.rating.toFixed(1)} (${c.votes.toLocaleString()} votes)`].filter(Boolean).join(" · ");
  return h("div", { class: "rv-cand" }, poster,
    h("span", {}, h("h5", {}, c.title + (c.year ? ` (${c.year})` : "")),
      c.original_title ? h("p", { class: "rv-orig" }, `Original title: ${c.original_title}`) : null,
      h("p", {}, facts), ...titleFacts(c),
      check, c.overview ? h("p", { class: "rv-overview" }, c.overview) : h("p", { class: "rv-note" }, "TMDB has no summary for this one."), link),
    h("button", { type: "button", class: "go", onclick: () => choose(d, c) }, "Accept", index < 9 ? kbd(String(index + 1)) : null));
}

function sectionHead(title, key, label, onclick) {
  return h("h4", {}, title, h("span", { class: "rv-gap" }), h("button", { type: "button", class: "link", onclick }, label, " ", kbd(key)));
}

function renderPreview(d) {
  const box = $("#preview-section");
  if (!box) return;
  const open = pref("preview", "1");  // open until the person closes it
  box.replaceChildren(sectionHead("Preview", "p", open ? "Hide" : "Play", togglePreview));
  if (open && downloading(d.id)) box.append(h("p", { class: "rv-note" }, "Still being written: the preview starts once it's done."));
  else if (open) {
    box.append(h("div", { id: "original" }));
    playOriginal(d);
  }
}

// Still frames (a title card, the credits) to help tell what the video is. Collapsed until opened; ffmpeg runs the first time only.
// Each is shown from a blob: URL (the page may not show data: or other URLs for a new tab) and opens full size in a new tab.
let frameUrls = [];
function frameLink(f) {
  const bytes = Uint8Array.from(atob(f.jpeg), (c) => c.charCodeAt(0));
  const url = URL.createObjectURL(new Blob([bytes], { type: "image/jpeg" }));
  frameUrls.push(url);
  return h("a", { href: url, target: "_blank", rel: "noopener", title: "Open full size in a new tab" },
    h("img", { src: url, alt: `Frame at ${f.at}`, loading: "lazy" }), h("figcaption", {}, f.at));
}

function renderFrames(d) {
  const box = $("#frames-section");
  if (!box) return;
  frameUrls.forEach((url) => URL.revokeObjectURL(url));  // the previous file's pictures; tabs already opened keep theirs
  frameUrls = [];
  const body = h("div", { class: "rv-frames" });
  let loaded = false;
  const section = h("details", { class: "rv-collapse" }, h("summary", {}, "Frames from the video"), body);
  section.addEventListener("toggle", async () => {
    if (!section.open || loaded) return;
    if (downloading(d.id)) return body.replaceChildren(h("p", { class: "rv-note" }, "Still being written: frames can be picked once it's done."));
    loaded = true;
    body.replaceChildren(h("p", { class: "rv-note" }, "Picking frames…"));
    try {
      const frames = await api(`/api/files/${d.id}/frames`);
      if (!body.isConnected) return;
      body.replaceChildren(...(frames.length
        ? frames.map((f) => h("figure", {}, frameLink(f)))
        : [h("p", { class: "rv-note" }, "No usable frames found: the video may be mostly black.")]));
    } catch (error) {
      loaded = false;  // opening it again tries again
      if (body.isConnected) body.replaceChildren(h("p", { class: "rv-note bad" }, `Could not get frames: ${error.message}`));
    }
  });
  box.replaceChildren(section);
}

// TMDB's artwork for the matched title (or, under review, the top suggestion), to compare with the frames. Collapsed until opened.
// The pictures come from image.tmdb.org (the page may show images from there); each opens full size in a new tab.
function renderImages(d) {
  const box = $("#images-section");
  if (!box) return;
  const body = h("div", { class: "rv-images" });
  let loaded = false;
  const section = h("details", { class: "rv-collapse" }, h("summary", {}, "Images from TMDB"), body);
  section.addEventListener("toggle", async () => {
    if (!section.open || loaded) return;
    loaded = true;
    body.replaceChildren(h("p", { class: "rv-note" }, "Fetching images…"));
    try {
      const found = await api(`/api/files/${d.id}/images`);
      if (!body.isConnected) return;
      if (!found.images.length) {
        body.replaceChildren(h("p", { class: "rv-note" }, found.title ? `TMDB has no images for ${found.title}.` : "There is no suggestion yet to show images for."));
        return;
      }
      const note = found.decided ? `Images for ${found.title}` : `Images for the top suggestion, ${found.title}. Pick a match to see its own.`;
      body.replaceChildren(h("p", { class: "rv-note" }, note), ...found.images.map((i) => h("figure", { class: i.label === "Poster" ? "tall" : "" },
        h("a", { href: i.full, target: "_blank", rel: "noopener noreferrer", title: "Open full size in a new tab" },
          h("img", { src: i.thumb, alt: `${i.label} for ${found.title}`, loading: "lazy" })),
        h("figcaption", {}, i.width && i.height ? `${i.label} · ${i.width}×${i.height}` : i.label))));
    } catch (error) {
      loaded = false;  // opening it again tries again
      if (body.isConnected) body.replaceChildren(h("p", { class: "rv-note bad" }, `Could not get images: ${error.message}`));
    }
  });
  box.replaceChildren(section);
}

const COPY_ICON = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
function copyButton(d) {
  const button = h("button", { type: "button", class: "link rv-copy", title: "Copy debug info to the clipboard", onclick: () => copyDebug(d) }, "Copy debug info");
  button.insertAdjacentHTML("afterbegin", COPY_ICON);
  return button;
}

// Plain text about this file (what each source said, ffprobe, the TMDB calls) to paste into a chat when something looks wrong.
async function copyDebug(d) {
  try {
    const { text } = await api(`/api/files/${d.id}/debug`);
    try { await navigator.clipboard.writeText(text); toast("Debug info copied"); }
    catch { window.prompt("Copy this (Ctrl+C), then paste it into the chat:", text); }
  } catch (error) { toast(`Could not build the debug info: ${error.message}`, { error: true }); }
}

function renderWhy(d) {
  const box = $("#why-section");
  if (!box) return;
  const body = h("div", { class: "rv-why" });
  const section = h("details", { class: "rv-collapse" }, h("summary", {}, "Why this match? ", kbd("i")), body);
  section.open = pref("why");
  section.addEventListener("toggle", () => remember("why", section.open ? "1" : "0"));
  box.replaceChildren(section);
  const mark = (agrees) => (agrees === true ? ["yes", "✓"] : agrees === false ? ["no", "✗"] : ["", "–"]);
  const rows = d.clues.map((c) => {
    const [cls, sign] = mark(c.agrees);
    return h("div", { class: "rv-clue" }, h("span", { class: "ic " + cls }, sign), h("span", { class: "src" }, c.source), h("span", { class: "val" }, c.said));
  });
  if (d.runtime) {
    const r = d.runtime, [cls, sign] = mark(r.ok);
    const file = `File is ${minutesText(Math.round(r.actual))}`;
    const text = !r.expected ? `${file} | TMDB doesn't list a runtime for this one`
      : r.estimate ? `${file} | TMDB has no runtime for this episode; ${r.estimate === "season" ? "others in the season run" : "the show's usual episode is"} ${minutesText(r.expected)} (${runtimeMatch(r)}% match)`
      : `${file} | TMDB reports ${minutesText(r.expected)} (${runtimeMatch(r)}% match)`;
    rows.push(h("div", { class: "rv-clue" }, h("span", { class: "ic " + cls }, sign), h("span", { class: "src" }, "length"), h("span", { class: "val" }, text)));
  }
  body.append(h("div", { class: "rv-clues" }, rows));
  const how = { auto: "Matched automatically: the clues were enough on their own.", human: "Chosen by you." };
  const lines = [];
  if (d.decision) lines.push(how[d.decision.source] || "");
  if (d.consensus && d.consensus.spoke) lines.push(`${d.consensus.agreeing} of ${d.consensus.spoke} clues agree with the match.`);
  if (d.runtime && !d.runtime.ok) lines.push("The file's length is far from TMDB's runtime, so it was not matched automatically.");
  if (!d.decision) lines.push("These are what the file itself says. Nothing has been matched yet.");
  body.append(h("p", { class: "rv-summary" }, lines.join(" ")));
  if (!advanced()) return;
  // Details for nerds: the raw evidence grid, how it was worked out step by step, and where it all came from.
  const tech = h("div", { class: "rv-tech" });
  if (d.flow) tech.append(h("div", { class: "rv-flow", id: "flow" }), h("p", { class: "rv-flowhint" }, "Hover a step to see what it returned."));
  const grid = h("div", { class: "wrap" });
  grid.innerHTML = d.grid_html; // server-built, every value escaped; see the note at the top
  tech.append(grid, h("p", { class: "path" }, `source: ${d.path}` + (d.decision ? ` · decided by: ${d.decision.source}` : "")));
  if (d.decision && d.decision.url && d.decision.url.startsWith("https://www.themoviedb.org/")) {
    tech.append(h("a", { href: d.decision.url, target: "_blank", rel: "noopener noreferrer" }, "View on TMDB ↗"));
  }
  body.append(tech);
  if (d.flow) drawFlow(d);
}

function rememberPosition() {  // a re-render drops the player; remember where it was so the next one resumes there
  const player = $("#original movi-player");
  if (player && state.detail && player.currentTime > 0) resume = { id: state.detail.id, at: player.currentTime, paused: player.paused };
}
function togglePreview() {
  rememberPosition();
  remember("preview", pref("preview", "1") ? "0" : "1");
  if (state.detail) renderPreview(state.detail);
}
function toggleWhy() { const section = $("#why-section details"); if (section) section.open = !section.open; }

// ----- actions ------------------------------------------------------------------------------------------------

// One file at a time, by ⏎ on the selected row or by a row's own button, once its new name is known and free.
function approve(id = state.cur) {
  const f = state.files.find((x) => x.id === id);
  if (!f || f.status === "moved") return;
  if (f.status !== "decided") return toast("Pick a match first: press 1–5, or / to search.");
  if (writing.get(id) !== false) return toast("This file is still being written. Accept it once it's done.");
  if (!canRename(f)) return;
  return act(async () => {
    const moved = await api(`/api/files/${id}/approve`, post(formats()));
    state.history.push({ type: "move", ids: [id] });
    await leave([id]);
    toast(`Moved to ${moved.moved_to}`, { undo: true });
  });
}

// Every row whose new name is known and free, by the button under the grid, one after another. If one fails the rest are
// left in the list; those already moved stay moved and are undone together (u).
// The moves themselves take milliseconds, so each row is held for a beat as it goes: done at full speed, it looked like
// nothing had happened. The beat shrinks for a long list so the whole run stays under a few seconds.
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
function markRow(id, cls, text) {
  const el = document.querySelector(`.rv-row[data-id="${CSS.escape(id)}"]`);
  if (!el) return;
  el.classList.add(cls);
  el.querySelector(".c-act").replaceChildren(h("span", { class: "rv-progress" }, text));
  el.scrollIntoView({ block: "nearest" });
}
function acceptAll() {
  const ids = acceptable().map((f) => f.id);
  if (!ids.length) return;
  const beat = Math.max(80, Math.min(350, 4000 / ids.length));
  return act(async () => {
    const moved = [];
    bulk = { done: 0, of: ids.length };
    try {
      for (const id of ids) {
        updateAcceptAll();
        markRow(id, "moving", "Moving…");
        await api(`/api/files/${id}/approve`, post(formats()));
        moved.push(id);
        bulk.done = moved.length;
        markRow(id, "renamed", "✓ Renamed");
        await pause(beat);
      }
    } finally {
      bulk = null;
      if (moved.length) {
        state.history.push({ type: "move", ids: moved });
        await leave(moved);
      } else renderList();  // nothing moved: clear the "Moving…" off the row that failed
    }
    toast(`Moved ${moved.length} file${moved.length === 1 ? "" : "s"} to the output folder`, { undo: true });
  });
}

// Deleting puts the video and its subtitles in the system trash, so it can't be undone here; it is asked about first.
function confirmDelete(f) {
  const dialog = $("#confirm-delete");
  $("#confirm-delete-name").textContent = basename(f.path);
  dialog.returnValue = "";
  dialog.showModal();
  $("#confirm-delete-cancel").focus();  // the safe choice is the one Enter presses
  return new Promise((resolve) => dialog.addEventListener("close", () => resolve(dialog.returnValue === "delete"), { once: true }));
}

async function remove(id) {
  const f = state.files.find((x) => x.id === id);
  if (!f || f.status === "moved" || state.working) return;
  if (!(await confirmDelete(f))) return;
  return act(async () => {
    await api(`/api/files/${id}/delete`, post());
    state.history = state.history.map((step) => ({ ...step, ids: step.ids.filter((x) => x !== id) })).filter((step) => step.ids.length);  // nothing left to undo for it
    await leave([id]);
    toast(`Moved ${basename(f.path)} to the trash`);
  });
}

function restore(id) {
  return act(async () => {
    await api(`/api/files/${id}/restore`, post());
    await leave([id]);
    toast("Put back where it was. It's back in Input.");
  });
}

function choose(d, c) {
  // Picking a match records it straight away; the file stays put in the list so its new name can be checked before ⏎ moves it.
  return act(async () => {
    await api(`/api/files/${d.id}/decide`, post({ kind: c.kind, id: c.id }));
    state.history.push({ type: "decide", ids: [d.id] });
    state.sticky.add(d.id);
    forget(d.id);
    await refreshList();
    await showCurrent();
    toast(`Matched as ${c.title}. Press ⏎ to rename it.`, { undo: true });
  });
}

function changeMatch(d) {
  return act(async () => {
    await api(`/api/files/${d.id}/undo`, post());
    state.history.push({ type: "forget", ids: [d.id], identity: d.identity });
    state.sticky.add(d.id);
    forget(d.id);
    await refreshList();
    await showCurrent();
    const query = $("#query");
    if (query) { query.focus(); query.select(); }
  });
}

function undo() {
  const last = state.history.pop();
  if (!last) return toast("Nothing to undo.");
  return act(async () => {
    for (const id of [...last.ids].reverse()) {
      if (last.type === "move") await api(`/api/files/${id}/restore`, post());
      else if (last.type === "decide") await api(`/api/files/${id}/undo`, post());
      else if (last.type === "forget") await api(`/api/files/${id}/decide`, post({ kind: last.identity.kind, id: last.identity.id }));
      forget(id);
    }
    state.files = await api("/api/files");
    const first = state.files.find((f) => f.id === last.ids[0]);
    state.sticky.clear();
    state.tab = first ? tabFor(first.status) : state.tab;
    state.cur = first ? first.id : null;
    renderTabs();
    renderList();
    await showCurrent();
    toast(last.ids.length > 1 ? `Undone for ${last.ids.length} files` : "Undone");
  });
}

function pick(index) {
  const d = state.detail;
  if (!d || d.decision || !state.candidates || !state.candidates[index]) return;
  choose(d, state.candidates[index]);
}

// "/" opens the picker: in the details pane, which it opens if need be.
async function search() {
  const f = state.files.find((x) => x.id === state.cur);
  if (!f || f.status === "moved") return;
  if (!detailsOpen()) setDetails(true);
  await shown;
  const d = state.detail;
  if (!d || d.id !== f.id) return;
  if (d.decision) return changeMatch(d);
  const query = $("#query");
  if (query) { query.focus(); query.select(); }
}

// ----- flow diagram, hover cards and the player ---------------------------------------------------------------------

// What each step returned, in a card that appears on hover. The HTML is built by the server with every value escaped, like the
// grid. The card stays open while the pointer is on it, so a long output can be scrolled.
let tip = null;
let tipTimer = null;
const TIP_DELAY_MS = 1000; // the pointer has to rest on a step this long before its card opens, so passing over one does nothing
function hideTip() {
  clearTimeout(tipTimer);
  tipTimer = setTimeout(() => { if (tip) tip.hidden = true; }, 250);
}
function showTipSoon(html, anchor) {
  clearTimeout(tipTimer);
  const open = tip && !tip.hidden; // moving from one step to the next while a card is open swaps it at once
  tipTimer = setTimeout(() => showTip(html, anchor), open ? 0 : TIP_DELAY_MS);
}
function showTip(html, anchor) {
  clearTimeout(tipTimer);
  if (!tip) {
    tip = h("div", { class: "rv-tip" });
    tip.addEventListener("mouseenter", () => clearTimeout(tipTimer));
    tip.addEventListener("mouseleave", hideTip);
    document.body.append(tip);
  }
  tip.innerHTML = html;
  tip.hidden = false;
  const box = anchor.getBoundingClientRect();
  const width = Math.min(620, window.innerWidth - 24);
  tip.style.width = `${width}px`;
  tip.style.left = `${Math.max(12, Math.min(box.left + box.width / 2 - width / 2, window.innerWidth - width - 12))}px`;
  const below = window.innerHeight - box.bottom, above = box.top;
  const room = Math.max(below, above) - 16;
  tip.style.maxHeight = `${Math.max(140, room)}px`;
  if (below >= above) { tip.style.top = `${box.bottom + 6}px`; tip.style.bottom = "auto"; }
  else { tip.style.bottom = `${window.innerHeight - box.top + 6}px`; tip.style.top = "auto"; }
}
function attachTips(box, tips) {
  for (const node of box.querySelectorAll("g.node")) {
    const name = (node.id.match(/flowchart-(\w+)-\d+$/) || [])[1];
    if (!name || !tips[name]) continue;
    node.style.cursor = "help";
    node.addEventListener("mouseenter", () => showTipSoon(tips[name], node));
    node.addEventListener("mouseleave", hideTip);
  }
}

let mermaidLoaded = null;
function loadMermaid() {
  // A plain script from our own server (fetched by `make serve`); it sets window.mermaid.
  mermaidLoaded ??= new Promise((resolve, reject) => {
    const script = h("script", { src: "/vendor/mermaid.js" });
    script.onload = () => {
      const dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
      window.mermaid.initialize({ startOnLoad: false, securityLevel: "strict", theme: dark ? "dark" : "default", flowchart: { htmlLabels: true, curve: "basis" } });
      resolve(window.mermaid);
    };
    script.onerror = () => { mermaidLoaded = null; reject(new Error("run `make serve` to fetch Mermaid")); };
    document.head.append(script);
  });
  return mermaidLoaded;
}

let flowCount = 0;
async function drawFlow(d) {
  // How the metadata was built up, tool by tool. The text is built by the server with every label escaped, and Mermaid
  // runs in strict mode, which sanitises what it draws.
  const box = $("#flow");
  try {
    const mermaid = await loadMermaid();
    const { svg } = await mermaid.render(`flow-${d.id}-${++flowCount}`, d.flow);
    if (state.cur !== d.id || !box.isConnected) return;
    box.innerHTML = svg;
    attachTips(box, d.tips);
  } catch (error) {
    if (state.cur === d.id && box.isConnected) box.replaceChildren(h("p", { class: "rv-note" }, `Could not draw the diagram: ${error.message}`));
  }
}

let moviLoaded = null;
let resume = null;  // {id, at, paused}: the position (and whether it was paused) the preview was hidden at
async function playOriginal(d) {
  // The player (5 MB of script + WASM) is loaded the first time a preview is opened. It streams the file by Range requests,
  // sending the token as a header, so nothing is converted and the token stays out of the URL.
  const box = $("#original");
  box.replaceChildren(h("p", { class: "rv-note" }, "Loading the player…"));
  try {
    moviLoaded ??= import("/vendor/movi-element.js");
    await moviLoaded;
    if (state.cur !== d.id || !box.isConnected) return;
    const player = document.createElement("movi-player");
    player.setAttribute("controls", "");
    if (pref("autoplay") && !(resume?.id === d.id && resume.paused)) player.setAttribute("autoplay", ""); // the browser may start it muted; the player offers tap-to-unmute
    player.setAttribute("wasmurl", "/vendor/movi.wasm");
    player.setAttribute("headers", JSON.stringify({ "X-Token": state.token }));
    const volume = parseFloat(remembered("volume"));
    if (volume >= 0 && volume <= 1) player.setAttribute("volume", volume);
    if (pref("muted")) player.setAttribute("muted", "");
    // Save what the person sets. The player may itself mute a muted-autoplay start, so changes are ignored until playback
    // has been running for a moment; after that they are the person's.
    let listening = false;
    player.addEventListener("playing", () => setTimeout(() => { listening = true; }, 1000), { once: true });
    player.addEventListener("volumechange", () => {
      if (!listening) return;
      remember("volume", player.volume);
      remember("muted", player.muted ? "1" : "0");
    });
    // Autoplay is a toggle inside the player's own bar and menu; it applies from the next file that is opened.
    player.addControl({
      id: "autoplay", label: "Autoplay", toggle: true, active: pref("autoplay"), placement: "both", before: "cc", hotkey: "shift+a",
      icon: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>',
      onSelect: (on) => remember("autoplay", on ? "1" : "0"),
    });
    if (resume?.id === d.id) {
      player.setAttribute("startat", resume.at); // the player's own start offset; a seek after loadedmetadata is lost when paused
    }
    resume = null;
    player.setAttribute("src", `/api/files/${d.id}/original`);
    player.className = "rv-original";
    box.replaceChildren(player);
  } catch (error) {
    moviLoaded = null;
    box.replaceChildren(h("p", { class: "rv-note" }, `Could not start the player: ${error.message}`));
  }
}

// ----- keyboard -------------------------------------------------------------------------------------------------

const KEYS = {
  j: () => move(1), ArrowDown: () => move(1), k: () => move(-1), ArrowUp: () => move(-1),
  Enter: approve, u: undo, "/": search, p: togglePreview, i: toggleWhy, d: () => state.cur && setDetails(!detailsOpen()),
  "[": () => setTab(TABS[(TABS.findIndex((t) => t.id === state.tab) + TABS.length - 1) % TABS.length].id),
  "]": () => setTab(TABS[(TABS.findIndex((t) => t.id === state.tab) + 1) % TABS.length].id),
  "?": () => $("#help").showModal(), ",": () => $("#settings-open").click(), r: () => $("#scan").click(),
  // "=" is + without Shift; a disabled button ignores the click, so the size stops at either end.
  "+": () => $("#text-larger").click(), "=": () => $("#text-larger").click(), "-": () => $("#text-smaller").click(),
};

function onKey(event) {
  if (event.metaKey || event.ctrlKey || event.altKey || !state.token) return;
  if (document.querySelector("dialog[open]")) return; // dialogs handle their own keys (Escape closes them)
  const target = event.target;
  if (target.matches("input:not([type=checkbox]), select, textarea")) {
    if (event.key === "Escape") target.blur();
    return;
  }
  if (target.closest("movi-player")) return; // the player has its own keys
  if (event.key === "Enter" && target.matches("button, a")) return; // Enter on a focused button presses that button
  if (/^[1-9]$/.test(event.key)) { event.preventDefault(); pick(Number(event.key) - 1); return; }
  const handler = KEYS[event.key];
  if (handler) { event.preventDefault(); handler(); }
}

// ----- folders, settings and scanning ---------------------------------------------------------------------------

async function loadFolders() {
  const choices = await api("/api/folders");
  $("#tv-format").value = choices.formats.tv_format;
  $("#movie-format").value = choices.formats.movie_format;
  state.defaultFormats = choices.default_formats;
  showKey(choices.tmdb_key);
  $("#remove-empty").checked = choices.remove_empty_folders;
  // The folders are set on the server (settings file or command line); here they are only shown.
  for (const side of ["input", "output"]) $(`#${side}-folder`).textContent = choices[side] || "";
  state.folders = { input: choices.input, output: choices.output };
  $("#group-folders").parentElement.hidden = !choices.input;
}

async function scan() {
  await act(async () => {
    say("Scanning: working out what each file is. This can take a while for a big folder.");
    $("#scan").disabled = true;
    try {
      state.files = await api("/api/scan", post());
    } finally {
      $("#scan").disabled = false;
      say("");
    }
    details.clear();
    clearDests();
    state.history = [];
    state.sticky.clear();
    $("#results").hidden = false;
    state.tab = "input";
    state.cur = null;
    renderTabs();
    renderList();
    await showCurrent();
  });
}

// The field holds the key in use, masked until Show is pressed; it is masked again whenever the dialog opens.
function showKey(key) {
  state.hasKey = Boolean(key.source);
  state.key = key.key || "";
  $("#tmdb-key").value = state.key;
  $("#tmdb-key").placeholder = "32 letters and digits";
  const status = $("#tmdb-key-status");
  status.textContent = !key.source ? "No key yet: TMDB lookups won't work until you add one."
    : key.source === "TMDB_API_KEY" ? "Using the key from the TMDB_API_KEY environment variable, which wins over one saved here."
    : "";
  status.hidden = !status.textContent;
}

function maskKey(masked) {
  $("#tmdb-key").type = masked ? "password" : "text";
  const button = $("#tmdb-key-show");
  button.setAttribute("aria-pressed", String(!masked)); // the CSS shows the eye, or the struck-out eye while the key shows
  button.setAttribute("aria-label", masked ? "Show key" : "Hide key");
  button.title = button.getAttribute("aria-label");
}

let previewTimer;
function updatePreview() {
  // Debounced; the server builds the example path so it matches what a real move would give.
  clearTimeout(previewTimer);
  previewTimer = setTimeout(async () => {
    const root = $("#output-folder").textContent.replace(/\/$/, "");
    try {
      const result = await api("/api/preview", post(formats()));
      for (const kind of ["tv", "movie"]) {
        const slot = $(`#preview-${kind}`), r = result[kind];
        slot.className = r.error ? "error" : "";
        slot.textContent = r.error || `${root}/${r.path}`;
      }
    } catch (error) { /* the preview is a convenience; saving still reports real problems */ }
  }, 150);
}

function settingsSetup() {
  const settings = $("#settings");
  const fields = ["#tv-format", "#movie-format", "#tmdb-key"];
  let before = {}, wasAdvanced = false, wasRemoveEmpty = false;
  // Settings take effect when Done is pressed and the server has saved them; Cancel (or ✕, Escape, a click outside)
  // puts back what was there when it opened.
  $("#settings-open").addEventListener("click", () => {
    before = Object.fromEntries(fields.map((id) => [id, $(id).value]));
    wasAdvanced = advanced();
    wasRemoveEmpty = $("#remove-empty").checked;
    $("#settings-error").hidden = true;
    maskKey(true);
    settings.returnValue = "";
    settings.showModal();
    updatePreview();
  });
  for (const id of ["#tv-format", "#movie-format"]) $(id).addEventListener("input", updatePreview);
  $("#formats-reset").addEventListener("click", () => { // like any edit here, kept only when Done is pressed
    $("#tv-format").value = state.defaultFormats.tv_format;
    $("#movie-format").value = state.defaultFormats.movie_format;
    updatePreview();
  });
  // A placeholder chip goes into its field where the cursor is (a field keeps that while unfocused), as if typed there;
  // or it can be dragged to the spot, which the browser inserts like any dropped text (and fires "input" for the preview).
  for (const chips of document.querySelectorAll(".rv-tokens")) {
    const field = $(`#${chips.dataset.for}`);
    for (const chip of chips.children) chip.draggable = true;
    chips.addEventListener("dragstart", (event) => {
      event.dataTransfer.setData("text/plain", event.target.textContent);
      event.dataTransfer.effectAllowed = "copy";
    });
    chips.addEventListener("click", (event) => {
      const chip = event.target.closest("button");
      if (!chip) return;
      field.focus();
      field.setRangeText(chip.textContent, field.selectionStart ?? field.value.length, field.selectionEnd ?? field.value.length, "end");
      field.dispatchEvent(new Event("input"));
    });
  }
  for (const field of [$("#tv-format"), $("#movie-format")]) {
    field.addEventListener("dragenter", () => field.classList.add("drop"));
    for (const type of ["dragleave", "drop"]) field.addEventListener(type, () => field.classList.remove("drop"));
  }
  $("#tmdb-key-show").addEventListener("click", () => maskKey($("#tmdb-key").type === "text"));
  $("#folders").addEventListener("submit", async (event) => {
    event.preventDefault(); // the dialog stays open until the save has worked, so a refused key can be corrected
    $("#settings-done").disabled = true;
    try {
      const saved = await api("/api/settings", post({ tv_format: $("#tv-format").value, movie_format: $("#movie-format").value, tmdb_api_key: $("#tmdb-key").value.trim() === state.key ? "" : $("#tmdb-key").value,
        remove_empty_folders: $("#remove-empty").checked }));
      showKey(saved.tmdb_key);
      settings.close("done");
    } catch (error) {
      $("#settings-error").textContent = error.message; // a toast would sit behind the dialog
      $("#settings-error").hidden = false;
    } finally {
      $("#settings-done").disabled = false;
    }
  });
  settings.addEventListener("close", () => {
    if (settings.returnValue !== "done") {
      fields.forEach((id) => { $(id).value = before[id]; });
      $("#advanced").checked = wasAdvanced;
      $("#remove-empty").checked = wasRemoveEmpty;
      updatePreview();
      return;
    }
    if (advanced() !== wasAdvanced) applyAdvanced();
    clearDests(); // the new names follow the formats
    renderList();
  });
  for (const id of ["#settings-cancel", "#settings-x"]) $(id).addEventListener("click", () => settings.close());
  for (const dialog of [settings, $("#help"), $("#confirm-delete")]) {
    dialog.addEventListener("click", (event) => { if (event.target === dialog || event.target.closest("[data-close]")) dialog.close(); }); // the backdrop, or ✕
  }
}

// While the page is open it pings the server: one started in the background stops a couple of minutes after the
// last page goes. If it has stopped (or was restarted, with a new token), say so instead of letting every button fail.
function keepAlive() {
  setInterval(() => api("/api/ping").catch(() => { setLive(false); say("tidyarr has stopped. Run it again to carry on.", true); }), 30000);
}

// Only a server started with `make dev` has /api/dev-version. The page reloads itself when its answer changes: the
// server restarted (Python changed) or a file in web/ changed. While the server is restarting the polls just fail.
async function devReload() {
  const version = () => api("/api/dev-version").then(JSON.stringify, () => null);
  const first = await version();
  if (!first) return;
  setInterval(async () => {
    const now = await version();
    if (now && now !== first) location.reload();
  }, 1000);
}

async function start() {
  // The token arrives in the URL fragment, which is then cleared from the address bar and history; this tab keeps it
  // in sessionStorage so a refresh still works. A restarted server opens a new address, whose token replaces it.
  const match = location.hash.match(/token=([\w-]+)/);
  try {
    if (match) sessionStorage.setItem("token", match[1]);
    state.token = match ? match[1] : sessionStorage.getItem("token");
  } catch {
    state.token = match ? match[1] : null;
  }
  history.replaceState(null, "", location.pathname); // keep the token out of the address bar and history
  if (!state.token) {
    say("Run tidyarr (or `make serve`) to open this page: the address it opens includes the access token.", true);
    return;
  }
  keepAlive();
  watchWriting();
  detailSplitter();
  devReload();
  $("#advanced").checked = pref("advanced");
  $("#stats").hidden = !advanced();
  showDetailsPane();
  $("#details-toggle").addEventListener("click", () => setDetails(!detailsOpen()));
  $("#scan").addEventListener("click", scan);
  $("#toast-undo").addEventListener("click", undo);
  document.addEventListener("keydown", onKey);
  settingsSetup();
  textSizeSetup();
  await act(loadFolders);
  loadStats();
  if (state.hasKey === false) $("#settings-open").click(); // nothing can be looked up without one
  scan();
}

start();
