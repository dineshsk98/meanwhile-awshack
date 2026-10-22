// The "Pair a device" widget: shows a one-time pairing QR code with a countdown, and lists and revokes paired devices.
// mountPairing(root, base) draws it into `root` and calls `${base}/pairing-codes` and `${base}/paired-devices`.

const PAIRING_LIST_MS = 3000;

/** Time since an ISO timestamp in its largest whole unit: "45 s ago", "12 min ago", "2 h ago", "3 d ago". */
function pairingAgo(ts) {
  const s = Math.max(0, Math.floor((Date.now() - Date.parse(ts)) / 1000));
  const unit = s < 60 ? `${s} s` : s < 3600 ? `${Math.floor(s / 60)} min` : s < 86400
    ? `${Math.floor(s / 3600)} h` : `${Math.floor(s / 86400)} d`;
  return `${unit} ago`;
}

/** Draw the widget into `root`, talking to the pairing API under `base` ("/api" on the core, "/voice" on the panel). */
function mountPairing(root, base) {
  root.innerHTML = `
    <style>
      .pairing .pair-qr { display: flex; gap: 16px; align-items: center; flex-wrap: wrap; margin: 12px 0; }
      .pairing .pair-qr svg { width: 220px; height: 220px; border-radius: 8px; display: block; }
      .pairing .pair-count { font-size: 15px; }
      .pairing .pair-devices { list-style: none; padding: 0; margin: 8px 0 0; }
      .pairing .pair-devices li { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; padding: 6px 0;
        border-top: 1px solid var(--line); }
      .pairing .pair-devices .when, .pairing .pair-note { color: var(--muted); font-size: 14px; }
      .pairing .pair-devices button { margin-left: auto; }
      .pairing [hidden] { display: none !important; }
    </style>
    <div class="pairing">
      <button type="button" class="pair-new">Pair a device</button>
      <div class="pair-qr" hidden>
        <div class="qr"></div>
        <div><p>Scan this with the new phone's camera, or with <b>Scan to pair</b> in the Meanwhile app.</p>
          <p class="pair-count"></p></div>
      </div>
      <p class="pair-note"></p>
      <ul class="pair-devices"></ul>
    </div>`;
  const $ = (sel) => root.querySelector(sel);
  const state = { timer: null, known: null };

  /** Show a short line under the QR code. */
  function note(text) {
    $(".pair-note").textContent = text;
  }

  /** Hide the QR code and stop its countdown. */
  function hideCode() {
    clearInterval(state.timer);
    state.timer = null;
    $(".pair-qr").hidden = true;
    $(".qr").innerHTML = "";
  }

  /** Ask for a new one-time code and show it as a QR code with a countdown until it expires. */
  async function showCode() {
    hideCode();
    note("");
    const reply = await fetch(`${base}/pairing-codes`, { method: "POST" });
    const body = await reply.json().catch(() => ({}));
    if (!reply.ok) {
      note(body.message || `Couldn't make a pairing code (${reply.status}).`);
      return;
    }
    $(".qr").innerHTML = body.qr_svg;
    $(".pair-qr").hidden = false;
    $(".pair-new").textContent = "Show a new code";
    const ends = Date.now() + body.expires_in_s * 1000;
    const tick = () => {
      const left = Math.round((ends - Date.now()) / 1000);
      if (left <= 0) {
        hideCode();
        note("That code expired. Tap Pair a device for a new one.");
        $(".pair-new").textContent = "Pair a device";
        return;
      }
      $(".pair-count").textContent = `Works once, for ${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")} more.`;
    };
    tick();
    state.timer = setInterval(tick, 1000);
  }

  /** Revoke one device after asking. */
  async function revoke(device) {
    const which = device.current ? "this device (it will need pairing again)" : `“${device.name}”`;
    if (!confirm(`Revoke ${which}?`)) return;
    const reply = await fetch(`${base}/paired-devices/${encodeURIComponent(device.device_id)}`, { method: "DELETE" });
    if (!reply.ok) note(`Couldn't revoke it (${reply.status}).`);
    listDevices();
  }

  /** Redraw the paired devices; a device that appears while a code is showing means it was just used. */
  async function listDevices() {
    const reply = await fetch(`${base}/paired-devices`, { cache: "no-store" }).catch(() => null);
    if (!reply || !reply.ok) return;
    const devices = (await reply.json()).devices;
    const fresh = state.known && devices.filter((d) => !state.known.has(d.device_id));
    if (fresh && fresh.length && state.timer) {
      hideCode();
      $(".pair-new").textContent = "Pair a device";
      note(`Paired: ${fresh.map((d) => d.name).join(", ")}.`);
    }
    state.known = new Set(devices.map((d) => d.device_id));
    const list = $(".pair-devices");
    list.innerHTML = devices.length ? "" : "<li class=\"when\">No paired devices yet.</li>";
    for (const d of devices) {
      const li = document.createElement("li");
      const name = document.createElement("span");
      name.textContent = `${d.name}${d.current ? " (this device)" : ""}`;
      const when = document.createElement("span");
      when.className = "when";
      when.textContent = `${d.kind} · paired ${pairingAgo(d.created)} · seen ${pairingAgo(d.last_seen)}`;
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = "Revoke";
      button.addEventListener("click", () => revoke(d));
      li.append(name, when, button);
      list.append(li);
    }
  }

  $(".pair-new").addEventListener("click", showCode);
  listDevices();
  setInterval(listDevices, PAIRING_LIST_MS);
}
