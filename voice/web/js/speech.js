// Speaking and listening for the home screen: browser text-to-speech for every reply and alert,
// and Chrome's speech recognition for commands (the wake word "Alexa", or Space / the mic button).

const SPEECH_TEXT = {
  start: "Start: sound and mic",
  on: "Sound and mic on (click to turn off)",
  clickToResume: "Click anywhere to turn sound back on",
  noVoices: "No voices in this browser",
  noListening: "This browser can't listen: use Chrome",
  micBlocked: "Microphone blocked: allow it in the address bar",
  needsInternet: "Listening needs internet",
  speechFailed: "Couldn't speak",
};

const WAKE_WORD = /\b(?:alexa|alexis|alexia|elexa|lexa)\b[\s,.!?]*/i;
const COMMAND_WINDOW_MS = 10000;
const SPEECH_TIMEOUT_MS = 20000;
const PROBE_MS = 1500;
const STORE_KEY = "meanwhile.sound";

const speech = {
  soundOn: false,
  listening: false,
  awaitingCommand: false,
  commandTimer: null,
  speaking: 0,
  queue: Promise.resolve(),
  recognizer: null,
  handlers: { status: () => {}, interim: () => {}, command: () => {}, awaiting: () => {}, wake: () => {}, ignored: () => {} },
};

// Register callbacks: status(text, problem), interim(text), command(text), awaiting(bool), wake(), ignored(text).
speech.on = function (handlers) {
  Object.assign(speech.handlers, handlers);
};

// Remember a preference; storage may be unavailable.
function saveSound(on) {
  try { localStorage.setItem(STORE_KEY, on ? "on" : "off"); } catch (err) {}
}

// The remembered preference.
function savedSound() {
  try { return localStorage.getItem(STORE_KEY) === "on"; } catch (err) { return false; }
}

// Turn on sound and the microphone; must run inside a click or key press.
speech.enable = function () {
  if (!window.speechSynthesis) {
    speech.handlers.status(SPEECH_TEXT.noVoices, true);
    return;
  }
  speechSynthesis.cancel();
  const warmUp = new SpeechSynthesisUtterance(" ");
  warmUp.volume = 0;
  speechSynthesis.speak(warmUp);
  speech.soundOn = true;
  saveSound(true);
  speech.handlers.status(SPEECH_TEXT.on, false);
  startListening();
};

// Turn sound and the microphone off.
speech.disable = function () {
  speech.soundOn = false;
  speech.listening = false;
  saveSound(false);
  closeCommand();
  if (window.speechSynthesis) speechSynthesis.cancel();
  if (speech.recognizer) {
    try { speech.recognizer.stop(); } catch (err) {}
    speech.recognizer = null;
  }
  speech.handlers.status(SPEECH_TEXT.start, false);
};

// On page load: if sound was on before, try to resume it; if the browser needs a click first, wait for one.
speech.resume = function () {
  if (!savedSound()) {
    speech.handlers.status(SPEECH_TEXT.start, false);
    return;
  }
  probeSpeech().then((allowed) => {
    if (allowed) {
      speech.soundOn = true;
      speech.handlers.status(SPEECH_TEXT.on, false);
      startListening();
      return;
    }
    speech.handlers.status(SPEECH_TEXT.clickToResume, true);
    const once = () => {
      document.removeEventListener("pointerdown", once, true);
      document.removeEventListener("keydown", once, true);
      speech.enable();
    };
    document.addEventListener("pointerdown", once, true);
    document.addEventListener("keydown", once, true);
  });
};

// Whether the page may speak without a click (true when Chrome runs with a no-gesture autoplay policy).
function probeSpeech() {
  return new Promise((resolve) => {
    if (!window.speechSynthesis) return resolve(false);
    const probe = new SpeechSynthesisUtterance(" ");
    probe.volume = 0;
    let done = false;
    const finish = (ok) => { if (!done) { done = true; resolve(ok); } };
    probe.onstart = () => finish(true);
    probe.onend = () => finish(true);
    probe.onerror = () => finish(false);
    speechSynthesis.speak(probe);
    setTimeout(() => { speechSynthesis.cancel(); finish(false); }, PROBE_MS);
  });
}

// Say a line after anything already queued; resolves when it ends. Without sound, resolves after a short pause.
speech.say = function (text) {
  speech.queue = speech.queue.then(() => sayNow(text));
  return speech.queue;
};

// Speak one line now and report failures on the status line.
function sayNow(text) {
  return new Promise((resolve) => {
    if (!speech.soundOn || !window.speechSynthesis || !text) {
      setTimeout(resolve, 1500);
      return;
    }
    if (!speechSynthesis.getVoices().length) speech.handlers.status(SPEECH_TEXT.noVoices, true);
    const line = new SpeechSynthesisUtterance(text);
    let finished = false;
    const finish = () => {
      if (finished) return;
      finished = true;
      speech.speaking -= 1;
      resolve();
    };
    line.onend = finish;
    line.onerror = (e) => {
      if (e.error !== "interrupted" && e.error !== "canceled") speech.handlers.status(`${SPEECH_TEXT.speechFailed}: ${e.error}`, true);
      finish();
    };
    speech.speaking += 1;
    speechSynthesis.speak(line);
    setTimeout(finish, SPEECH_TIMEOUT_MS);
  });
}

// Push-to-talk: stop talking and listen for a command without the wake word; pressed again while listening, stop listening.
speech.pushToTalk = function () {
  if (!speech.soundOn) speech.enable();
  if (speech.awaitingCommand) {
    closeCommand();
    return;
  }
  speech.stop();
  awaitCommand();
};

// Listen for an answer without the wake word, as after a question.
speech.listenForAnswer = function () {
  if (speech.listening) awaitCommand();
};

// Stop the line being spoken now; lines queued after it still play.
speech.stop = function () {
  if (speech.speaking > 0 && window.speechSynthesis) speechSynthesis.cancel();
};

// Open (or extend) the window in which the next sentence counts as a command.
function awaitCommand() {
  speech.awaitingCommand = true;
  speech.handlers.awaiting(true);
  clearTimeout(speech.commandTimer);
  speech.commandTimer = setTimeout(closeCommand, COMMAND_WINDOW_MS);
}

// Close the command window.
function closeCommand() {
  clearTimeout(speech.commandTimer);
  if (!speech.awaitingCommand) return;
  speech.awaitingCommand = false;
  speech.handlers.awaiting(false);
}

// Start continuous recognition; it restarts itself whenever Chrome stops it.
function startListening() {
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Recognition) {
    speech.handlers.status(SPEECH_TEXT.noListening, true);
    return;
  }
  if (speech.recognizer) return;
  const rec = new Recognition();
  rec.continuous = true;
  rec.interimResults = true;
  rec.lang = "en-US";
  rec.onresult = onResult;
  rec.onerror = (e) => {
    if (e.error === "not-allowed" || e.error === "service-not-allowed") {
      speech.handlers.status(SPEECH_TEXT.micBlocked, true);
      speech.listening = false;
    } else if (e.error === "network") {
      speech.handlers.status(SPEECH_TEXT.needsInternet, true);
    }
  };
  rec.onend = () => {
    if (speech.listening) setTimeout(() => { try { rec.start(); } catch (err) {} }, 250);
  };
  speech.recognizer = rec;
  speech.listening = true;
  try { rec.start(); } catch (err) {}
}

// Handle recognised speech: while speaking, only "Alexa, ..." counts (it interrupts); otherwise catch the wake word
// or a push-to-talk command.
function onResult(event) {
  for (let i = event.resultIndex; i < event.results.length; i++) {
    const text = event.results[i][0].transcript.trim();
    if (speech.speaking > 0) {
      if (event.results[i].isFinal && WAKE_WORD.test(text)) speech.stop();
      else continue;
    }
    if (!event.results[i].isFinal) {
      if (WAKE_WORD.test(text) && !speech.awaitingCommand) {
        speech.handlers.wake();
        awaitCommand();
      } else if (speech.awaitingCommand) {
        awaitCommand();
      }
      if (speech.awaitingCommand) speech.handlers.interim(text);
      continue;
    }
    const wake = text.search(WAKE_WORD);
    if (wake >= 0) {
      speech.handlers.wake();
      const command = text.slice(wake).replace(WAKE_WORD, "").trim();
      if (/[a-z]/i.test(command)) sendCommand(command);
      else awaitCommand();
    } else if (speech.awaitingCommand && /[a-z]/i.test(text)) {
      sendCommand(text);
    } else if (text) {
      speech.handlers.ignored(text);
    }
  }
}

// Hand a command to the panel and close the command window.
function sendCommand(text) {
  closeCommand();
  speech.handlers.command(text);
}
