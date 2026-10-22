# Meanwhile core API

The HTTP API the Meanwhile core serves and the voice side calls.

## Conventions
- **Base URL:** `$MEANWHILE_CORE_URL`, e.g. `http://127.0.0.1:8100`. All paths start with `/api`.
- **Data:** JSON in and out.
- **Time:** ISO 8601 UTC with milliseconds and a `Z`, e.g. `2026-10-10T09:32:00.123Z`.
- **Errors:** `{"error": "<code>", "message": "<one sentence>"}`, with the status codes listed under each endpoint.
- **Speed:** every `GET` answers in under 100 ms.
- **Auth:** the core always has a key (`MEANWHILE_KEY`; it won't start without one). Every `/api` route except `POST /api/pair` answers `401 {"error": "unauthorized"}` unless the request carries the key or a paired device's token (see Device pairing), in the `X-Meanwhile-Key` header or the `mw_key` cookie (never in a URL). A client that presents `MEANWHILE_AUTH_FAIL_TRIES` different wrong keys or tokens within `MEANWHILE_AUTH_FAIL_WINDOW_S` seconds gets **429** `{"error": "too_many_tries", "retry_after_s"}` with `Retry-After` until the window has passed. Files under `/media` need the same credentials or a signed link (below).
- **Media:** every media link the core returns (`clip_url`, `frame_url`, `thumb_url`, `photo_url`, `photo_urls`, and the push `frame_url`) is a signed path, `/media/<key>?exp=<unix seconds>&sig=<signature>`, ready for a `<video>` or `<img>` without credentials. Use it as given, query string included. It opens only that file, for at least `MEANWHILE_MEDIA_URL_TTL_S` seconds (at most 1.5 times that), and the same file keeps the same link within each half of that time; after it expires, read the record again for a new link.

## Shapes
```jsonc
// Camera (also `id`, a copy of camera_id; sessions, events and people carry `id` the same way)
{"camera_id": "desk", "label": "Desk", "last_seen": "…Z", "online": true, "thumb_url": "/media/…jpg"}

// Session: at most one active
{"session_id": "ses_…", "started_at": "…Z", "ended_at": null,          // null while active
 "instruction": "keep an eye on my watch",                             // as spoken
 "watch_order": {"kind": "object|notes|monitor", "target": "watch",
                 "cameras": ["desk"] /* or "all" */, "clip_question": "…", "report": ["…"],
                 "alert_when": ["picked_up"],
                 "summary_for_alexa": "keeping an eye on your watch on the Desk camera"},  // a phrase completing "I'm …"
 "focus_object": "watch", "focus_camera_id": "desk",                  // object orders only
 "state": "on_surface|with_person|with_owner|taken|unknown", "state_actor": "ravi",  // object orders only
 "caught_up_until": "…Z", "caught_up_at": "…Z",     // optional: the newest event the user has been told, and when
 "live_box": {"camera_id": "desk", "box": [0.45, 0.71, 0.65, 0.92],  // object orders with detector tracking only:
              "held": false, "missing": false, "at": "…Z",          // where the object is now ([x0, y0, x1, y1]
              "source": "detector|vision|step_away"}}                // fractions of the frame), moving in a hand,
                                                                      // or out of sight (box = last place); source
                                                                      // = who placed it: the detector, the vision
                                                                      // model, or still the step-away box; updated
                                                                      // at most twice a second

// Event
{"event_id": "evt_…", "session_id": "ses_…", "ts": "…Z", "camera_id": "desk",
 "happened_at": "…Z",            // optional: when it happened; ts is when the event was written (later if analysis lags)
 "action": "picked_up",          // open text. The tracker's own: picked_up|put_back|moved|handed_to|left_view|back_in_view|
                                 // still_tracking|taken (object), left; anything else is a few
                                 // lowercase words from the video model ("turned the lamp on", "came into the room")
 "thing": "the front door", "state": "locked",  // optional: what the event left in a new state, in the user's words
 "object_action": true,          // object orders only: something done to the object besides the tracker's moves
 "actor_id": "ravi", "actor_name": "Ravi", "actor_role": "helper",     // "unknown" + null name/role if not recognised;
                                                                       // null when no person was involved
 "recipient_id": "owner",
 "summary": "Your watch was picked up by Ravi.",                        // one sentence, ready to speak
 "alert": "heads_up|verdict|alert|none",
 "clip_url": "/media/clips/desk/….mp4", "frame_url": "/media/frames/desk/….jpg",
 "source": "video_model|vision|detector|rekognition|tracker"}

// Person
{"person_id": "owner", "name": "Owner", "role": "owner|family|helper|relative|friend|guest",
 "note": "a blue shirt",                                               // what they wear; names them when face ID is off
 "photo_url": "/media/people/owner/1.jpg",                            // the first photo, or null
 "photo_urls": ["/media/people/owner/1.jpg"], "faces": 1,              // faces: faces indexed for face ID
 "unnamed": false}
// An unnamed person: face ID saw a face that matched nobody (kept when MEANWHILE_FACE_KEEP_UNNAMED is on), or a clip
// showed someone the session hadn't recognised by face (then "note" is what they wore, with no photo and "faces": 0)
{"person_id": "unnamed-4f1b83", "name": "Person 3", "role": "", "unnamed": true, "number": 3,
 "photo_url": "/media/people/unnamed-4f1b83/1.jpg",                   // the upper-body crop it was first seen in
 "first_seen": "…Z", "last_seen": "…Z", "camera_id": "desk", "faces": 1}
// Events name an unnamed person "an unnamed person (Person 3)" in actor_name and summaries, and carry
// "actor_unnamed": true
```

## Endpoints
| Method and path | Returns |
|---|---|
| `POST /api/watch-orders` `{"instruction": "…"}` | Exactly one of:<br>**201** `{"session": Session}`: compiled (with grounding for objects) and started.<br>**200** `{"clarify": "…"}`: no session started. The instruction was unclear, was a question rather than an instruction ("did I lock the door?"), or named a camera that doesn't exist. `clarify` is a polite, whole-sentence question.<br>**422** `{"error": "not_found", "message": "I can't see your umbrella on the Desk or Room camera."}`.<br>**409** `{"error": "already_watching", "session": Session}`. |
| `GET /api/sessions/latest` | **200** `{"session": Session \| null, "active": bool}`: the active session, else the most recently started one. |
| `GET /api/sessions/{id}/events?since=<ts>` | **200** `{"events": [Event…]}` in time order; only events after `since` if given.<br>**404** unknown session. |
| `POST /api/sessions/{id}/end` | **200** `{"session": Session}` with `ended_at` set.<br>**409** already ended. |
| `POST /api/sessions/{id}/caught-up` `{"until": "…Z"}` | The voice side calls this after it has told the user the session's events up to `until` (the newest `ts` told). Keeps the later of this and any earlier cursor in `caught_up_until`, and sets `caught_up_at` to now; works on active and ended sessions. "What did I miss?" then tells only events after the cursor, so a session is told once.<br>**200** `{"session": Session}`. **400** `bad_until`. **404** `not_found`. |
| `GET /api/sessions?limit=20` | **200** `{"sessions": [Session…]}` newest first (limit 1 to 100). Each also has `active`, `last_verdict` (the summary of its last `verdict` event, or null; also as `summary`) and `event_count`.<br>**400** `bad_limit`. |
| `GET /api/status` | **200** `{"lag_s": 2.4, "queued": 0, "busy": false, "analyser": true, "active": false, "push": {"enabled": false, "devices": 0, "last": null}, "recent_window_s": 300}`. `recent_window_s` is how far back `POST /api/look-back` can see. `lag_s` is how far behind live the analysis is: the age of the oldest upload still waiting or being analysed, or when idle, the delay of the last analysed item. |
| `POST /api/devices` `{"fcm_token": "…"}` | **200** `{"registered": true, "devices": 1}`: the phone's Firebase token; the core pushes heads-up, verdict and alert events to it when push is configured.<br>**400** `bad_token`. |
| `GET /api/rtc/ice-servers` | **200** `{"ice_servers": [{"urls": "stun:…"}, {"urls": "turn:…", "username": "…", "credential": "…"}]}`: the STUN/TURN servers (RTCIceServer shape) a live viewer such as the phone app uses for `/api/rtc/{camera_id}/offers`, the same list camera pages get at registration. |
| `GET /api/cameras` | **200** `{"cameras": [Camera…]}`. `online` means a heartbeat within the last 10 s. Removed cameras aren't listed. |
| `PATCH /api/cameras/{id}` `{"label"}` | **200** `{"camera": Camera}`: a new label; the id and past events stay, and the camera page picks the new label up at its next heartbeat. Registering the new label (`POST /api/cameras`, any case) reuses this camera.<br>**400** `bad_label`. **404** `unknown_camera`. **409** `label_taken` (another camera has it). |
| `DELETE /api/cameras/{id}` | **200** `{"removed": "<id>"}`: the camera leaves the list and its thumbnail is deleted; its past events stay. Its page's next heartbeat gets **410** `camera_removed` and stops; starting the page again with the same label brings the camera back.<br>**404** `unknown_camera`. **409** `camera_in_use` (the active session's focus camera). |
| `GET /api/people` | **200** `{"people": [Person…]}`: named people by `person_id`, then unnamed people newest first. Unnamed people not seen for `MEANWHILE_FACE_UNNAMED_DAYS` are deleted (with their faces and photos) before listing. |
| `PATCH /api/people/{id}` `{"name", "role", "note"}` | Each field optional, but naming an unnamed person needs `name` (`role` then defaults to `family`). A new name gives the person the id that name makes: their faces, photos, past events (ids and summaries), session holder and votes move to it.<br>**200** `{"person": Person, "renamed_from": "unnamed-4f1b83"}` (`renamed_from` only when the id changed).<br>**400** `bad_name`, `bad_role`, `bad_note`. **404** `not_found`. **409** `name_taken` (with `into`: that person's id; merge instead) or `owner_exists`. **503** `face_id_unavailable`. |
| `POST /api/people/{id}/merge` `{"into": "owner"}` | The same person seen twice: this person's faces, photos and past events go to `into`, and this id is removed.<br>**200** `{"person": Person, "merged": "unnamed-4f1b83"}`.<br>**400** `bad_merge` (the same id twice). **404** `not_found`. **503** `face_id_unavailable`. |
| `DELETE /api/people/unnamed` | **200** `{"removed": ["unnamed-4f1b83", …]}`: every unnamed person, with their faces and photos.<br>**503** `face_id_unavailable` (with `removed` so far). |
| `POST /api/people` | JSON `{"name", "role", "note"}`, or `multipart/form-data` with the same fields and 1 to 3 `photo` files (JPEG or PNG, up to 15 MB each). Registers or updates the person (`person_id` comes from the name). When the core's `MEANWHILE_OWNER_ID` is set, that person is always the owner (spoken as "you") and nobody else can be. Photos replace earlier ones; with face ID on, each photo's largest face is indexed for that person.<br>**200** `{"person": Person}`.<br>**400** `bad_name`, `bad_role`, `bad_note`, `bad_photo`, `no_face` (a photo shows no face; nothing is saved).<br>**409** `owner_exists`. **413** `bad_size`. **503** `face_id_unavailable`. |
| `DELETE /api/people/{id}` | **200** `{"removed": "owner"}`: the person, their photos and every face indexed for them.<br>**404** `not_found`. **503** `face_id_unavailable` (nothing is removed). |
| `GET /api/events/latest?action=left&actor_role=owner` | **200** `{"event": Event \| null}`: the newest matching event across all sessions. Filters (all optional, ANDed): `action`, `actor_id`, `actor_role`, `camera_id`. |
| `POST /api/look` `{"question": "do you see me?", "camera": "desk"?}` | Answers any question about what a camera sees right now, with or without an active session. `camera` (a label or id, "the desk camera" works too) picks one camera, online or not; without it every online camera is looked at, in one model call. Each camera's newest frame is used: its live frame while the detector follows it, else its last heartbeat thumbnail. People are named only by face (the owner as "you", others by name or "an unnamed person (Person 3)"); anyone else is "someone". Takes a few seconds (face ID plus the vision model).<br>**200** `{"question": "…", "answer": "Yes, I can see you at the desk.", "camera_id": "desk", "camera": "Desk", "online": true, "frame_at": "…Z", "frame_url": "/media/looks/desk/….jpg", "frame_source": "live\|thumbnail", "people": [{"person_id": "owner", "name": "Owner", "role": "owner", "camera_id": "desk"}], "looked_at": [{"camera_id", "camera", "online", "frame_at", "frame_url", "frame_source"}…], "took_s": {"faces": 0.4, "model": 2.1}}`. `answer` is one or two sentences, ready to speak; the top-level camera fields are the frame the answer is about, and `looked_at` lists every frame looked at (each kept under `/media/looks/`).<br>**400** `bad_question`. **404** `unknown_camera` (the message lists the cameras). **409** `no_frame` (no camera on, or the camera hasn't sent a picture). **503** `model_unavailable`. Every error's `message` is a sentence Alexa can say. |
| `POST /api/look-back` `{"question": "what did I do?", "camera": "desk"?}` | Answers a question about what happened in the recent past, with or without a session. Every camera keeps its heartbeat thumbnails of the last `MEANWHILE_RECENT_WINDOW_S` seconds (at most `MEANWHILE_RECENT_MAX_FRAMES` each, under `/media/recent/`, deleted with the camera); up to `MEANWHILE_RECENT_MODEL_FRAMES` of them, spread over the window and over the named camera or every camera with recent frames, go to the video model with how long ago each was taken. People are named only by face, as in `POST /api/look`. Nothing older than the window is seen.<br>**200** `{"question": "…", "answer": "In the last 5 minutes, you sat down at the desk.", "asks_earlier": false, "window_s": 300, "window": "5 minutes", "camera_id": "desk", "camera": "Desk", "online": true, "frame_at": "…Z", "frame_url": "/media/looks/desk/….jpg", "frame_source": "recent", "people": [{"person_id", "name", "role", "camera_id"}…], "looked_at": [{"camera_id", "camera", "frames": 8, "kept": 97, "from": "…Z", "to": "…Z"}…], "took_s": {"faces": 0.6, "model": 3.1}}`. `asks_earlier` is true when the question was about a time before the window (the answer covers the window only); `window` is the window in words, for speech; the camera and frame fields are the frame that best shows the answer.<br>**400** `bad_question`. **404** `unknown_camera`. **409** `no_frames` ("I don't have any pictures from any camera in the last 5 minutes."). **503** `model_unavailable`. Every error's `message` is a sentence Alexa can say. |
| `POST /api/state-of` `{"question": "is the front door locked?", "thing": "the front door", "camera": "door"?}` | How a thing is. `thing` matches stored statuses by its words without my/the/our ("door" finds "the front door"), on the named camera if one is given.<br>**200** `{"source": "state", "thing": "the front door", "value": "locked", "at": "…Z", "checked_by": "event|video", "by": "ravi", "by_name": "Ravi", "by_role": "helper", "camera_id": "door", "camera": "Door", "event_id": "evt_…", "clip_url": "/media/clips/door/….mp4"}`: the stored status is conclusive (it says something, every camera's newest status of that thing agrees, and it is no older than `MEANWHILE_STATE_MAX_AGE_S`); no model is asked. `checked_by` is `event` for a change seen during a watch (with who and the clip), `video` for an earlier video check.<br>**200** `{"source": "video", "thing", "value": "open" \| null, "stored": true, "answer": "…", "camera_id", "camera", "frame_at", "frame_url", "people", "took_s"}`: otherwise the video model checks the frames (the last change's clip when that change is fresh but unclear, else the recent window, else the newest frame; of the named camera, the last change's camera, a camera whose label is in `thing`, or every online camera). When it is confident, its conclusion is stored as the status (`stored: true`, `checked_by: "video"`, at the time of the frames it saw), so the next question answers from it.<br>Frames older than `MEANWHILE_STATE_MAX_AGE_S` (an offline camera's last thumbnail) are never checked or stored.<br>**400** `bad_question`. **404** `unknown_camera`. **409** `no_frame`, or `stale_frame` with `camera` and `last_frame_at` (the only pictures are too old to tell). **503** `model_unavailable`. |

## Device pairing
A new phone or browser connects by scanning a one-time QR code shown on a device that is already paired, so the shared key is never shown, typed or put in a URL.

| Method and path | Returns |
|---|---|
| `POST /api/pairing-codes` (key or device token) | **201** `{"code": "…", "pair_url": "https://<host>/pair?code=…", "expires_at": "…Z", "expires_in_s": 120, "qr_svg": "<svg …>"}`: a one-time code that works once, for `MEANWHILE_PAIR_CODE_TTL_S` seconds. `pair_url` starts with `MEANWHILE_PUBLIC_URL` (else the request's own address) and opens the browser pairing page; `qr_svg` is `pair_url` as an inline SVG QR code. Show the QR code with a countdown from `expires_in_s` (the device clock may differ from `expires_at`). |
| `POST /api/pair` `{"code", "device_name", "browser"?}` (no key needed) | **201** `{"device_id": "dev_…", "id", "name", "kind": "app\|browser", "created", "last_seen", "current": false, "device_token": "…"}`. If the request carries a paired device's token (a browser's old `mw_key` cookie, or the app's old token in `X-Meanwhile-Key`), that device is pairing again: its old entry is removed and its old token stops working. `name` is unique: a name another device already has (any case) gets " (2)", " (3)"…. The app keeps `device_token` privately and sends it in `X-Meanwhile-Key`; it never expires but can be revoked. With `"browser": true` the token is not in the body: it comes only as the cookie `mw_key` (`HttpOnly; Secure; SameSite=Lax; Path=/`, `Max-Age` from `MEANWHILE_PAIR_COOKIE_DAYS`, and `Domain=MEANWHILE_PAIR_COOKIE_DOMAIN` when set, covering its subdomains).<br>**400** `bad_json`, `bad_code`, `bad_name` (1 to 60 characters). **403** `invalid_code` (wrong, used or expired). **429** `too_many_tries` with `retry_after_s` and `Retry-After`: more than `MEANWHILE_PAIR_TRIES` attempts from one address in `MEANWHILE_PAIR_TRIES_WINDOW_S` seconds. |
| `GET /api/paired-devices` | **200** `{"devices": [Device…]}` oldest first: `device_id`, `id`, `name`, `kind`, `created`, `last_seen` (to the minute) and `current` (the caller's own device). Tokens are never listed. |
| `DELETE /api/paired-devices/{id}` | **200** `{"revoked": true, "device_id": "dev_…"}`: its token stops working at once.<br>**404** `not_found`. |
| `GET /api/auth/check` | **204** when the request carries valid credentials, or when `X-Forwarded-Uri` names a `/media` path with a valid signed link; else **401** (or **429** while the client is blocked). A reverse proxy asks this to gate every request, passing the original request in `X-Forwarded-Uri` and the client in `X-Forwarded-For`. |

- **Browser pairing page:** `GET /pair?code=…` (open, never cached, `Referrer-Policy: no-referrer`). It removes the code from the address bar, asks for a device name and pairs on one tap, so the cookie is set by that `POST /api/pair` response with no redirect before it. It needs HTTPS (or localhost) for the `Secure` cookie.
- **The app:** scan the QR code, take `code` from `pair_url`'s query and the hub (`https://<host>`) from the rest, then `POST /api/pair` with `device_name` and without `browser`.
- Codes and tokens are stored only as SHA-256 hashes.

## Push
When `MEANWHILE_FCM_SERVICE_ACCOUNT` points at a Firebase service-account file, each event written with `alert` of `heads_up`, `verdict` or `alert` is sent to every registered device (FCM HTTP v1). The message has a notification (title "Heads-up", "Verdict" or "Alert"; body is the summary), Android channel id equal to the alert, and string data `event_id`, `summary`, `alert`, `frame_url` (a path under the core's base URL, or empty) and `session_id`. Without the file, push is off.

## Things' states
There is no door-only kind. "Watch the front door and tell me when it's locked or unlocked" is an ordinary `monitor` (or `notes`) order whose `clip_question`, `report` and `alert_when` come from the user's words; a camera whose label is in the instruction ("door" → the camera labelled Door) is the one watched. When the video model reports an event that leaves something in a new state, the event carries `thing` (in the user's words) and `state` (one or two words), and the core records that state per camera and thing. `POST /api/state-of` answers from those records when they are conclusive, and otherwise checks the video and stores what it confidently sees.
