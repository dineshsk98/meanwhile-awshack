// The simulated Alexa+ panel: shows listening / thinking / speaking, sends what was heard (or typed) to /voice/ask,
// speaks the reply, and lists each MCP tool call it made.

const PANEL_TEXT = {
  idle: "Say “Alexa, …” or press Space",
  listening: "Listening…",
  thinking: "Thinking…",
  speaking: "Speaking",
  wait: "One moment.",
  tooSlow: "Sorry, that's taking too long. Could you try again?",
  unreachable: "Sorry, I can't reach Meanwhile right now.",
  ignored: "Heard, not for me:",
  placeholder: "Or type to Alexa",
  noTool: "no tool",
};

const WAIT_LINE_MS = 2500;
const ASK_TIMEOUT_MS = 45000;
const IGNORED_SHOWN_MS = 4000;
const MAX_CALLS = 4;

const panel = {
  state: "idle",
  busy: false,
  next: null,
  request: null,
  ignoredTimer: null,
};

// Show a state on the ring and its label.
function setState(state, detail) {
  panel.state = state;
  $("alexa-panel").dataset.state = state;
  $("alexa-state").textContent = detail || PANEL_TEXT[state];
}

// Send one sentence to the simulated Alexa, show and speak the answer. A new sentence while thinking replaces the
// pending one; while speaking, it waits its turn.
async function askAlexa(text) {
  if (!text.trim()) return;
  if (panel.busy) {
    panel.next = text;
    if (panel.state === "thinking") cancelThinking();
    return;
  }
  panel.busy = true;
  closeReplay();
  $("alexa-heard").textContent = `“${text}”`;
  $("alexa-reply").textContent = "";
  const request = { controller: new AbortController(), cancelled: false, started: Date.now() };
  panel.request = request;
  setState("thinking");
  const tick = setInterval(() => {
    setState("thinking", `${PANEL_TEXT.thinking} ${Math.round((Date.now() - request.started) / 1000)} s`);
  }, 1000);
  const waitLine = setTimeout(() => speech.say(PANEL_TEXT.wait), WAIT_LINE_MS);
  const timeout = setTimeout(() => request.controller.abort(), ASK_TIMEOUT_MS);
  let answer;
  try {
    answer = await api.ask(text, request.controller.signal);
  } catch (err) {
    const timedOut = err.name === "AbortError" && !request.cancelled;
    answer = { reply: timedOut ? PANEL_TEXT.tooSlow : PANEL_TEXT.unreachable, tool: null, error: String(err.message || err) };
  } finally {
    clearInterval(tick);
    clearTimeout(waitLine);
    clearTimeout(timeout);
    panel.request = null;
  }
  if (request.cancelled) {
    finishTurn(false);
    return;
  }
  $("alexa-reply").textContent = answer.reply;
  addCall(answer);
  setState("speaking");
  await speech.say(answer.reply);
  finishTurn(answer.reply.trim().endsWith("?"));
}

// Drop the question being thought about (its answer is ignored) and listen again.
function cancelThinking() {
  if (!panel.request) return;
  panel.request.cancelled = true;
  panel.request.controller.abort();
  speech.stop();
}

// End a turn: back to idle, then run a sentence that came in meanwhile, or listen for an answer after a question.
function finishTurn(askedQuestion) {
  setState(speech.awaitingCommand ? "listening" : "idle");
  panel.busy = false;
  const next = panel.next;
  panel.next = null;
  if (next) askAlexa(next);
  else if (askedQuestion) speech.listenForAnswer();
}

// Show, faintly and briefly, something heard that wasn't for Alexa, so a missed wake word is visible.
function showIgnored(text) {
  $("alexa-ignored").textContent = `${PANEL_TEXT.ignored} “${text}”`;
  clearTimeout(panel.ignoredTimer);
  panel.ignoredTimer = setTimeout(() => { $("alexa-ignored").textContent = ""; }, IGNORED_SHOWN_MS);
}

// Add a card for the tool call: name, arguments, where the words came from, and timings.
function addCall(answer) {
  const li = document.createElement("li");
  const tool = answer.tool;
  const name = document.createElement("code");
  name.textContent = tool ? `${tool.name}(${formatArgs(tool.arguments)})` : PANEL_TEXT.noTool;
  if ((tool && tool.error) || answer.error) li.classList.add("failed");
  const t = answer.timings || {};
  const meta = document.createElement("span");
  meta.className = "meta";
  meta.textContent = [
    t.route_ms != null && `chose ${seconds(t.route_ms)}`,
    t.tool_ms != null && `MCP ${seconds(t.tool_ms)}`,
    t.phrase_ms != null && `words ${seconds(t.phrase_ms)} (${answer.reply_from})`,
    answer.error,
  ].filter(Boolean).join(" · ");
  li.append(name, meta);
  const list = $("alexa-calls");
  list.prepend(li);
  while (list.children.length > MAX_CALLS) list.lastChild.remove();
}

// Tool arguments as a short readable list.
function formatArgs(args) {
  return Object.entries(args || {}).map(([k, v]) => `${k}: ${JSON.stringify(v)}`).join(", ");
}

// Milliseconds as "1.2 s".
function seconds(ms) {
  return `${(ms / 1000).toFixed(1)} s`;
}

// Wire the panel to speech, the ring, Space and the text box.
function startPanel() {
  $("alexa-input").placeholder = PANEL_TEXT.placeholder;
  setState("idle");
  speech.on({
    interim: (text) => { if (panel.state !== "speaking") setState("listening", text); },
    awaiting: (on) => {
      if (on) setState("listening");
      else if (panel.state === "listening") setState(panel.busy ? "thinking" : "idle");
    },
    wake: () => {
      if (panel.state === "thinking") cancelThinking();
    },
    ignored: (text) => showIgnored(text),
    command: (text) => askAlexa(text),
  });
  $("alexa-mic").addEventListener("click", () => speech.pushToTalk());
  document.addEventListener("keydown", (e) => {
    if (e.code !== "Space" || e.repeat || e.target.closest("input, textarea, button")) return;
    e.preventDefault();
    speech.pushToTalk();
  });
  $("alexa-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const text = $("alexa-input").value;
    $("alexa-input").value = "";
    askAlexa(text);
  });
}

startPanel();
