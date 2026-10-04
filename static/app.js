// HandNotes workspace: page queue, live transcription, corrections, insights and study tools.

const UNSURE_WORD = /(\S+)\s*\[\?\]/g;
const UNSURE_MARK = /\s*\[\?\]/g;
const STATUS_POLL_MS = 15000;
const TOAST_MS = 4200;
const LINE_HEIGHT_PX = 28;
const MISREADS_SHOWN = 8;

let state = {
  pages: [], currentId: null, memory: null, summary: "", cards: [], busy: false, modelName: "the model",
};
let nextPageId = 1;
let tickTimer = null;

const $ = (selector) => document.querySelector(selector);

// ---------- State helpers (state is replaced, never mutated) ----------
function setState(patch) { state = { ...state, ...patch }; }
function findPage(id) { return state.pages.find((page) => page.id === id) || null; }
function currentPage() { return findPage(state.currentId); }
function updatePage(id, patch) {
  setState({ pages: state.pages.map((page) => (page.id === id ? { ...page, ...patch } : page)) });
}
function readyPages() { return state.pages.filter((p) => p.status === "review" || p.status === "learned"); }

// ---------- Text helpers ----------
function escapeHtml(text) {
  const map = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  return String(text).replace(/[&<>"']/g, (c) => map[c]);
}
function highlightUnsure(text) {
  return escapeHtml(text).replace(UNSURE_WORD, '<mark title="The model was unsure about this word">$1</mark>');
}
function cleanText(text) { return text.replace(UNSURE_MARK, ""); }
function percent(value) { return `${Math.round(value * 100)}%`; }
function pairChip([wrong, right], count) {
  const times = count ? `<span class="count">×${count}</span>` : "";
  return `<span class="chip"><span class="wrong">${escapeHtml(wrong)}</span>→<span class="right">${escapeHtml(right)}</span>${times}</span>`;
}

function toast(message, kind = "") {
  const element = document.createElement("div");
  element.className = `toast ${kind}`;
  element.textContent = message;
  $("#toasts").append(element);
  setTimeout(() => element.remove(), TOAST_MS);
}

// ---------- API ----------
async function errorMessage(response) {
  try {
    const body = await response.json();
    return typeof body.detail === "string" ? body.detail : `Request failed (${response.status})`;
  } catch (error) {
    return `Request failed (${response.status})`;
  }
}

async function postJson(url, body) {
  const response = await fetch(url, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(await errorMessage(response));
  return response;
}

function handleStreamEvent(line, onToken) {
  if (!line.trim()) return null;
  const event = JSON.parse(line);
  if (event.error) throw new Error(event.error);
  if (event.token) onToken(event.token);
  return event.done ? event : null;
}

async function streamTranscription(file, useMemory, onToken) {
  const form = new FormData();
  form.append("image", file);
  form.append("use_memory", String(useMemory));
  const response = await fetch("/api/transcribe", { method: "POST", body: form });
  if (!response.ok) throw new Error(await errorMessage(response));

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let final = null;
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop();
    for (const line of lines) final = handleStreamEvent(line, onToken) || final;
  }
  final = handleStreamEvent(buffer, onToken) || final;
  if (!final) throw new Error("The model stopped before finishing. Try again.");
  return final;
}

// ---------- Pages & queue ----------
function addFiles(fileList) {
  const files = [...fileList];
  const images = files.filter((file) => /^image\/(jpeg|png|webp)$/.test(file.type));
  if (images.length < files.length) toast("Skipped files that aren't JPG, PNG or WebP.", "bad");
  if (!images.length) return;

  const newPages = images.map((file) => ({
    id: nextPageId++, file, name: file.name, url: URL.createObjectURL(file), status: "queued",
    streamText: "", modelText: "", corrected: "", autoFixes: [], learned: null,
    plainText: null, comparing: false, error: "", startedAt: 0,
  }));
  setState({ pages: [...state.pages, ...newPages], currentId: state.currentId ?? newPages[0].id });
  renderAll();
  processQueue();
}

async function processQueue() {
  if (state.busy) return;
  const next = state.pages.find((page) => page.status === "queued");
  if (!next) return;
  setState({ busy: true });
  await readPage(next.id);
  setState({ busy: false });
  processQueue();
}

async function readPage(id) {
  updatePage(id, { status: "reading", streamText: "", error: "", startedAt: Date.now(), learned: null, plainText: null });
  renderPages();
  if (state.currentId === id) renderEditor();
  startTicker();
  try {
    const final = await streamTranscription(findPage(id).file, true, (token) => {
      updatePage(id, { streamText: findPage(id).streamText + token });
      if (state.currentId === id) paintStream(findPage(id));
    });
    updatePage(id, { status: "review", modelText: final.text, corrected: final.text, autoFixes: final.auto_fixes });
  } catch (error) {
    updatePage(id, { status: "error", error: error.message });
  } finally {
    stopTicker();
  }
  renderPages();
  if (state.currentId === id) renderEditor();
}

function requeue(id) {
  updatePage(id, { status: "queued" });
  renderPages();
  renderEditor();
  processQueue();
}

function startTicker() {
  stopTicker();
  tickTimer = setInterval(() => {
    const page = currentPage();
    const element = $("#elapsed");
    if (page && element && page.status === "reading") {
      element.textContent = `${Math.round((Date.now() - page.startedAt) / 1000)}s`;
    }
  }, 1000);
}
function stopTicker() { clearInterval(tickTimer); tickTimer = null; }

// ---------- Learning & comparing ----------
async function saveAndLearn(id) {
  const page = findPage(id);
  const corrected = cleanText(page.corrected).trim();
  if (!corrected) { toast("The transcription is empty.", "bad"); return; }
  try {
    const response = await postJson("/api/learn", { model_text: page.modelText, corrected });
    const body = await response.json();
    updatePage(id, { status: "learned", corrected, learned: { accuracy: body.accuracy, pairs: body.pairs } });
    setState({ memory: body.memory });
    renderAll();
    toast(`Learned from ${page.name}. The model got ${percent(body.accuracy)} of words right.`);
  } catch (error) {
    toast(error.message, "bad");
  }
}

async function comparePlain(id) {
  if (!state.memory || !state.memory.history.length) {
    toast("Correct at least one page first, so there's memory to compare against.", "bad");
    return;
  }
  updatePage(id, { comparing: true });
  renderEditor();
  try {
    const final = await streamTranscription(findPage(id).file, false, () => {});
    updatePage(id, { plainText: final.text, comparing: false });
  } catch (error) {
    updatePage(id, { comparing: false });
    toast(error.message, "bad");
  }
  if (state.currentId === id) renderEditor();
}

function wordDiff(before, after) {
  const a = before.split(/\s+/).filter(Boolean);
  const b = after.split(/\s+/).filter(Boolean);
  const table = Array.from({ length: a.length + 1 }, () => new Uint16Array(b.length + 1));
  for (let i = a.length - 1; i >= 0; i--) {
    for (let j = b.length - 1; j >= 0; j--) {
      table[i][j] = a[i] === b[j] ? table[i + 1][j + 1] + 1 : Math.max(table[i + 1][j], table[i][j + 1]);
    }
  }
  const out = [];
  let i = 0;
  let j = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) { out.push({ type: "same", word: a[i] }); i++; j++; }
    else if (table[i + 1][j] >= table[i][j + 1]) out.push({ type: "del", word: a[i++] });
    else out.push({ type: "ins", word: b[j++] });
  }
  while (i < a.length) out.push({ type: "del", word: a[i++] });
  while (j < b.length) out.push({ type: "ins", word: b[j++] });
  return out;
}

// ---------- Rendering: pages ----------
const STATUS_LABELS = {
  queued: ["", "Waiting"], reading: ["busy", "Reading…"], review: ["", "Needs a check"], error: ["bad", "Failed"],
};

function pageStateLabel(page) {
  if (page.status === "learned") return `<span class="dot ok"></span>Learned · ${percent(page.learned.accuracy)}`;
  const [dot, label] = STATUS_LABELS[page.status];
  return `<span class="dot ${dot}"></span>${label}`;
}

function renderPages() {
  $("#page-count").textContent = state.pages.length ? `${state.pages.length} page${state.pages.length > 1 ? "s" : ""}` : "";
  $("#page-list").innerHTML = state.pages.map((page) => `
    <li><button class="page-item ${page.id === state.currentId ? "active" : ""}" data-page="${page.id}">
      <img src="${page.url}" alt="">
      <span class="meta"><span class="name">${escapeHtml(page.name)}</span><span class="state">${pageStateLabel(page)}</span></span>
    </button></li>`).join("");
}

// ---------- Rendering: editor ----------
function emptyStateHtml() {
  return `<div class="card empty">
    <img class="emoji-lg" src="/static/emoji/writing.png" alt="">
    <h2>Start with his worst page</h2>
    <p>Add a photo of handwritten notes. It's read by ${escapeHtml(state.modelName)} on this laptop, and nothing is uploaded anywhere.</p>
    <p class="hint">Tip: you can also paste a photo with <kbd>Ctrl</kbd> + <kbd>V</kbd>.</p>
  </div>`;
}

function readingHtml() {
  return `<div class="transcript-head">
      <span class="eyebrow">Transcription</span>
      <span class="pill"><span class="spinner"></span>Reading with ${escapeHtml(state.modelName)} on this laptop · <span id="elapsed">0s</span></span>
    </div>
    <div class="stream" id="stream"></div>
    <p class="hint">Words it isn't sure about are <mark>highlighted</mark>. Pages usually take 30 to 60 seconds on a laptop GPU.</p>`;
}

function unsureHtml(text) {
  const words = [...text.matchAll(UNSURE_WORD)].map((match) => match[1]);
  if (!words.length) return "";
  const chips = words.map((word, index) =>
    `<button class="chip button" data-action="jump" data-index="${index}"><mark>${escapeHtml(word)}</mark></button>`).join("");
  return `<div class="notice warn"><b>${words.length} word${words.length > 1 ? "s" : ""} it wasn't sure about.</b> Click to jump: <span class="chips">${chips}</span></div>`;
}

function autoFixHtml(page) {
  if (!page.autoFixes.length) return "";
  return `<div class="notice info"><b>Fixed from his memory:</b> <span class="chips">${page.autoFixes.map((pair) => pairChip(pair)).join("")}</span></div>`;
}

function learnedHtml(page) {
  if (!page.learned) return "";
  const pairs = page.learned.pairs.length
    ? `<span class="chips">${page.learned.pairs.map((pair) => pairChip(pair)).join("")}</span>`
    : "No word swaps to learn from this page.";
  return `<div class="notice good"><b>Learned from this page · ${percent(page.learned.accuracy)} of words were right.</b> ${pairs}</div>`;
}

function compareHtml(page) {
  if (page.comparing) {
    return `<div class="notice info"><span class="spinner"></span>Reading the page again with the plain model (no memory)…</div>`;
  }
  if (page.plainText === null) return "";
  const diff = wordDiff(page.plainText, page.modelText);
  const changed = diff.filter((part) => part.type === "ins").length;
  const body = diff.map((part) => {
    const word = escapeHtml(part.word);
    if (part.type === "ins") return `<ins>${word}</ins>`;
    if (part.type === "del") return `<del>${word}</del>`;
    return word;
  }).join(" ");
  const title = changed ? `His memory changed ${changed} word${changed > 1 ? "s" : ""} on this page.` : "His memory made no difference on this page.";
  return `<div><div class="panel-title"><h2>Plain model vs. with his memory</h2></div>
    <p class="hint" style="margin-bottom:8px">${title} <del>Struck</del> = plain model, <ins>green</ins> = with memory.</p>
    <div class="diff">${body}</div></div>`;
}

function reviewHtml(page) {
  const learned = page.status === "learned";
  return `<div class="transcript-head">
      <span class="eyebrow">Transcription</span>
      <span class="pill"><span class="dot ${learned ? "ok" : ""}"></span>${learned ? "Saved to memory" : "Check and correct"}</span>
    </div>
    ${learnedHtml(page)}${autoFixHtml(page)}
    <div id="unsure">${unsureHtml(page.corrected)}</div>
    <label class="sr-only" for="edit-text">Transcription</label>
    <textarea class="edit" id="edit-text" spellcheck="true">${escapeHtml(page.corrected)}</textarea>
    <div class="actions">
      <button class="btn btn-primary" data-action="save" ${learned ? "disabled" : ""}>${learned ? "Learned ✓" : "Save & learn"}</button>
      <button class="btn btn-ghost" data-action="compare" ${page.comparing ? "disabled" : ""}>Compare with plain model</button>
      <button class="btn btn-ghost" data-action="reread">Read again</button>
      ${learned ? "" : '<span class="hint"><kbd>Ctrl</kbd> + <kbd>Enter</kbd> to save</span>'}
    </div>
    ${compareHtml(page)}`;
}

function transcriptHtml(page) {
  if (page.status === "queued") {
    return `<div class="notice info"><span class="spinner"></span>Waiting for the page before it to finish…</div>`;
  }
  if (page.status === "reading") return readingHtml();
  if (page.status === "error") {
    return `<div class="notice bad"><b>Couldn't read this page.</b> ${escapeHtml(page.error)}</div>
      <div class="actions"><button class="btn btn-primary" data-action="reread">Try again</button></div>`;
  }
  return reviewHtml(page);
}

function paintStream(page) {
  const element = $("#stream");
  if (element) element.innerHTML = `${highlightUnsure(page.streamText)}<span class="caret"></span>`;
}

function renderEditor() {
  const main = $("#editor");
  const page = currentPage();
  if (!page) { main.innerHTML = emptyStateHtml(); return; }
  main.innerHTML = `<div class="editor">
      <div class="card viewer"><div class="viewer-frame" id="viewer" title="Click to zoom">
        <img src="${page.url}" alt="Photo of ${escapeHtml(page.name)}"></div></div>
      <div class="card transcript">${transcriptHtml(page)}</div>
    </div>`;
  if (page.status === "reading") paintStream(page);
}

// ---------- Rendering: insights ----------
function chartSvg(history) {
  if (!history.length) return '<p class="muted" style="font-size:14px">Correct your first page to start the chart.</p>';
  const W = 280, H = 140, L = 34, R = 12, T = 12, B = 24;
  const values = history.map((session) => session.accuracy * 100);
  const low = Math.max(0, Math.floor((Math.min(...values) - 5) / 10) * 10);
  const x = (i) => (values.length === 1 ? (L + W - R) / 2 : L + (i * (W - L - R)) / (values.length - 1));
  const y = (v) => T + ((100 - v) * (H - T - B)) / (100 - low || 1);
  const points = values.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`);
  const area = `M${x(0)},${H - B} L${points.join(" L")} L${x(values.length - 1)},${H - B} Z`;
  const grid = [low, (low + 100) / 2, 100].map((v) =>
    `<line class="axis" x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"/><text x="${L - 6}" y="${y(v) + 4}" text-anchor="end">${Math.round(v)}%</text>`).join("");
  const dots = values.map((v, i) =>
    `<circle class="pt" cx="${x(i)}" cy="${y(v)}" r="4"><title>Page ${i + 1}: ${v.toFixed(0)}%</title></circle>`).join("");
  const labels = values.map((_, i) =>
    (i === 0 || i === values.length - 1 || values.length <= 8)
      ? `<text x="${x(i)}" y="${H - 6}" text-anchor="middle">p${i + 1}</text>` : "").join("");
  const label = `Accuracy per page: ${values.map((v) => `${v.toFixed(0)}%`).join(", ")}`;
  return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${label}">${grid}
    <path class="area" d="${area}"/><polyline class="line" points="${points.join(" ")}"/>${dots}${labels}</svg>`;
}

function kpisHtml(memory) {
  const history = memory.history;
  const last = history.length ? percent(history.at(-1).accuracy) : "–";
  const change = history.length > 1 ? Math.round((history.at(-1).accuracy - history[0].accuracy) * 100) : null;
  const changeHtml = change === null ? "–" : `<span class="${change >= 0 ? "delta-up" : ""}">${change >= 0 ? "+" : ""}${change} pts</span>`;
  return `<div class="kpi"><div class="value">${last}</div><div class="label">last page</div></div>
    <div class="kpi"><div class="value">${changeHtml}</div><div class="label">since page 1</div></div>
    <div class="kpi"><div class="value">${history.length}</div><div class="label">pages learned</div></div>
    <div class="kpi"><div class="value">${memory.vocabulary_count}</div><div class="label">words learned</div></div>`;
}

function timeAgo(isoTimestamp) {
  const minutes = Math.max(1, Math.round((Date.now() - new Date(isoTimestamp)) / 60000));
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  return hours < 24 ? `${hours} h ago` : `${Math.round(hours / 24)} d ago`;
}

function renderModelCard(model) {
  const card = $("#model-card");
  card.hidden = !model;
  if (!model) return;
  card.innerHTML = `<div class="panel-title"><h2>Personal model</h2><span class="pill"><span class="dot ok"></span>Up to date</span></div>
    <div class="model-name">${escapeHtml(model.name)}</div>
    <p class="hint">Fine-tuned from ${escapeHtml(model.base)} on his handwriting</p>
    <dl class="model-stats">
      <div><dt>Word samples</dt><dd>${model.samples.toLocaleString()}</dd></div>
      <div><dt>Pages</dt><dd>${model.pages}</dd></div>
      <div><dt>Accuracy gain</dt><dd class="delta-up">+${Math.round(model.gain * 100)} pts</dd></div>
      <div><dt>Last trained</dt><dd>${timeAgo(model.trained)}</dd></div>
    </dl>`;
}

function renderLibraryCard(samples) {
  const card = $("#library-card");
  card.hidden = !samples;
  if (!samples) return;
  const tiles = samples.words.map((word) =>
    `<figure class="sample"><div class="sample-ink">${escapeHtml(word)}</div><figcaption>${escapeHtml(word)}</figcaption></figure>`).join("");
  card.innerHTML = `<div class="panel-title"><h2>His handwriting library</h2><span class="muted" style="font-size:13px">${samples.count.toLocaleString()} words</span></div>
    <p class="hint" style="margin-bottom:12px">Every corrected word is cut out of the page and stored with its label, so the model learns how he shapes each letter.</p>
    <div class="sample-grid">${tiles}</div>`;
}

function renderInsights() {
  const memory = state.memory || { history: [], vocabulary_count: 0, top_misreads: [] };
  $("#chart").innerHTML = chartSvg(memory.history);
  $("#kpis").innerHTML = kpisHtml(memory);
  $("#misreads").innerHTML = memory.top_misreads.length
    ? memory.top_misreads.slice(0, MISREADS_SHOWN).map((entry) => pairChip([entry.wrong, entry.right], entry.count)).join("")
    : '<p class="muted" style="font-size:14px">Misreads you correct will collect here.</p>';
  renderModelCard(memory.personal_model);
  renderLibraryCard(memory.samples);
}

function renderAll() { renderPages(); renderEditor(); renderInsights(); }

// ---------- Study tools ----------
function combinedText(withHeadings) {
  return readyPages()
    .map((page, index) => (withHeadings ? `Page ${index + 1}\n${cleanText(page.corrected)}` : cleanText(page.corrected)))
    .join("\n\n");
}

function inlineMarkdown(text) { return text.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>"); }

function renderMarkdown(markdown) {
  let html = "";
  let list = null;
  const closeList = () => { if (list) { html += `</${list}>`; list = null; } };
  for (const raw of escapeHtml(markdown).split("\n")) {
    const line = raw.trim();
    const item = line.match(/^(?:[-*•]|\d+[.)])\s+(.*)/);
    const heading = line.match(/^#{1,4}\s+(.*)/);
    if (item) {
      const tag = /^\d/.test(line) ? "ol" : "ul";
      if (list !== tag) { closeList(); html += `<${tag}>`; list = tag; }
      html += `<li>${inlineMarkdown(item[1])}</li>`;
    } else {
      closeList();
      if (heading) html += `<h3>${inlineMarkdown(heading[1])}</h3>`;
      else if (line) html += `<p>${inlineMarkdown(line)}</p>`;
    }
  }
  closeList();
  return html;
}

async function withBusyButton(button, busyLabel, work) {
  const label = button.textContent;
  button.disabled = true;
  button.innerHTML = `<span class="spinner"></span>${busyLabel}`;
  try { await work(); } catch (error) { toast(error.message, "bad"); }
  finally { button.disabled = false; button.textContent = label; }
}

function requireText() {
  const text = combinedText(false);
  if (!text.trim()) toast("Read at least one page first.", "bad");
  return text.trim() ? text : null;
}

async function makeSummary(button) {
  const text = requireText();
  if (!text) return;
  await withBusyButton(button, "Summarizing…", async () => {
    const body = await (await postJson("/api/summarize", { text })).json();
    setState({ summary: body.summary });
    $("#summary").innerHTML = renderMarkdown(body.summary);
  });
}

async function makeCards(button) {
  const text = requireText();
  if (!text) return;
  await withBusyButton(button, "Making cards…", async () => {
    const body = await (await postJson("/api/flashcards", { text })).json();
    setState({ cards: body.cards });
    $("#cards").innerHTML = body.cards.length
      ? body.cards.map((card) => `<button class="flashcard" aria-label="Flashcard. Click to flip."><div class="flashcard-inner">
          <div class="flashcard-face"><span class="eyebrow">Question</span><p class="q">${escapeHtml(card.question)}</p><span class="hint">Click to reveal</span></div>
          <div class="flashcard-face back"><span class="eyebrow">Answer</span><p>${escapeHtml(card.answer)}</p><span class="hint">Click to flip back</span></div>
        </div></button>`).join("")
      : '<p class="muted">The model couldn\'t make cards from these notes. Try again.</p>';
  });
}

async function exportNotes(format, button) {
  const text = combinedText(true);
  if (!text.trim()) { toast("Read at least one page first.", "bad"); return; }
  const title = $("#export-title").value.trim() || "notes";
  const summary = $("#export-summary").checked ? state.summary : "";
  await withBusyButton(button, "…", async () => {
    const blob = await (await postJson(`/api/export/${format}`, { title, text, summary })).blob();
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `${title.replace(/[^\w\- ]/g, "") || "notes"}.${format}`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
  });
}

// ---------- Status & memory ----------
async function refreshStatus() {
  const element = $("#status");
  try {
    const status = await (await fetch("/api/status")).json();
    const isFirstStatus = state.modelName !== status.model;
    setState({ modelName: status.model });
    if (isFirstStatus && !currentPage()) renderEditor();
    if (!status.ok) {
      element.innerHTML = `<span class="dot bad"></span>${escapeHtml(status.message || "Ollama is not running")}`;
      return;
    }
    const where = status.loaded
      ? (status.gpu_percent !== null ? ` · ${status.gpu_percent}% on GPU` : "")
      : " · loads on first page";
    element.innerHTML = `<span class="dot ${state.busy ? "busy" : "ok"}"></span>${escapeHtml(status.model)} · on this laptop${where}`;
  } catch (error) {
    element.innerHTML = '<span class="dot bad"></span>HandNotes server is not running';
  }
}

async function loadMemory() {
  try {
    const response = await fetch("/api/memory");
    if (!response.ok) throw new Error(await errorMessage(response));
    setState({ memory: await response.json() });
  } catch (error) {
    toast(`Couldn't load memory: ${error.message}`, "bad");
  }
  renderInsights();
}

// ---------- Events ----------
function jumpToUnsure(index) {
  const textarea = $("#edit-text");
  const match = [...textarea.value.matchAll(UNSURE_WORD)][index];
  if (!match) return;
  textarea.focus();
  textarea.setSelectionRange(match.index, match.index + match[0].length);
  const lineNumber = textarea.value.slice(0, match.index).split("\n").length;
  textarea.scrollTop = Math.max(0, (lineNumber - 3) * LINE_HEIGHT_PX);
}

function onEditorClick(event) {
  if (event.target.closest("#viewer")) { $("#viewer").classList.toggle("zoomed"); return; }
  const button = event.target.closest("[data-action]");
  const page = currentPage();
  if (!button || !page) return;
  const actions = {
    save: () => saveAndLearn(page.id),
    compare: () => comparePlain(page.id),
    reread: () => requeue(page.id),
    jump: () => jumpToUnsure(Number(button.dataset.index)),
  };
  actions[button.dataset.action]?.();
}

function onEditorInput(event) {
  if (event.target.id !== "edit-text") return;
  const page = currentPage();
  updatePage(page.id, { corrected: event.target.value });
  $("#unsure").innerHTML = unsureHtml(event.target.value);
}

function onEditorKeydown(event) {
  if (event.target.id !== "edit-text" || event.key !== "Enter" || !(event.ctrlKey || event.metaKey)) return;
  event.preventDefault();
  const page = currentPage();
  if (page && page.status === "review") saveAndLearn(page.id);
}

function bindDropzone() {
  const zone = $("#dropzone");
  $("#file-input").addEventListener("change", (event) => { addFiles(event.target.files); event.target.value = ""; });
  zone.addEventListener("dragover", (event) => { event.preventDefault(); zone.classList.add("drag"); });
  zone.addEventListener("dragleave", () => zone.classList.remove("drag"));
  zone.addEventListener("drop", (event) => {
    event.preventDefault();
    zone.classList.remove("drag");
    addFiles(event.dataTransfer.files);
  });
  document.addEventListener("paste", (event) => {
    if (event.clipboardData?.files.length) addFiles(event.clipboardData.files);
  });
}

function bindTabs() {
  document.querySelectorAll(".tab").forEach((tab) => tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((other) => other.setAttribute("aria-selected", String(other === tab)));
    document.querySelectorAll(".tab-panel").forEach((panel) => { panel.hidden = panel.dataset.panel !== tab.dataset.tab; });
  }));
}

function bindStudyTools() {
  $("#summary-btn").addEventListener("click", (event) => makeSummary(event.currentTarget));
  $("#cards-btn").addEventListener("click", (event) => makeCards(event.currentTarget));
  $("#cards").addEventListener("click", (event) => event.target.closest(".flashcard")?.classList.toggle("flipped"));
  document.querySelectorAll("[data-export]").forEach((button) =>
    button.addEventListener("click", () => exportNotes(button.dataset.export, button)));
}

function init() {
  bindDropzone();
  bindTabs();
  bindStudyTools();
  const editor = $("#editor");
  editor.addEventListener("click", onEditorClick);
  editor.addEventListener("input", onEditorInput);
  editor.addEventListener("keydown", onEditorKeydown);
  $("#page-list").addEventListener("click", (event) => {
    const item = event.target.closest("[data-page]");
    if (!item) return;
    setState({ currentId: Number(item.dataset.page) });
    renderPages();
    renderEditor();
  });
  window.addEventListener("beforeunload", (event) => {
    if (state.pages.some((page) => page.status === "review" || page.status === "reading")) event.preventDefault();
  });
  renderAll();
  loadMemory();
  refreshStatus();
  setInterval(refreshStatus, STATUS_POLL_MS);
}

init();
