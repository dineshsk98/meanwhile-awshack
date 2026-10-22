# Meanwhile: voice

The Alexa+ side of Meanwhile: a self-hosted MCP server that Alexa+ calls, the home screen its tools drive (session
card, clip replay, alert banners), and a simulated Alexa+ panel you can talk or type to. It reads the Meanwhile core
only through the HTTP API in [`CONTRACT.md`](../CONTRACT.md).

## MCP server
- **Endpoint:** `/mcp` over Streamable HTTP (stateless, JSON responses), built on the official MCP Python SDK
  (`mcp` in `backend/requirements.txt`). It negotiates MCP spec `2025-11-25` and also accepts Alexa+'s `2025-03-26`
  initialize.
- **Tools:**

| Tool | Use it for |
|---|---|
| `step_away(instruction)` | "I'm stepping away, keep an eye on my keys": starts watching. |
| `what_did_i_miss(detail?)` | "What did I miss?": the events not told yet (each session's events are told once, marked with the core's caught-up cursor); `detail` reads every one. With nothing untold: the last few minutes, else "nothing new". |
| `show_me(event?)` | "Show me the hand-over": plays the clips on the home screen. |
| `im_back()` | "I'm back": ends the session. |
| `status()` | "What are you watching?": the session and which cameras are live. |
| `ask_state(question, thing, camera?)` | "Is the front door locked?", "is the lamp on?": the last recorded change of that thing (when and who), else the video. A camera whose label is in the question answers it: label one camera "Door" and door asks refer to it (see `SCENARIOS.md`, "Try it: the door lock"). |
| `look(question, camera?)` | "Do you see me?": what a camera sees right now. |
| `look_back(question, camera?)` | "What did I just do?": the cameras' last few minutes. |

Each tool returns structured content plus `spoken`, the sentence to say.

## Run locally
Set up both venvs and both `.env` files first, as in the root [README](../README.md#run-locally). Configuration is in
[`.env.example`](.env.example): copy it to `voice/.env` and set `MEANWHILE_CORE_KEY` to the core's `MEANWHILE_KEY`.
Every setting is required.

Start everything (Ollama checks, the core, this voice side and Chrome) from the repo root:
```powershell
powershell -ExecutionPolicy Bypass -File .\start-local.ps1
```
Or start the voice side by hand, after the core, from `voice\backend`:
```powershell
..\.venv\Scripts\python -m meanwhile.voice.devserver
```
`--port N` overrides `MEANWHILE_VOICE_PORT`; `--lan` listens on the Wi-Fi too. Wait for `Alexa's model is ready` in
its log.

| What | Where |
|---|---|
| Home screen and simulated Alexa+ panel | `http://127.0.0.1:8000` |
| MCP endpoint (for MCP clients) | `http://127.0.0.1:8000/mcp` |

To run only the MCP server, without the home screen (same port, so one or the other):
```powershell
..\.venv\Scripts\python -m meanwhile.voice.mcp_server
```

## Try it
1. Open `http://127.0.0.1:8000` in **Chrome**, press **Start** in the header and allow the microphone.
2. Say "Alexa, I'm stepping away, keep an eye on my keys", or type it in the panel's text box and press Enter.
   Space (or the ring) also starts listening without the wake word.
3. Expect: Alexa says what it is watching, the panel shows the `step_away` call with its timings, and the card shows
   the session. Then try "what did I miss?", "show me", "do you see me?" and "I'm back".

Listening uses Chrome's speech recognition, which needs internet; typing works without it. To let Chrome speak
without a click, start it with `--autoplay-policy=no-user-gesture-required`.

## Connect an MCP client or Alexa+
- **Any MCP client**, e.g. the MCP Inspector: run `npx @modelcontextprotocol/inspector`, open the link it prints,
  choose Streamable HTTP and enter `http://127.0.0.1:8000/mcp`.
- **Alexa+** needs a public HTTPS address. Deploy as in the root README's
  [Set up and run on AWS](../README.md#set-up-and-run-on-aws), then register `https://<host>/mcp` (Streamable HTTP)
  with the `X-Meanwhile-Key` header set to the core's key.

## Tests
From `voice\backend`:
```powershell
..\.venv\Scripts\python -m pytest -q -m "not model"
```
The fast tests (about 30 s) need nothing running. All tests, including those that ask the local model (skipped if
Ollama isn't running):
```powershell
..\.venv\Scripts\python -m pytest -q
```

### Scenario dataset
`backend/tests/scenarios/` is a test and evaluation dataset, not part of the product: `data.py` holds three cameras
(Desk, Room, Door), two fictional people (the owner, spoken as "you", and Ravi, a helper) and three scripted scenarios
(object, notes, door); `fake_core.py` plays them to the voice side over `CONTRACT.md`'s API, in memory. The fast
tests run every scenario. To evaluate the simulated Alexa+'s tool choice on the real model:
```powershell
..\.venv\Scripts\python -m pytest -q -m model tests\test_alexa_sim.py
```

## Troubleshooting
| You see | Do |
|---|---|
| "I can't reach the Meanwhile core." on the page | Start the core; check `curl.exe -s http://127.0.0.1:8100/api/cameras`. |
| "Sorry, I can't reach Meanwhile right now." in the panel | Check the voice devserver is running and Ollama is up (`ollama ps`). |
| The header button names a problem (no voices, microphone blocked, listening needs internet) | Fix that one; for the microphone, allow it from the icon left of the address and reload. |
| A setting `is not set` at startup | Add it to `voice/.env` (see `.env.example`). |
| Port 8000 already in use | Another copy is running; close it, or start with `--port`. |
| Old page after an update | Reload (Ctrl+F5). |
