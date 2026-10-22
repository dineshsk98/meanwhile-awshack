// The home screen: polls /voice/state every second and renders the session card,
// plays "show me" clips (and shows "look" frames) in the replay window, and raises and speaks (through speech.js) each alert once.

const POLL_MS = 1000;
const BANNER_MS = 15000;
const MAX_EVENTS = 8;

// Shorthand for document.getElementById.
const $ = (id) => document.getElementById(id);

const home = {
  first: true,
  seenAlerts: new Set(),
  pending: [],
  showing: false,
  hideTimer: null,
  displayAt: null,
  clips: [],
  clipIndex: 0,
  labels: {},
  times: {},
};

// Poll the devserver forever, one request at a time.
async function poll() {
  try {
    const state = await api.state();
    $("offline").hidden = true;
    render(state);
  } catch (err) {
    $("offline").hidden = false;
  }
  setTimeout(poll, POLL_MS);
}

// Draw one state snapshot and react to new alerts and display commands.
function render(state) {
  home.labels = Object.fromEntries(state.cameras.map((c) => [c.camera_id, c.label]));
  home.times = Object.fromEntries(state.events.map((e) => [e.event_id, e.time]));
  renderCameras(state.cameras);
  renderCard(state.card, state.events);
  checkAlerts(state.events);
  checkDisplay(state.display);
  home.first = false;
}

// The camera strip: each label with an online dot.
function renderCameras(cameras) {
  const list = $("cameras");
  list.replaceChildren(...cameras.map((c) => {
    const li = document.createElement("li");
    li.textContent = c.label;
    li.className = c.online ? "online" : "offline";
    return li;
  }));
}

// The session card: heading, what was said, what Meanwhile understood, the state line and the latest events.
function renderCard(card, events) {
  $("card-heading").textContent = card.heading || "";
  $("card-said").textContent = card.said || "";
  $("card-understood").textContent = card.understood || "";
  $("card-state").textContent = card.state || "";
  $("card-empty").textContent = events.length ? "" : card.empty || "";
  const playing = home.clips[home.clipIndex]?.event_id;
  $("events").replaceChildren(...events.slice(-MAX_EVENTS).map((e) => {
    const li = document.createElement("li");
    li.dataset.eventId = e.event_id;
    li.className = [e.banner?.tone, e.event_id === playing ? "playing" : ""].filter(Boolean).join(" ");
    li.append(cell("time", e.time), cell("camera", e.camera), cell("summary", e.summary));
    return li;
  }));
}

// A <span> with a class and text.
function cell(className, text) {
  const span = document.createElement("span");
  span.className = className;
  span.textContent = text;
  return span;
}

// Queue a banner for every alert event not seen before; alerts already there on page load count as seen.
function checkAlerts(events) {
  for (const e of events) {
    if (!e.banner || home.seenAlerts.has(e.event_id)) continue;
    home.seenAlerts.add(e.event_id);
    if (!home.first) home.pending.push(e);
  }
  if (!home.showing) nextBanner();
}

// Show the next queued banner and speak it; move on when the speech ends.
function nextBanner() {
  const e = home.pending.shift();
  if (!e) {
    home.showing = false;
    return;
  }
  home.showing = true;
  const banner = $("banner");
  banner.className = `banner ${e.banner.tone}`;
  banner.dataset.eventId = e.event_id;
  banner.querySelector(".banner-title").textContent = e.banner.title;
  banner.querySelector(".banner-text").textContent = e.banner.say;
  banner.hidden = false;
  clearTimeout(home.hideTimer);
  home.hideTimer = setTimeout(() => { if (!home.showing) banner.hidden = true; }, BANNER_MS);
  speech.say(e.banner.say).then(() => setTimeout(nextBanner, 1000));
}

// Start a new replay whenever the display command changes.
function checkDisplay(display) {
  if (display.at === home.displayAt) return;
  home.displayAt = display.at;
  if (display.cmd !== "show" || !display.clips.length) {
    closeReplay();
    return;
  }
  home.clips = display.clips;
  home.clipIndex = 0;
  playClip();
}

// Play the current clip (or show a still frame, from "look") in the replay window, with its time, camera and
// summary underneath.
function playClip() {
  const clip = home.clips[home.clipIndex];
  if (!clip) return;
  const video = $("replay-video");
  const image = $("replay-image");
  video.hidden = !clip.clip_url;
  image.hidden = !!clip.clip_url;
  if (clip.clip_url) {
    image.removeAttribute("src");
    video.src = clip.clip_url;
    video.play().catch(() => {});
  } else {
    video.removeAttribute("src");
    image.src = clip.frame_url;
  }
  const when = home.times[clip.event_id] || new Date(clip.ts).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  const camera = home.labels[clip.camera_id] || clip.camera_id;
  $("replay-caption").textContent = `${when} · ${camera} · ${clip.summary}`;
  $("replay").hidden = false;
  markPlaying(clip.event_id);
}

// Stop and hide the replay window; it opens again only for the next display command.
function closeReplay() {
  home.clips = [];
  const video = $("replay-video");
  video.pause();
  video.removeAttribute("src");
  video.load();
  $("replay-image").removeAttribute("src");
  $("replay").hidden = true;
  markPlaying(null);
}

// Highlight the event whose clip is playing.
function markPlaying(eventId) {
  for (const li of $("events").children) li.classList.toggle("playing", li.dataset.eventId === eventId);
}

// After a clip ends, play the next one; the last one stays on its final frame.
$("replay-video").addEventListener("ended", () => {
  if (home.clipIndex < home.clips.length - 1) {
    home.clipIndex += 1;
    playClip();
  }
});

// The replay window closes with its button, with Esc, or with a click anywhere outside it.
$("replay-close").addEventListener("click", closeReplay);
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("replay").hidden) closeReplay(); });
document.addEventListener("pointerdown", (e) => {
  if (!$("replay").hidden && !$("replay").contains(e.target)) closeReplay();
});

// The header button shows whether sound and the mic are on; clicking it turns them on or off.
speech.on({
  status: (text, problem) => {
    $("sound").textContent = text;
    $("sound").classList.toggle("on", speech.soundOn && !problem);
    $("sound").classList.toggle("problem", problem);
  },
});
$("sound").addEventListener("click", () => { if (speech.soundOn) speech.disable(); else speech.enable(); });

speech.resume();
poll();
