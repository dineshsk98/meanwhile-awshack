// Calls from the home screen to the devserver's /voice API.

const api = {
  // The home screen's state: session, card text, events, cameras and what to display.
  async state() {
    const reply = await fetch("/voice/state", { cache: "no-store" });
    if (!reply.ok) throw new Error(`/voice/state ${reply.status}`);
    return reply.json();
  },

  // Ask the simulated Alexa one sentence (abortable); returns what it heard, the tool call, the reply and timings.
  async ask(text, signal) {
    const reply = await fetch("/voice/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
      signal,
    });
    const body = await reply.json().catch(() => ({}));
    if (!reply.ok) throw new Error(body.message || `/voice/ask ${reply.status}`);
    return body;
  },
};
