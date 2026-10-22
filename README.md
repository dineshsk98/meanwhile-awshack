# Meanwhile

Cameras record everything, but understanding live footage as it happens, across several cameras at once, is still hard. Meanwhile is built to close that gap: it watches every camera feed together, follows what happens from one camera to the next, and tells you what it means while it is still happening.

The longer aim is video intelligence for every kind of video, from live camera feeds to streams and recordings, that you can simply talk to through AI agents.

This submission is the first step: connect any number of cameras with no setup friction, and give Alexa an instruction in your own words. It already covers three uses: a door alert, a focused watch on something you care about, and a verdict built from what several cameras saw.

Built for the Amazon "Build, Ship, Shape" Developer Hackathon (Alexa+ track, AWS Builder mini challenge).

```powershell
git clone https://github.com/dineshsk98/meanwhile-awshack.git  
```

## What's here
```
backend/          the core (Python): the HTTP API, the analyser, face ID, device pairing; tests/
web/              the core's pages: /camera (a browser or phone as a camera), /debug.html, /pair
voice/            the Alexa+ side: the MCP server at /mcp, the home screen and a simulated Alexa+ panel (voice/README.md)
android/          the Android app: sessions, alerts, cameras, people, pairing (android/README.md)
infra/cloud/      the AWS box: deploy.ps1 (run on the laptop), install.sh and box-update.sh (run on the box), Caddy,
                  systemd units, settings templates (*.env.example), the face ID IAM policy
infra/sagemaker/  the SageMaker vision endpoint: deploy.py, delete.py, invoke_test.py
architecture/     the architecture images and the scripts that draw them
CONTRACT.md       the core's HTTP API, used by the voice side and the Android app
start-local.ps1   starts everything on a Windows laptop
```
How the pieces fit together today, and a plan for running it for many homes: [ARCHITECTURE.md](ARCHITECTURE.md).
Everything that works today, and how each piece was verified: [SCENARIOS.md](SCENARIOS.md).

**MCP server (Alexa+):** `/mcp` on the voice side, over Streamable HTTP, built on the official MCP Python SDK
(`mcp` 2.3.0), which implements MCP spec 2025-11-25 (and negotiates older clients' versions, such as `2025-03-26`).
The tools are listed in [voice/README.md](voice/README.md).

## Prerequisites
- Windows 10/11 with PowerShell (on Linux, use `.venv/bin/python` for `.venv\Scripts\python` and start the two
  servers by hand).
- An NVIDIA GPU with at least 6 GB of memory and a current driver.
- Python 3.11 and Git.
- [Ollama](https://ollama.com), running.
- Chrome (speech on the home screen, and the camera pages).
- Optional: an AWS account with Amazon Rekognition for face ID; Android Studio and a Firebase project for the app.

## Run locally
**Once**, from the repo root:
1. Create the two virtualenvs. Install the CUDA build of PyTorch first (pip's default wheel on Windows is CPU-only):
   ```powershell
   python -m venv .venv
   .venv\Scripts\python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu130
   .venv\Scripts\python -m pip install -r requirements.txt
   python -m venv voice\.venv; voice\.venv\Scripts\python -m pip install -r voice\backend\requirements.txt
   ```
2. Copy `.env.example` to `.env` and `voice\.env.example` to `voice\.env`. Then put the same random key into both
   (the core won't start without one; this doesn't print it):
   ```powershell
   $b = New-Object byte[] 30; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b); $k = [Convert]::ToBase64String($b) -replace '[+/=]', ''
   (Get-Content .env) -replace '^MEANWHILE_KEY=.*', "MEANWHILE_KEY=$k" | Set-Content .env
   (Get-Content voice\.env) -replace '^MEANWHILE_CORE_KEY=.*', "MEANWHILE_CORE_KEY=$k" | Set-Content voice\.env; Remove-Variable b, k
   ```
   Face ID is off in `.env.example` (`MEANWHILE_FACES=off`), so nothing needs AWS. To turn it on, see
   [Face ID](#face-id-optional).
3. Pull the model: `ollama pull qwen3.5:4b` (about 3.3 GB).
4. Download the detector weights (about 330 MB) into `weights\detector\`:
   ```powershell
   .venv\Scripts\python -c "from ultralytics.utils.downloads import attempt_download_asset as d; [d(f'weights/detector/{n}') for n in ('yoloe-26m-seg.pt', 'mobileclip2_b.ts')]"
   ```

**Start everything:**
```powershell
powershell -ExecutionPolicy Bypass -File start-local.ps1
```
It checks Ollama, the model, the key and the ports, starts the core and the voice side in their own windows, and opens
Chrome with a pairing link, the home screen, the laptop camera page and the debug page. Press **Pair** on the first
tab, then reload the other tabs. `-CheckOnly` runs only the checks; `-NoChrome` skips Chrome.

**Or by hand**, one window each:
```powershell
cd backend; ..\.venv\Scripts\python -m local.devserver --lan
cd voice\backend; ..\.venv\Scripts\python -m meanwhile.voice.devserver
```

| What | Where |
|---|---|
| Home screen with the simulated Alexa+ panel | `http://127.0.0.1:8000` |
| MCP endpoint | `http://127.0.0.1:8000/mcp` |
| Core API, debug page | `http://127.0.0.1:8100`, `http://127.0.0.1:8100/debug.html` |
| Laptop camera | `http://127.0.0.1:8100/camera?label=Desk` |
| Phone cameras | `https://<laptop-ip>:8443/camera?label=<name>` (the `Phones:` line the core prints) |

**Phone cameras:** on the debug page, **Pair a device** shows a one-time QR code. Scan it with the phone, press
**Pair**, then open the phone camera link above and allow the camera. The phone shows a certificate warning once
(the core makes a self-signed certificate with `--lan`). The laptop's Wi-Fi must be a Private network, with Python
allowed through Windows Firewall.

**Without a camera:** `/camera?label=Desk&test-video=/media/test/clip.mp4` plays a looping video file as the camera
(put it in `backend\.data\media\test\`).

## Try it
With everything running locally (above) and the laptop camera page open:
1. Put an object (keys, a controller, a cup) where the Desk camera sees it, at rest. Wait a few seconds.
2. On the home screen (`http://127.0.0.1:8000`), press **Start** and say, or type into the panel:
   "Alexa, I'm stepping away, keep an eye on my keys."

   Expect: Alexa answers that it is keeping an eye on your keys on the Desk camera, the home screen shows the session,
   and the camera page draws a box around the object.
3. Pick it up and carry it out of view. With face ID off, the tracker recognises nobody, so you can test this alone.
   Expect a **heads-up** about 4 s after it leaves view ("Someone I don't recognise carried your keys out of view."),
   then "still tracking" at 15 s and "taken" at 30 s if it stays away. Put it back: the **verdict** says it is back,
   not taken. Picking it up and putting it down in view is logged quietly.
4. Ask "What did I miss?" for the spoken catch-up, "Show me" to play the clips, and "I'm back" to end the session.
   "Do you see me?" and "What did I just do?" answer from the cameras now and from the last few minutes.

With face ID on, register yourself on the debug page with the role `owner`: you then get no alerts for your own moves,
so have someone else take the object.

The debug page shows the same session, its events and the analyser's queue. Any MCP client can call the tools too,
e.g. the MCP Inspector (`npx @modelcontextprotocol/inspector`): Streamable HTTP, `http://127.0.0.1:8000/mcp`.

**Tests** (fast ones; drop `-m "not model"` to also ask the local model):
```powershell
cd backend; ..\.venv\Scripts\python -m pytest -q -m "not model"
cd voice\backend; ..\.venv\Scripts\python -m pytest -q -m "not model"
```
The app's unit tests: `cd android; .\gradlew.bat testDebugUnitTest` (see [android/README.md](android/README.md)).

## Face ID (optional)
1. In your AWS account, create a Rekognition collection with a name of your own:
   `aws rekognition create-collection --collection-id <collection> --region <region> --profile <profile>`.
2. In `.env`, set `MEANWHILE_FACES=on`, `MEANWHILE_FACE_COLLECTION` (that name), `MEANWHILE_AWS_PROFILE` and
   `MEANWHILE_AWS_REGION`. The profile needs the permissions in `infra/cloud/faces-policy.json`.
3. Restart the core, then add people with up to 3 photos each on the debug page.

## Build the Android app
1. Open `android/` in Android Studio.
2. Copy `android/local.properties.example` to `android/local.properties` and fill it in.
3. In a Firebase project, add an Android app with the package name `com.meanwhile.android` and put its
   `google-services.json` into `android/app/`. For push from the core, create a service-account key (Project
   settings → Service accounts) and set `MEANWHILE_FCM_SERVICE_ACCOUNT` to its path; without it, push is off.
4. **Run** in Android Studio, or `cd android; .\gradlew.bat installDebug` with a phone on USB debugging.
5. In the app, pair by scanning a QR code from **Pair a device** on a paired browser.

Details: [android/README.md](android/README.md).

## Your own setup
Each setup is one home and is independent of every other: nothing is shared with this project or another home.
Each setup creates its own:
- **key** (`MEANWHILE_KEY`), from which paired devices' tokens and signed media links follow;
- **Rekognition collection** (`MEANWHILE_FACE_COLLECTION`, a name you choose) if face ID is on;
- **Firebase project** with its own `google-services.json` and service-account key, for push;
- **SageMaker endpoint** (`MEANWHILE_SM_NAME`) if you use one;
- **host name** and certificates (`MEANWHILE_CLOUD_PUBLIC_HOST`) on AWS.

## Set up and run on AWS
One EC2 GPU instance runs Caddy (HTTPS and the access gate), the core, the voice side and Ollama; the vision model can
run on an Amazon SageMaker endpoint instead. Face ID uses Amazon Rekognition. Create the AWS resources in the console;
the scripts install and update the box.

**1. AWS resources** (one region):
- **IAM role** for EC2 (e.g. `meanwhile-core-ec2`) with two inline policies: `infra/cloud/faces-policy.json` (fill in
  the region, account id and collection name), and `sagemaker:InvokeEndpoint` on
  `arn:aws:sagemaker:<region>:<account-id>:endpoint/<MEANWHILE_SM_NAME>` if you use the SageMaker endpoint.
- **Rekognition collection** with a name of your own:
  `aws rekognition create-collection --collection-id <collection> --region <region>`.
- **Key pair** (ED25519), saved as `<MEANWHILE_SECRETS_DIR>\<MEANWHILE_CLOUD_NAME>.pem`, readable only by you:
  `icacls <file> /inheritance:r /grant:r "$($env:USERNAME):(R)"`.
- **Security group:** SSH 22 from your IP only; TCP 443 and 80 (HTTPS and certificate issuance) and UDP 32768-60999
  (WebRTC) from anywhere. The app, browsers and MCP clients all use `https://<host>`; TCP 8100 and 8000 serve only
  older app builds that call plain HTTP.
- **EC2 instance:** g4dn.xlarge (NVIDIA T4); AMI "Deep Learning OSS Nvidia Driver AMI GPU PyTorch" (Ubuntu); 75 GiB
  gp3; the role above as its instance profile; shutdown behaviour **Stop**; IMDSv2 only.
- **Elastic IP** on the instance, and DNS A records for your host name and for `alexa.<host>` pointing at it.
- **SageMaker endpoint** (optional): set the `MEANWHILE_SM_*` settings in `.env`, then
  `.venv\Scripts\python infra\sagemaker\deploy.py` (10-15 minutes; it creates the execution role, the model and the
  endpoint). Check it with `.venv\Scripts\python infra\sagemaker\invoke_test.py <folder of JPEGs>`. The endpoint quota
  for `ml.g6.xlarge` may start at 0: request one in Service Quotas first.

**2. Laptop settings** in `.env`: `MEANWHILE_CLOUD_NAME`, `MEANWHILE_SECRETS_DIR`, `MEANWHILE_CLOUD_PUBLIC_IP` (the
Elastic IP) and `MEANWHILE_CLOUD_PUBLIC_HOST` (the host name).

**3. First install** (packs the working tree with the detector weights, copies it to the box and runs `install.sh`):
```powershell
powershell -ExecutionPolicy Bypass -File infra\cloud\deploy.ps1 -Install -WithWeights
```
Then over SSH (`ssh -i <key file> ubuntu@<elastic-ip>`):
1. `sudo nano /etc/meanwhile/key.env`: keep the random key `install.sh` put there, or set your own (16+ characters).
2. `sudo nano /etc/meanwhile/core.env`: set `MEANWHILE_FACE_COLLECTION` to your collection; for the SageMaker
   endpoint, set `MEANWHILE_VISION_MODEL` and `MEANWHILE_VIDEO_MODEL` to `sagemaker:<MEANWHILE_SM_NAME>`. Once you've
   added yourself as a person, set `MEANWHILE_OWNER_ID`.
3. Push alerts: `scp` the Firebase service-account file to `/tmp/fcm.json` on the box, then
   `sudo install -m 600 -o ubuntu /tmp/fcm.json /etc/meanwhile/fcm.json && rm /tmp/fcm.json`.
4. `sudo systemctl restart caddy meanwhile-core meanwhile-voice`.

**4. Every update after that:**
```powershell
powershell -ExecutionPolicy Bypass -File infra\cloud\deploy.ps1
```

**5. Check it:**
- The deploy prints `active` for `caddy`, `meanwhile-core`, `meanwhile-voice` and `ollama`, the core's
  `Detector: … ready` line with nothing "not set", and `voice /voice/state: 200`.
- Without a key or token, `https://<host>/api/status` answers `unauthorized`, and so does any `https://<host>/media/…`
  path without the signed link the core hands out.

**6. Pair and use it:**
- **The first browser:** on the box, `bash ~/meanwhile/infra/cloud/pair-link.sh` prints a one-time pairing link; open
  it and press **Pair**.
- **Phones and other browsers:** `https://<host>/debug.html` → **Pair a device**, scan the QR code, press **Pair**.
  A phone camera is then `https://<host>/camera?label=<name>`.
- **The Android app:** pair it with a QR code the same way; it then talks to `https://<host>` only.
- **The simulated Alexa+ panel:** `https://alexa.<host>/`.
- **MCP (Alexa+ or any MCP client):** `https://<host>/mcp`, Streamable HTTP, with the key in the `X-Meanwhile-Key`
  header (or a paired device's token).

**Nightly stop:** `install.sh` installs and enables `meanwhile-nightly-stop.timer`, which shuts the box down (and so
stops the instance) every night at 01:00 India time. Start it again in the console. To keep it running, on the box:
`sudo systemctl disable --now meanwhile-nightly-stop.timer` (change `OnCalendar` in
`infra/cloud/meanwhile-nightly-stop.timer` for another time).

**Costs** (on-demand, ap-south-1): g4dn.xlarge about $0.58 per hour while running; ml.g6.xlarge about $1.35 per hour
while the endpoint exists; plus the disk and the Elastic IP. Stop the instance in the console when you're done.

**The project's own hub** is kept stopped to save cost and started on demand, and its SageMaker endpoint is not kept
running. To try the AWS path, set it up in your own account as above, or open a GitHub issue asking for the project's
hub to be started.

**Teardown:** `.venv\Scripts\python infra\sagemaker\delete.py` (the endpoint and its config), then in the console:
terminate the instance, release the Elastic IP, delete the security group, the key pair, the two IAM roles, the
SageMaker model and the collection (`aws rekognition delete-collection --collection-id <collection>`).

## Configuration
Every setting is in an `.env.example`, with what it means; every name must be present in the copy:
- `.env.example`: the core, plus the laptop's deploy and SageMaker settings (copy to `.env`).
- `voice/.env.example`: the voice side (copy to `voice/.env`).
- `infra/cloud/*.env.example`: the box's files in `/etc/meanwhile/` (`install.sh` creates them).
- `android/local.properties.example`: the app's build settings.

Models are chosen per step with `MEANWHILE_ORDER_MODEL`, `MEANWHILE_VISION_MODEL` and `MEANWHILE_VIDEO_MODEL`:
`ollama:<name>`, `ollama-cpu:<name>`, `gemini:<model-id>` (with `GEMINI_API_KEY`) or `sagemaker:<endpoint-name>`.

## Licence
Copyright (C) 2026 Dinesh Kumar Saravanan. AGPL-3.0: see [LICENSE](LICENSE). Third-party software and models, and
their licences, are listed in [NOTICE](NOTICE).
