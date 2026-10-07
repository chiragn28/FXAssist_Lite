// FXAssist Lite web page (ADR-027). Plain JavaScript, no build step.
// Every piece of text from the server is inserted with textContent, never as HTML (SAF-09).
"use strict";

const MAX_QUESTION = 1000;
const STAGES = {
  queued: "Waiting for a free slot",
  guard: "Checking the question",
  retrieve: "Searching the documents",
  grade: "Picking the relevant excerpts",
  rewrite: "Rephrasing the search",
  generate: "Writing the answer",
  validate: "Checking citations and numbers",
  abstain: "Deciding there is no answer",
};
const OUTCOME_LABELS = {
  abstained: "No answer in the documents",
  out_of_scope: "Outside what these documents cover",
  clarify: "Please be more specific",
  declined_advice: "Not personal advice",
  refused: "Refused",
  error: "Something went wrong",
};

const $ = (id) => document.getElementById(id);
const chat = $("chat");
const form = $("ask-form");
const questionBox = $("question");
const sendButton = $("send");
const note = $("composer-note");
const keyDialog = $("key-dialog");
const keyInput = $("key-input");
const imageInput = $("image-input");
const attachment = $("attachment");
const attachmentThumb = $("attachment-thumb");
const attachmentStatus = $("attachment-status");
const ocrText = $("ocr-text");

let image = null; // { file, url, reading: Promise, text }
let busy = false;

// --- API key: this tab only ---------------------------------------------------------------

function getKey() {
  try { return sessionStorage.getItem("fxa_key") || ""; } catch { return ""; }
}
function setKey(value) {
  try { sessionStorage.setItem("fxa_key", value); } catch { /* storage blocked: ask again */ }
}
function askForKey() {
  keyInput.value = getKey();
  keyDialog.showModal();
  keyInput.focus();
}
$("key-button").addEventListener("click", askForKey);
keyDialog.addEventListener("close", () => {
  if (keyDialog.returnValue === "save") setKey(keyInput.value.trim());
  updateKeyButton();
});
function updateKeyButton() {
  $("key-button").textContent = getKey() ? "API key ✓" : "API key";
}
updateKeyButton();

// --- Composer ------------------------------------------------------------------------------

function setNote(text, isError = false) {
  note.textContent = text;
  note.classList.toggle("error", isError);
}

function autosize() {
  questionBox.style.height = "auto";
  questionBox.style.height = `${Math.min(questionBox.scrollHeight, 180)}px`;
}
questionBox.addEventListener("input", autosize);
questionBox.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    form.requestSubmit();
  }
});

for (const button of document.querySelectorAll(".example")) {
  button.addEventListener("click", () => {
    questionBox.value = button.textContent;
    autosize();
    form.requestSubmit();
  });
}

// --- Images: attach, paste or drop; text is read on the server -----------------------------

const IMAGE_TYPES = ["image/png", "image/jpeg", "image/webp"];
const MAX_IMAGE_BYTES = 5_000_000;

function clearImage() {
  if (image) URL.revokeObjectURL(image.url);
  image = null;
  attachment.hidden = true;
  ocrText.hidden = true;
  ocrText.value = "";
  imageInput.value = "";
}
$("attachment-remove").addEventListener("click", clearImage);

function attachImage(file) {
  if (!IMAGE_TYPES.includes(file.type)) {
    setNote("Attach a PNG, JPEG or WebP image.", true);
    return;
  }
  if (file.size > MAX_IMAGE_BYTES) {
    setNote("That image is larger than 5 MB.", true);
    return;
  }
  if (!getKey()) {
    askForKey();
    return;
  }
  clearImage();
  setNote("");
  const url = URL.createObjectURL(file);
  image = { file, url, text: "" };
  attachmentThumb.src = url;
  attachment.hidden = false;
  attachmentStatus.textContent = "Reading text from the image…";
  image.reading = readImage(image);
}

async function readImage(current) {
  try {
    const response = await fetch("/v1/ocr", {
      method: "POST",
      headers: { Authorization: `Bearer ${getKey()}`, "Content-Type": current.file.type },
      body: current.file,
    });
    const body = await response.json().catch(() => ({}));
    if (image !== current) return; // removed or replaced meanwhile
    if (!response.ok) {
      attachmentStatus.textContent = errorText(response.status, body);
      if (response.status === 401) askForKey();
      return;
    }
    current.text = body.text || "";
    if (!current.text) {
      attachmentStatus.textContent = "No text found in this image. It will not be sent.";
      return;
    }
    attachmentStatus.textContent = body.truncated
      ? "Text read (shortened to fit). Edit it if needed:"
      : "Text read from the image. Edit it if needed:";
    ocrText.value = current.text;
    ocrText.hidden = false;
  } catch {
    if (image === current) attachmentStatus.textContent = "Could not reach the server.";
  }
}

imageInput.addEventListener("change", () => {
  if (imageInput.files[0]) attachImage(imageInput.files[0]);
});
document.addEventListener("paste", (e) => {
  const file = [...(e.clipboardData?.files || [])].find((f) => f.type.startsWith("image/"));
  if (file) {
    e.preventDefault();
    attachImage(file);
  }
});
document.addEventListener("dragover", (e) => {
  e.preventDefault();
  document.body.classList.add("dragging");
});
document.addEventListener("dragleave", () => document.body.classList.remove("dragging"));
document.addEventListener("drop", (e) => {
  e.preventDefault();
  document.body.classList.remove("dragging");
  const file = e.dataTransfer?.files?.[0];
  if (file) attachImage(file);
});

// --- Messages ------------------------------------------------------------------------------

function addMessage(role) {
  $("welcome")?.remove();
  const node = $("message-template").content.firstElementChild.cloneNode(true);
  node.classList.add(role);
  chat.append(node);
  return node.querySelector(".bubble");
}

function scrollDown() {
  window.scrollTo({ top: document.body.scrollHeight, behavior: "smooth" });
}

function errorText(status, body) {
  const message = body?.message || body?.error?.message;
  if (status === 401) return "The API key is missing or not valid.";
  if (status === 429) return message || "Too many requests. Wait a moment and try again.";
  const code = body?.code || body?.error?.code;
  if (code === "ocr_unavailable") return "Reading images is not available on this server.";
  return message || `The server answered with an error (${status}).`;
}

function renderAnswer(bubble, body) {
  bubble.replaceChildren();
  bubble.className = `bubble ${body.outcome || ""}`;
  if (OUTCOME_LABELS[body.outcome]) {
    const label = document.createElement("strong");
    label.textContent = OUTCOME_LABELS[body.outcome];
    bubble.append(label, document.createElement("br"));
  }
  const sourceIds = new Map();
  (body.citations || []).forEach((c) => sourceIds.set(c.label, `src-${body.request_id}-${c.label}`));

  const text = document.createElement("div");
  text.className = "answer-text";
  const parts = (body.answer || "").split(/(\[S\d+(?:\s*,\s*S\d+)*\])/g);
  for (const part of parts) {
    const match = part.match(/^\[(S\d+(?:\s*,\s*S\d+)*)\]$/);
    if (!match) {
      text.append(document.createTextNode(part));
      continue;
    }
    for (const label of match[1].split(/\s*,\s*/)) {
      const link = document.createElement("a");
      link.className = "cite";
      link.textContent = label;
      const target = sourceIds.get(label);
      if (target) {
        link.href = `#${target}`;
        link.addEventListener("click", (e) => {
          e.preventDefault();
          const item = document.getElementById(target);
          item?.scrollIntoView({ behavior: "smooth", block: "center" });
          item?.classList.remove("flash");
          void item?.offsetWidth;
          item?.classList.add("flash");
        });
      }
      text.append(link);
    }
  }
  bubble.append(text);

  if (body.citations?.length) {
    const sources = document.createElement("div");
    sources.className = "sources";
    const heading = document.createElement("h3");
    heading.textContent = "Sources";
    const list = document.createElement("ol");
    for (const c of body.citations) {
      const item = document.createElement("li");
      item.id = sourceIds.get(c.label);
      const tag = document.createElement("span");
      tag.className = "cite";
      tag.textContent = c.label;
      const link = document.createElement("a");
      link.textContent = `${c.publisher}: ${c.title}${c.page ? `, page ${c.page}` : ""}`;
      if (/^https?:\/\//.test(c.url || "")) {
        link.href = c.url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
      }
      item.append(tag, link);
      list.append(item);
    }
    sources.append(heading, list);
    bubble.append(sources);
  }
  if (body.disclaimer) {
    const disclaimer = document.createElement("div");
    disclaimer.className = "disclaimer";
    disclaimer.textContent = body.disclaimer;
    bubble.append(disclaimer);
  }
  const meta = document.createElement("div");
  meta.className = "meta";
  meta.textContent = [body.model, body.cached ? "from cache" : null, body.request_id]
    .filter(Boolean)
    .join(" · ");
  bubble.append(meta);
}

function renderError(bubble, status, body) {
  bubble.replaceChildren();
  bubble.className = "bubble error";
  bubble.textContent = errorText(status, body);
}

// --- Streaming (SSE over fetch, so the API key can go in a header) ------------------------

async function* sseEvents(response) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let cut;
    while ((cut = buffer.indexOf("\n\n")) >= 0) {
      const block = buffer.slice(0, cut);
      buffer = buffer.slice(cut + 2);
      let name = "message";
      const data = [];
      for (const line of block.split("\n")) {
        if (line.startsWith("event:")) name = line.slice(6).trim();
        else if (line.startsWith("data:")) data.push(line.slice(5).trim());
      }
      if (data.length) yield [name, JSON.parse(data.join("\n"))];
    }
  }
}

function composeQuestion(question) {
  const extra = image && !ocrText.hidden ? ocrText.value.trim() : "";
  if (!extra) return { text: question, trimmed: false };
  const prefix = `${question}\n\nText from my screenshot:\n`;
  const room = MAX_QUESTION - prefix.length;
  if (room < 40) return { text: question, trimmed: true };
  return { text: prefix + extra.slice(0, room), trimmed: extra.length > room };
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  if (busy) return;
  const question = questionBox.value.trim();
  if (!question) {
    setNote("Type a question first.", true);
    return;
  }
  if (!getKey()) {
    askForKey();
    return;
  }
  if (image?.reading) await image.reading;
  const { text, trimmed } = composeQuestion(question);

  busy = true;
  sendButton.disabled = true;
  setNote(trimmed ? "The image text was shortened to fit the 1,000-character limit." : "");

  const mine = addMessage("user");
  mine.textContent = question;
  if (image) {
    const thumb = document.createElement("img");
    thumb.src = image.url;
    thumb.alt = "Attached image";
    const attached = document.createElement("span");
    attached.className = "attached-note";
    attached.textContent = text !== question ? "Image text sent with the question" : "Image attached (no text found)";
    mine.append(thumb, attached);
    image = null; // the message keeps the thumbnail; the composer starts empty
    attachment.hidden = true;
    ocrText.hidden = true;
    ocrText.value = "";
    imageInput.value = "";
  }
  questionBox.value = "";
  autosize();

  const bubble = addMessage("assistant");
  const progress = document.createElement("div");
  progress.className = "progress";
  bubble.append(progress);
  scrollDown();

  try {
    const response = await fetch("/v1/ask", {
      method: "POST",
      headers: {
        Authorization: `Bearer ${getKey()}`,
        "Content-Type": "application/json",
        Accept: "text/event-stream",
      },
      body: JSON.stringify({ question: text, stream: true }),
    });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      renderError(bubble, response.status, body);
      if (response.status === 401) askForKey();
      return;
    }
    for await (const [name, data] of sseEvents(response)) {
      if (name === "status") {
        progress.querySelector(".current")?.classList.remove("current");
        const step = document.createElement("div");
        step.className = "step current";
        step.textContent = STAGES[data.stage] || data.stage;
        progress.append(step);
        scrollDown();
      } else if (name === "answer") {
        renderAnswer(bubble, data);
      } else if (name === "error") {
        renderError(bubble, data.status, data);
      }
    }
  } catch {
    renderError(bubble, 0, { message: "Could not reach the server, or the connection was lost." });
  } finally {
    busy = false;
    sendButton.disabled = false;
    scrollDown();
    questionBox.focus();
  }
});
