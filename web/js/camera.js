// The camera page's logic: register a label, start the camera, send heartbeats with a thumbnail, stream live video
// to viewers over WebRTC, and while a watch order covers this camera, detect change and upload a frame and short
// clips (with pre-roll) when it happens, and draw where the core sees the object now.

const BEAT_MS = 3000;
const THUMB_WIDTH = 640;
const CLIP_MS = 6000;
const POST_ROLL_CLIPS = 1;
const FRAME_QUALITY = 0.85;
const LABEL_KEY = "meanwhile.cameraLabel";
const RTC_POLL_MS = 1000;
const RTC_MAX_VIEWERS = 3;
const RTC_GATHER_MS = 2000;
const RTC_DISCONNECTED_DROP_MS = 3000;
const LIVE_BOX_POLL_MS = 500;
const CLIP_TYPES = [
  ["video/mp4;codecs=avc1.42E01E,mp4a.40.2", "mp4"], ["video/mp4", "mp4"],
  ["video/webm;codecs=vp8,opus", "webm"], ["video/webm", "webm"],
];

// The change rule.
// Four times a second, shrink the watched area (the focus box plus a margin, or the whole frame) to a small grey
// grid and compare it with the previous one. After removing any overall brightness shift (auto-exposure), count
// the cells whose brightness moved by more than pixelDelta. It is a change when that share is at least minFraction
// on confirmSamples checks in a row; after a change, wait cooldownMs before reporting another. Once the change
// has been quiet for settleMs, send one more frame, so the scene after the change is seen at once.
const CHANGE = {
  sampleMs: 250, gridWidth: 64, boxMargin: 0.5,
  pixelDelta: 24, minFraction: 0.03, confirmSamples: 2, cooldownMs: 3000, settleMs: 1000,
};

/** Share (0-1) of grid cells whose brightness changed by more than pixelDelta, ignoring a global brightness shift. */
function changedFraction(prev, cur) {
  let shift = 0;
  for (let i = 0; i < cur.length; i++) shift += cur[i] - prev[i];
  shift /= cur.length;
  let moved = 0;
  for (let i = 0; i < cur.length; i++) if (Math.abs(cur[i] - prev[i] - shift) > CHANGE.pixelDelta) moved++;
  return moved / cur.length;
}

/** Whether the latest fractions (newest last) count as a change. */
function isChange(fractions) {
  const recent = fractions.slice(-CHANGE.confirmSamples);
  return recent.length === CHANGE.confirmSamples && recent.every((f) => f >= CHANGE.minFraction);
}

const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(location.search);
const page = {
  camera: null, stream: null, facing: "environment", timer: null, wakeLock: null, running: false,
  watch: { active: false, focus_box: null }, sampler: null, prevGrid: null, fractions: [], lastChangeAt: 0,
  settling: false, lastMovedAt: 0,
  recorder: null, segmentChanged: false, preroll: null, postRollLeft: 0, uploads: Promise.resolve(),
  sent: { frames: 0, clips: 0 }, clipType: null,
  viewers: new Map(), ingestViewers: new Set(), seenOffers: new Set(), rtcTimer: null, iceServers: [],
  live: null, liveTimer: null,
};

/** Read a remembered value, or null if storage is unavailable. */
function recall(key) {
  try { return localStorage.getItem(key); } catch { return null; }
}

/** Remember (or forget, with null) a value, ignoring storage errors. */
function remember(key, value) {
  try { value === null ? localStorage.removeItem(key) : localStorage.setItem(key, value); } catch { }
}

/** POST (or another method) JSON to the core; resolves to {status, body}. */
async function postJson(path, body, method = "POST") {
  const res = await fetch(path, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  return { status: res.status, body: await res.json().catch(() => ({})) };
}

/** Rename this page's camera in the core (it keeps its id and past events) before registering under the new label. */
async function renameIfChanged(label) {
  const old = page.camera;
  if (!old || !label.trim() || label.trim().toLowerCase() === String(old.label).toLowerCase()) return;
  const { status, body } = await postJson(`/api/cameras/${old.camera_id}`, { label: label.trim() }, "PATCH");
  if (status !== 200 && status !== 404) throw new Error(body.message || "Couldn't rename the camera.");
}

/** Show a status line; tone is "live", "warn" or "". */
function setStatus(text, tone = "") {
  $("status").textContent = text;
  $("status").className = `status ${tone}`;
}

/** Register this camera's label with the core and remember it. */
async function register(label) {
  const { status, body } = await postJson("/api/cameras", { label });
  if (status !== 200) throw new Error(body.message || "Couldn't register the camera.");
  page.camera = body.camera;
  page.iceServers = body.ice_servers || [];
  page.encodings = body.encodings || null;
  remember(LABEL_KEY, page.camera.label);
  $("label").textContent = page.camera.label;
  applyWatch(body.watch);
}

/** Play a looping test video (?test-video=URL) in the preview instead of the camera, and use its stream.
 *  Unmuted at volume 0, because Chrome pauses muted video when the page is in the background. */
async function testVideoStream(url) {
  const video = $("video");
  video.hidden = false;
  Object.assign(video, { src: url, loop: true, muted: false, volume: 0 });
  await video.play();
  page.testSource = video;
  return video.captureStream();
}

/** A still scene (?test-image=URL): the photo redrawn 15 times a second with a little sensor-like noise. */
async function testImageStream(url) {
  const img = new Image();
  img.src = url;
  await img.decode();
  const canvas = Object.assign(document.createElement("canvas"), { width: img.naturalWidth, height: img.naturalHeight });
  const ctx = canvas.getContext("2d");
  page.testSource = setInterval(() => {
    ctx.drawImage(img, 0, 0);
    const frame = ctx.getImageData(0, 0, canvas.width, canvas.height);
    for (let i = 0; i < frame.data.length; i += 4) {
      const n = (Math.random() - 0.5) * 12;
      frame.data[i] += n; frame.data[i + 1] += n; frame.data[i + 2] += n;
    }
    ctx.putImageData(frame, 0, 0);
  }, 1000 / 15);
  return canvas.captureStream(15);
}

/** Start (or restart) the camera stream: camera and microphone, else camera only, or a test source. */
async function startVideo() {
  if (page.stream && !page.testSource) page.stream.getTracks().forEach((t) => t.stop());
  if (params.get("test-video") || params.get("test-image")) {
    page.stream = page.stream || (params.get("test-video")
      ? await testVideoStream(params.get("test-video")) : await testImageStream(params.get("test-image")));
    if (!params.get("test-video")) $("video").srcObject = page.stream;
    if (page.recorder) restartRecording();
    sendNewTrackToViewers();
    return;
  } else {
    const video = { facingMode: { ideal: page.facing }, width: { ideal: 1280 }, height: { ideal: 720 } };
    try {
      page.stream = await navigator.mediaDevices.getUserMedia({ video, audio: true });
    } catch {
      page.stream = await navigator.mediaDevices.getUserMedia({ video, audio: false });
    }
  }
  $("video").srcObject = page.stream;
  if (page.recorder) restartRecording();
  sendNewTrackToViewers();
}

/** Wait until a connection has gathered its network addresses (or RTC_GATHER_MS), so one description carries them. */
function gathered(pc) {
  if (pc.iceGatheringState === "complete") return Promise.resolve();
  return new Promise((resolve) => {
    pc.addEventListener("icegatheringstatechange", () => pc.iceGatheringState === "complete" && resolve());
    setTimeout(resolve, RTC_GATHER_MS);
  });
}

/** The encoding for one viewer, from the core's settings: reduced live quality for people, and for the detector
 * the frame's short side scaled to the ingest size, whichever way the phone is held (the detector keeps that size
 * when the device is busy, sending fewer frames instead). Without settings, the browser's defaults apply. */
function encodingFor(purpose) {
  const enc = page.encodings;
  if (!enc) return {};
  if (purpose !== "ingest") {
    return { maxBitrate: enc.live.max_bitrate, scaleResolutionDownBy: enc.live.scale_down, maxFramerate: enc.live.max_framerate };
  }
  const settings = page.stream.getVideoTracks()[0].getSettings();
  const video = $("video");
  const shortSide = Math.min(settings.width || video.videoWidth, settings.height || video.videoHeight) || enc.ingest.short_side;
  return { maxBitrate: enc.ingest.max_bitrate, maxFramerate: enc.ingest.max_framerate,
           scaleResolutionDownBy: Math.max(1, shortSide / enc.ingest.short_side) };
}

/** Answer one viewer's offer and send it this camera's video (see encodingFor). With RTC_MAX_VIEWERS already
 * open, a detector's offer replaces the oldest person's viewer and any other offer waits for a free slot. A viewer
 * that stays disconnected for RTC_DISCONNECTED_DROP_MS is dropped, so its slot frees quickly. */
async function acceptViewer({ offer_id, sdp, purpose }) {
  if (page.viewers.size >= RTC_MAX_VIEWERS) {
    const oldest = [...page.viewers.keys()].find((id) => !page.ingestViewers.has(id));
    if (purpose !== "ingest" || !oldest) return;
    dropViewer(oldest);
  }
  const pc = new RTCPeerConnection({ iceServers: page.iceServers });
  page.viewers.set(offer_id, pc);
  if (purpose === "ingest") page.ingestViewers.add(offer_id);
  pc.addEventListener("connectionstatechange", () => {
    if (["failed", "closed"].includes(pc.connectionState)) dropViewer(offer_id);
    if (pc.connectionState === "disconnected") {
      setTimeout(() => pc.connectionState === "disconnected" && dropViewer(offer_id), RTC_DISCONNECTED_DROP_MS);
    }
  });
  await pc.setRemoteDescription({ type: "offer", sdp });
  const sender = pc.addTrack(page.stream.getVideoTracks()[0], page.stream);
  await pc.setLocalDescription(await pc.createAnswer());
  const settings = sender.getParameters();
  settings.encodings = [{ ...(settings.encodings?.[0] || {}), ...encodingFor(purpose) }];
  if (purpose === "ingest") settings.degradationPreference = "maintain-resolution";
  await sender.setParameters(settings).catch(() => { });
  await gathered(pc);
  const { status } = await postJson(`/api/rtc/offers/${offer_id}/answer`, { sdp: pc.localDescription.sdp });
  if (status !== 200) dropViewer(offer_id);
}

/** Close one viewer's connection. */
function dropViewer(offerId) {
  page.viewers.get(offerId)?.close();
  page.viewers.delete(offerId);
  page.ingestViewers.delete(offerId);
}

/** After a camera flip, send the new video track to every viewer without reconnecting. */
function sendNewTrackToViewers() {
  const track = page.stream?.getVideoTracks()[0];
  for (const pc of page.viewers.values()) {
    pc.getSenders().forEach((s) => s.track?.kind === "video" && s.replaceTrack(track));
  }
}

/** Check for new viewer offers every RTC_POLL_MS while the camera runs. */
async function rtcLoop() {
  if (!page.running) return;
  try {
    const { offers } = await (await fetch(`/api/rtc/${page.camera.camera_id}/offers`)).json();
    for (const offer of offers) {
      if (page.seenOffers.has(offer.offer_id)) continue;
      page.seenOffers.add(offer.offer_id);
      acceptViewer(offer).catch(() => dropViewer(offer.offer_id));
    }
  } catch { }
  page.rtcTimer = setTimeout(rtcLoop, RTC_POLL_MS);
}

/** The current video frame as a JPEG blob `width` wide (default full size), or null before the first frame. */
function grabFrame(width, quality) {
  const video = $("video");
  if (!video.videoWidth) return Promise.resolve(null);
  const canvas = document.createElement("canvas");
  canvas.width = width || video.videoWidth;
  canvas.height = Math.round(canvas.width * video.videoHeight / video.videoWidth);
  canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
  return new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", quality));
}

/** Upload a blob the way the core says (a PUT to the URL it hands out); returns its key. */
async function upload(blob, kind, ext) {
  const { status, body } = await postJson("/api/uploads", { camera_id: page.camera.camera_id, kind, ext });
  if (status !== 200) throw new Error(body.message || "Upload refused.");
  const res = await fetch(body.url, { method: body.method, headers: body.headers, body: blob });
  if (!res.ok) throw new Error(`Upload failed (${res.status}).`);
  return body.key;
}

/** Queue an upload so they go one at a time, counting what was sent. */
function queueUpload(blob, kind, ext) {
  page.uploads = page.uploads
    .then(() => upload(blob, kind, ext))
    .then(() => { page.sent[`${kind}s`]++; showWatchStatus(); })
    .catch((err) => setStatus(`Upload failed, will keep trying new ones (${err.message})`, "warn"));
}

/** The watched area in video pixels: the focus box plus a margin, or the whole frame (no box, or the object is away
 * from its resting spot and could be anywhere). */
function watchedArea(video) {
  const box = page.watch.held ? null : page.watch.focus_box;
  const W = video.videoWidth, H = video.videoHeight;
  if (!box) return { x: 0, y: 0, w: W, h: H };
  const mx = (box[2] - box[0]) * CHANGE.boxMargin, my = (box[3] - box[1]) * CHANGE.boxMargin;
  const x0 = Math.max(0, box[0] - mx), y0 = Math.max(0, box[1] - my);
  const x1 = Math.min(1, box[2] + mx), y1 = Math.min(1, box[3] + my);
  return { x: x0 * W, y: y0 * H, w: (x1 - x0) * W, h: (y1 - y0) * H };
}

/** The watched area shrunk to a small grid of brightness values (0-255). */
function sampleGrid() {
  const video = $("video");
  if (!video.videoWidth) return null;
  const area = watchedArea(video);
  const w = CHANGE.gridWidth, h = Math.max(8, Math.round(w * area.h / area.w));
  page.gridCanvas = page.gridCanvas || document.createElement("canvas");
  const canvas = page.gridCanvas;
  canvas.width = w; canvas.height = h;
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  ctx.drawImage(video, area.x, area.y, area.w, area.h, 0, 0, w, h);
  const rgba = ctx.getImageData(0, 0, w, h).data;
  const grid = new Float32Array(w * h);
  for (let i = 0; i < grid.length; i++) grid[i] = 0.299 * rgba[4 * i] + 0.587 * rgba[4 * i + 1] + 0.114 * rgba[4 * i + 2];
  return grid;
}

/** One change check: compare with the last grid; on a change mark the clip and upload a frame, and once it has
 * been quiet for CHANGE.settleMs upload one settled frame. */
function checkForChange() {
  const grid = sampleGrid();
  if (!grid) return;
  const prev = page.prevGrid;
  page.prevGrid = grid;
  if (!prev || prev.length !== grid.length) return;
  page.fractions = [...page.fractions.slice(-(CHANGE.confirmSamples - 1)), changedFraction(prev, grid)];
  if (!isChange(page.fractions)) {
    if (page.settling && performance.now() - page.lastMovedAt >= CHANGE.settleMs) {
      page.settling = false;
      grabFrame(0, FRAME_QUALITY).then((jpeg) => jpeg && queueUpload(jpeg, "frame", "jpg"));
    }
    return;
  }
  page.settling = true;
  page.lastMovedAt = performance.now();
  page.segmentChanged = true;
  if (performance.now() - page.lastChangeAt < CHANGE.cooldownMs) return;
  page.lastChangeAt = performance.now();
  grabFrame(0, FRAME_QUALITY).then((jpeg) => jpeg && queueUpload(jpeg, "frame", "jpg"));
}

/** The first clip format this browser can record. */
function clipType() {
  return CLIP_TYPES.find(([mime]) => MediaRecorder.isTypeSupported(mime)) || ["", "webm"];
}

/** Record one standalone CLIP_MS clip, then hand it on and start the next while watching. */
function recordSegment() {
  const [mimeType, ext] = page.clipType;
  const chunks = [];
  const recorder = new MediaRecorder(page.stream, mimeType ? { mimeType } : {});
  page.recorder = recorder;
  page.segmentChanged = false;
  recorder.ondataavailable = (e) => e.data.size && chunks.push(e.data);
  recorder.onstop = () => {
    const segment = { blob: new Blob(chunks, { type: recorder.mimeType }), ext, changed: page.segmentChanged, sent: false };
    handleSegment(segment);
    if (page.recorder === recorder && page.watch.active) recordSegment();
  };
  recorder.start();
  setTimeout(() => recorder.state === "recording" && recorder.stop(), CLIP_MS);
}

/** Upload a finished clip if something changed in it (with the clip before as pre-roll) or it is post-roll. */
function handleSegment(segment) {
  if (segment.changed) {
    if (page.preroll && !page.preroll.sent) send(page.preroll);
    send(segment);
    page.postRollLeft = POST_ROLL_CLIPS;
  } else if (page.postRollLeft > 0) {
    send(segment);
    page.postRollLeft--;
  }
  page.preroll = segment;
}

/** Upload one clip once. */
function send(segment) {
  if (segment.sent || !segment.blob.size) return;
  segment.sent = true;
  queueUpload(segment.blob, "clip", segment.ext);
}

/** Begin watching: change checks four times a second and back-to-back clips. */
function startWatching() {
  page.clipType = page.clipType || clipType();
  page.prevGrid = null; page.fractions = []; page.preroll = null; page.postRollLeft = 0; page.settling = false;
  page.sampler = setInterval(checkForChange, CHANGE.sampleMs);
  recordSegment();
}

/** Stop watching: no more change checks or clips. */
function stopWatching() {
  clearInterval(page.sampler);
  const recorder = page.recorder;
  page.recorder = null;
  if (recorder && recorder.state === "recording") recorder.stop();
}

/** Start a fresh clip on the current stream (after a camera flip). */
function restartRecording() {
  stopWatching();
  if (page.watch.active) startWatching();
}

/** Follow what the core says this camera should do (from register and every heartbeat). */
function applyWatch(watch) {
  if (!watch) return;
  const was = page.watch;
  page.watch = watch;
  if (watch.active && !was.active) startWatching();
  else if (!watch.active && was.active) stopWatching();
  else if (JSON.stringify(watch.focus_box) !== JSON.stringify(was.focus_box) || watch.held !== was.held) {
    page.prevGrid = null; page.fractions = [];
  }
  placeFocusBox();
  showWatchStatus();
}

/** Place a box element over the preview at a box given as frame fractions, matching the video's letterboxing;
 * hidden when there is no box or no video yet. */
function placeBox(el, box) {
  const video = $("video");
  if (!box || !video.videoWidth) { el.hidden = true; return; }
  const scale = Math.min(innerWidth / video.videoWidth, innerHeight / video.videoHeight);
  const w = video.videoWidth * scale, h = video.videoHeight * scale;
  const left = (innerWidth - w) / 2, top = (innerHeight - h) / 2;
  Object.assign(el.style, {
    left: `${left + box[0] * w}px`, top: `${top + box[1] * h}px`,
    width: `${(box[2] - box[0]) * w}px`, height: `${(box[3] - box[1]) * h}px`,
  });
  el.hidden = false;
}

/** Draw the live box when the core has one for this camera: where the object is now, solid where the detector
 * sees it, dashed where the vision model (or the step-away box) placed it, white while it moves in a hand, red and
 * dotted at its last place while it is out of sight. The focus box is drawn only when there is no live box. */
function placeFocusBox() {
  const live = page.watch.active && page.live?.camera_id === page.camera?.camera_id ? page.live : null;
  placeBox($("focus"), !live && page.watch.active && page.watch.focus_box);
  $("focus").classList.toggle("held", Boolean(page.watch.held));
  placeBox($("live"), live?.box);
  $("live").classList.toggle("guess", Boolean(live && live.source !== "detector"));
  $("live").classList.toggle("held", Boolean(live?.held));
  $("live").classList.toggle("missing", Boolean(live?.missing));
}

/** Read the session's live box every LIVE_BOX_POLL_MS while this camera's watch is active, and redraw. */
async function liveBoxLoop() {
  if (!page.running) return;
  if (page.watch.active && page.watch.focus_box) {
    try {
      const { session } = await (await fetch("/api/sessions/latest")).json();
      page.live = session && session.session_id === page.watch.session_id ? session.live_box || null : null;
    } catch { page.live = null; }
  } else {
    page.live = null;
  }
  placeFocusBox();
  page.liveTimer = setTimeout(liveBoxLoop, LIVE_BOX_POLL_MS);
}

/** The status line while running: live, or watching with what has been sent. */
function showWatchStatus() {
  if (!page.running) return;
  if (!page.watch.active) { setStatus("Live. Keep this page open with the screen on.", "live"); return; }
  const where = page.watch.focus_box && !page.watch.held ? "the box" : "the whole view";
  setStatus(`Watching ${where}. Sent ${page.sent.frames} frames, ${page.sent.clips} clips.`, "live");
}

/** One heartbeat: upload a thumbnail if there is a frame, then tell the core this camera is alive. */
async function beat() {
  const thumb = await grabFrame(THUMB_WIDTH, 0.7);
  const payload = thumb ? { thumb_key: await upload(thumb, "thumb", "jpg") } : {};
  let { status, body } = await postJson(`/api/cameras/${page.camera.camera_id}/heartbeat`, payload);
  if (status === 410) {
    remember(LABEL_KEY, null);
    stop();
    $("setup-error").textContent = body.message || "This camera was removed. Start it again to add it back.";
    return;
  }
  if (status === 404) {
    await register(page.camera.label);
    ({ status, body } = await postJson(`/api/cameras/${page.camera.camera_id}/heartbeat`, {}));
  }
  if (status !== 200) throw new Error(body.message || "Heartbeat refused.");
  if (body.camera && body.camera.label !== page.camera.label) {
    page.camera.label = body.camera.label;
    remember(LABEL_KEY, page.camera.label);
    $("label").textContent = page.camera.label;
  }
  applyWatch(body.watch);
}

/** Run heartbeats one after another every BEAT_MS while the camera is running. */
async function loop() {
  if (!page.running) return;
  try {
    await beat();
    showWatchStatus();
  } catch (err) {
    setStatus(`Can't reach Meanwhile, retrying… (${err.message})`, "warn");
  }
  page.timer = setTimeout(loop, BEAT_MS);
}

/** Keep the screen awake while the camera runs, where the browser allows it. */
async function keepAwake() {
  try { if (page.running && "wakeLock" in navigator) page.wakeLock = await navigator.wakeLock.request("screen"); } catch { }
}

/** Register, start the camera and begin heartbeats. */
async function start(label) {
  if (!params.get("test-video") && !params.get("test-image") && !navigator.mediaDevices?.getUserMedia) {
    throw new Error(`This page needs HTTPS to use the camera. Open this page over https:// instead.`);
  }
  await startVideo();
  $("setup").hidden = true;
  $("video").hidden = $("bar").hidden = false;
  page.running = true;
  await register(label);
  keepAwake();
  loop();
  rtcLoop();
  liveBoxLoop();
}

/** Stop the camera, watching and heartbeats, and show the label form again. */
function stop() {
  page.running = false;
  clearTimeout(page.timer);
  clearTimeout(page.rtcTimer);
  clearTimeout(page.liveTimer);
  [...page.viewers.keys()].forEach(dropViewer);
  stopWatching();
  page.watch = { active: false, focus_box: null };
  if (page.stream && !page.testSource) page.stream.getTracks().forEach((t) => t.stop());
  page.wakeLock?.release().catch(() => { });
  $("video").hidden = $("bar").hidden = $("focus").hidden = $("live").hidden = true;
  $("setup").hidden = false;
  $("label-input").value = page.camera?.label || "";
  $("label-input").focus();
}

$("setup").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("setup-error").textContent = "";
  try {
    await renameIfChanged($("label-input").value);
    await start($("label-input").value);
  } catch (err) { stop(); $("setup-error").textContent = err.message; }
});

$("rename").addEventListener("click", () => { remember(LABEL_KEY, null); stop(); });

$("flip").addEventListener("click", async () => {
  page.facing = page.facing === "environment" ? "user" : "environment";
  try { await startVideo(); } catch (err) { setStatus(err.message, "warn"); }
});

$("video").addEventListener("loadedmetadata", placeFocusBox);
addEventListener("resize", placeFocusBox);
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") keepAwake(); });

const saved = params.get("label") || recall(LABEL_KEY);
if (saved) {
  start(saved).catch((err) => { stop(); $("setup-error").textContent = err.message; });
} else {
  $("setup").hidden = false;
}
