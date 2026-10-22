// The debug page's logic: poll cameras, the latest session, its events, people, the analyser and the newest uploads;
// show each online camera as live WebRTC video (snapshot until connected) with the focus box and the object's live
// box; start and end watch orders; add, name, merge and remove people; pair and revoke devices (pairing.js).

const POLL_MS = 1000;
const LIVE_BOX_POLL_MS = 500;
const LIVE_RETRY_MS = 5000;
const ANSWER_WAIT_MS = 15000;
const ANSWER_POLL_MS = 500;
const GATHER_MS = 2000;
const live = new Map();
const grid = document.getElementById("grid");
const err = document.getElementById("err");
const view = { session: null };

/** Time since an ISO timestamp in its largest whole unit: "45 s", "12 min", "2 h", "3 d". */
function ago(ts) {
  const s = Math.max(0, Math.floor((Date.now() - Date.parse(ts)) / 1000));
  if (s < 60) return `${s} s`;
  if (s < 3600) return `${Math.floor(s / 60)} min`;
  if (s < 86400) return `${Math.floor(s / 3600)} h`;
  return `${Math.floor(s / 86400)} d`;
}

/** Local clock time of an ISO timestamp. */
function clock(ts) {
  return new Date(ts).toLocaleTimeString();
}

/** GET a JSON path from the core. */
async function getJson(path) {
  return (await fetch(path)).json();
}

/** The tile element for a camera, created on first sight. */
function tileFor(camera) {
  let tile = document.getElementById(`cam-${camera.camera_id}`);
  if (!tile) {
    tile = document.createElement("div");
    tile.id = `cam-${camera.camera_id}`;
    tile.className = "tile";
    tile.innerHTML = '<div class="frame"><img alt=""><video autoplay playsinline muted></video><div class="box" hidden></div><div class="live-box" hidden></div></div>'
      + '<div class="meta"><strong class="label"></strong><span class="state"></span></div>';
    grid.append(tile);
  }
  return tile;
}

/** Wait until a connection has gathered its network addresses (or GATHER_MS), so one description carries them. */
function gathered(pc) {
  if (pc.iceGatheringState === "complete") return Promise.resolve();
  return new Promise((resolve) => {
    pc.addEventListener("icegatheringstatechange", () => pc.iceGatheringState === "complete" && resolve());
    setTimeout(resolve, GATHER_MS);
  });
}

/** Ask a camera for live video: post an offer through the core, wait for its answer, and play what arrives. */
async function connectLive(camera, tile) {
  const pc = new RTCPeerConnection();
  const entry = { pc, retryAt: 0 };
  live.set(camera.camera_id, entry);
  const video = tile.querySelector("video");
  pc.addTransceiver("video", { direction: "recvonly" });
  pc.addEventListener("track", (e) => { video.srcObject = e.streams[0] || new MediaStream([e.track]); });
  pc.addEventListener("connectionstatechange", () => {
    tile.classList.toggle("streaming", pc.connectionState === "connected");
    if (["failed", "closed"].includes(pc.connectionState)) dropLive(camera.camera_id, tile);
  });
  try {
    await pc.setLocalDescription(await pc.createOffer());
    await gathered(pc);
    const res = await fetch(`/api/rtc/${camera.camera_id}/offers`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ sdp: pc.localDescription.sdp }) });
    const { offer_id } = await res.json();
    const until = Date.now() + ANSWER_WAIT_MS;
    while (Date.now() < until && live.get(camera.camera_id) === entry && !entry.retryAt) {
      const { sdp } = await getJson(`/api/rtc/offers/${offer_id}`);
      if (sdp) { await pc.setRemoteDescription({ type: "answer", sdp }); return; }
      await new Promise((r) => setTimeout(r, ANSWER_POLL_MS));
    }
    dropLive(camera.camera_id, tile);
  } catch {
    dropLive(camera.camera_id, tile);
  }
}

/** Close a camera's live connection, show its snapshot again, and allow a retry after LIVE_RETRY_MS. */
function dropLive(cameraId, tile) {
  const entry = live.get(cameraId);
  if (!entry || entry.retryAt) return;
  entry.pc.close();
  entry.retryAt = Date.now() + LIVE_RETRY_MS;
  tile.classList.remove("streaming");
  tile.querySelector("video").srcObject = null;
}

/** Keep a live connection for each online camera, and close it when the camera goes offline. */
function keepLive(camera, tile) {
  const entry = live.get(camera.camera_id);
  if (!camera.online) { if (entry) dropLive(camera.camera_id, tile); return; }
  if (!entry || (entry.retryAt && Date.now() >= entry.retryAt)) connectLive(camera, tile);
}

/** Draw one camera: live video or thumbnail, focus box (dashed while the object is away from its spot), label, and
 * live, snapshot or how long it has been offline. */
function draw(camera) {
  const tile = tileFor(camera);
  keepLive(camera, tile);
  const img = tile.querySelector("img");
  if (camera.thumb_url && img.getAttribute("src") !== camera.thumb_url) img.src = camera.thumb_url;
  tile.classList.toggle("offline", !camera.online);
  tile.querySelector(".label").textContent = camera.label;
  const state = tile.querySelector(".state");
  state.className = `state ${camera.online ? "live" : ""}`;
  state.textContent = tile.classList.contains("streaming") ? "live"
    : camera.online ? "snapshot (connecting live video)" : `offline · last seen ${ago(camera.last_seen)} ago`;
  const s = view.session, box = tile.querySelector(".box");
  const live = s && !s.ended_at && s.live_box && s.live_box.camera_id === camera.camera_id;
  const focus = !live && s && !s.ended_at && s.focus_camera_id === camera.camera_id && s.focus_box;
  box.hidden = !focus;
  box.classList.toggle("held", Boolean(focus && s.state && s.state !== "on_surface"));
  if (focus) {
    const [x0, y0, x1, y1] = focus;
    Object.assign(box.style, { left: `${x0 * 100}%`, top: `${y0 * 100}%`, width: `${(x1 - x0) * 100}%`, height: `${(y1 - y0) * 100}%` });
  }
  drawLiveBox(tile, camera.camera_id);
}

/** Draw the object's live box on a camera's tile: solid where the detector sees it, dashed where the vision model
 * (or the step-away box) placed it, white while it moves in a hand, red and dotted at its last place while it is out
 * of sight; hidden when the active session has none for this camera (the focus box is drawn instead). */
function drawLiveBox(tile, cameraId) {
  const s = view.session, live = s && !s.ended_at && s.live_box;
  const el = tile.querySelector(".live-box");
  el.hidden = !(live && live.camera_id === cameraId && live.box);
  if (el.hidden) return;
  tile.querySelector(".box").hidden = true;
  const [x0, y0, x1, y1] = live.box;
  Object.assign(el.style, { left: `${x0 * 100}%`, top: `${y0 * 100}%`, width: `${(x1 - x0) * 100}%`, height: `${(y1 - y0) * 100}%` });
  el.classList.toggle("guess", live.source !== "detector");
  el.classList.toggle("held", Boolean(live.held));
  el.classList.toggle("missing", Boolean(live.missing));
}

/** Refresh only the latest session and its live box, between the full polls. */
async function pollLive() {
  try {
    const { session } = await getJson("/api/sessions/latest");
    view.session = session;
    const live = session && session.live_box;
    const tile = live && document.getElementById(`cam-${live.camera_id}`);
    if (tile) drawLiveBox(tile, live.camera_id);
  } catch { }
}

/** Show the latest session, with an End button while it is active. */
function drawSession(session) {
  const card = document.getElementById("session");
  const key = session ? `${session.session_id}:${session.ended_at}` : "";
  if (card.dataset.key === key) return;
  card.dataset.key = key;
  if (!session) { card.textContent = "No session yet."; return; }
  const order = session.watch_order;
  const status = session.ended_at ? `ended ${clock(session.ended_at)}` : `active since ${clock(session.started_at)}`;
  card.innerHTML = '<strong class="summary"></strong> <span class="muted status"></span> '
    + (session.ended_at ? "" : '<button type="button">End</button>') + "<pre></pre>";
  card.querySelector(".summary").textContent = `I'm ${order.summary_for_alexa}.`;
  card.querySelector(".status").textContent = `· ${order.kind} · ${status}`;
  card.querySelector("pre").textContent = JSON.stringify({ instruction: session.instruction, ...order,
    focus_camera_id: session.focus_camera_id, focus_box: session.focus_box }, null, 2);
  card.querySelector("button")?.addEventListener("click", async () => {
    await fetch(`/api/sessions/${session.session_id}/end`, { method: "POST" });
    poll();
  });
}

/** Show the newest frames and clips as links. */
function drawUploads(uploads) {
  const list = document.getElementById("uploads");
  const key = uploads.map((u) => u.key).join();
  if (list.dataset.key === key) return;
  list.dataset.key = key;
  list.replaceChildren(...(uploads.length ? uploads : [null]).map((u) => {
    const li = document.createElement("li");
    if (!u) { li.className = "muted"; li.textContent = "None yet."; return li; }
    const a = Object.assign(document.createElement("a"), { href: u.url, target: "_blank", textContent: u.key });
    li.append(`${clock(u.at)}  `, a, `  ${(u.bytes / 1024).toFixed(0)} KB`);
    return li;
  }));
}

/** Show the session's events, newest first, coloured by alert, with links to their clip and frame. */
function drawEvents(events) {
  const list = document.getElementById("events");
  const key = events.map((e) => `${e.event_id}:${e.clip_url}`).join();
  if (list.dataset.key === key) return;
  list.dataset.key = key;
  list.replaceChildren(...(events.length ? [...events].reverse() : [null]).map((e) => {
    const li = document.createElement("li");
    if (!e) { li.className = "muted"; li.textContent = "None yet."; return li; }
    li.className = e.alert;
    const lag = e.happened_at ? ` · written ${((Date.parse(e.ts) - Date.parse(e.happened_at)) / 1000).toFixed(1)} s later` : "";
    li.append(Object.assign(document.createElement("div"), { textContent: e.summary }));
    const when = Object.assign(document.createElement("div"), { className: "when muted",
      textContent: `${clock(e.happened_at || e.ts)} · ${e.camera_id} · ${e.action} · ${e.alert} · ${e.source}${lag} ` });
    for (const [label, url] of [["clip", e.clip_url], ["frame", e.frame_url]]) {
      if (url) when.append(Object.assign(document.createElement("a"), { href: url, target: "_blank", textContent: label }), " ");
    }
    li.append(when);
    return li;
  }));
}

/** Show what the analyser is doing: queue length, the item in progress and the latest outcome. */
function drawAnalysis(status) {
  const line = document.getElementById("analysis");
  if (status.off) { line.textContent = "Analyser off."; return; }
  const last = status.recent[0];
  line.textContent = `Analyser: ${status.queued} queued` + (status.busy ? ` · working on ${status.busy}` : "")
    + (last ? ` · last: ${last.key} (${last.took_s} s, queued ${last.waited_s} s): ${last.outcome}` : "");
}

/** Send a people request and show its error (if any) under the People form, then redraw. */
async function peopleRequest(path, method, body) {
  const res = await fetch(path, body === undefined ? { method }
    : { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const reply = await res.json().catch(() => ({}));
  document.getElementById("person-reply").textContent = res.ok ? "" : reply.message || `Error ${res.status}`;
  poll();
}

/** Controls for an unnamed person: give them a name and role, or merge them into someone already listed. */
function namingControls(p, people) {
  const name = Object.assign(document.createElement("input"), { placeholder: "Name them", autocomplete: "off" });
  const role = document.createElement("select");
  ["family", "helper", "relative", "friend", "guest", "owner"].forEach((r) => role.append(new Option(r, r)));
  const save = Object.assign(document.createElement("button"), { type: "button", textContent: "Name" });
  save.addEventListener("click", () => peopleRequest(`/api/people/${p.person_id}`, "PATCH",
    { name: name.value, role: role.value }));
  const into = document.createElement("select");
  into.append(new Option("same person as…", ""));
  people.filter((o) => o.person_id !== p.person_id).forEach((o) => into.append(new Option(o.name, o.person_id)));
  const merge = Object.assign(document.createElement("button"), { type: "button", textContent: "Merge" });
  merge.addEventListener("click", () => into.value
    && peopleRequest(`/api/people/${p.person_id}/merge`, "POST", { into: into.value }));
  return [name, role, save, into, merge];
}

/** Show the people (with their first photo and how many faces are indexed): named ones with a Remove button,
 *  unnamed ones face ID found with controls to name or merge them too. */
function drawPeople(people) {
  const list = document.getElementById("people");
  const key = people.map((p) => `${p.person_id}:${p.role}:${p.note}:${p.photo_url}:${p.faces}`).join();
  if (list.dataset.key === key) return;
  list.dataset.key = key;
  document.getElementById("remove-unnamed").hidden = !people.some((p) => p.unnamed);
  list.replaceChildren(...(people.length ? people : [null]).map((p) => {
    const li = document.createElement("li");
    if (!p) { li.className = "muted"; li.textContent = "Nobody yet."; return li; }
    if (p.photo_url) li.append(Object.assign(document.createElement("img"), { src: p.photo_url, alt: p.name }));
    li.append(Object.assign(document.createElement("strong"), { textContent: p.name }),
      Object.assign(document.createElement("span"), { className: "muted", textContent: p.unnamed
        ? `unnamed · last seen ${p.last_seen ? new Date(p.last_seen).toLocaleString() : "?"}`
        : `${p.role} · wears ${p.note || "?"} · ${p.faces ? `${p.faces} face(s) indexed` : "no face"}` }));
    if (p.unnamed) li.append(...namingControls(p, people));
    const remove = Object.assign(document.createElement("button"), { type: "button", textContent: "Remove" });
    remove.addEventListener("click", () => peopleRequest(`/api/people/${p.person_id}`, "DELETE"));
    li.append(remove);
    return li;
  }));
}

/** Fetch cameras, session, events, people, analyser status and uploads once and redraw. */
async function poll() {
  try {
    const [{ cameras }, { session }, { uploads }, { people }, analysis] = await Promise.all(
      [getJson("/api/cameras"), getJson("/api/sessions/latest"), getJson("/api/debug/uploads"),
       getJson("/api/people"), getJson("/api/debug/analysis")]);
    err.textContent = "";
    view.session = session;
    drawSession(session);
    drawUploads(uploads);
    drawPeople(people);
    drawAnalysis(analysis);
    drawEvents(session ? (await getJson(`/api/sessions/${session.session_id}/events`)).events : []);
    if (!cameras.length) {
      grid.innerHTML = '<p class="empty">No cameras yet. Open <a href="/camera">/camera</a> on a phone or this laptop.</p>';
      return;
    }
    grid.querySelector(".empty")?.remove();
    cameras.forEach(draw);
  } catch (e) {
    err.textContent = `Can't reach the core: ${e.message}`;
  }
}

document.getElementById("order-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const reply = document.getElementById("order-reply");
  const instruction = document.getElementById("instruction").value;
  reply.textContent = "Thinking…";
  const started = performance.now();
  const res = await fetch("/api/watch-orders", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ instruction }) });
  const body = await res.json().catch(() => ({}));
  const took = `${((performance.now() - started) / 1000).toFixed(1)} s`;
  if (res.status === 201) reply.textContent = `Started (${took}): I'm ${body.session.watch_order.summary_for_alexa}.`;
  else if (body.clarify) reply.textContent = `Asks back (${took}): ${body.clarify}`;
  else reply.textContent = `${res.status} ${body.error || ""} (${took}): ${body.message || ""}`;
  poll();
});

document.getElementById("remove-unnamed").addEventListener("click", () => {
  if (confirm("Delete every unnamed person, with their faces and photos?")) peopleRequest("/api/people/unnamed", "DELETE");
});

document.getElementById("person-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const reply = document.getElementById("person-reply");
  const fields = { name: document.getElementById("person-name").value,
    role: document.getElementById("person-role").value, note: document.getElementById("person-note").value };
  const photoInput = document.getElementById("person-photo");
  let request = { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(fields) };
  if (photoInput.files.length) {
    const form = new FormData();
    Object.entries(fields).forEach(([k, v]) => form.append(k, v));
    [...photoInput.files].forEach((f) => form.append("photo", f));
    request = { method: "POST", body: form };
  }
  reply.textContent = "Saving…";
  const res = await fetch("/api/people", request);
  const body = await res.json().catch(() => ({}));
  reply.textContent = res.ok ? `Added ${body.person.name}` + (body.person.faces ? ` (${body.person.faces} face(s) indexed).` : ".")
    : body.message || `Error ${res.status}`;
  if (res.ok) {
    document.getElementById("person-name").value = document.getElementById("person-note").value = "";
    photoInput.value = "";
  }
  poll();
});

poll();
mountPairing(document.getElementById("pairing"), "/api");
setInterval(poll, POLL_MS);
setInterval(pollLive, LIVE_BOX_POLL_MS);
