# Friction log

Problems with Amazon and AWS tools, APIs and consoles hit while building Meanwhile: what was attempted, what happened, how it was worked around, and what would fix it. Severity is the impact on the project: **High** blocked a planned service, **Medium** forced a change of plan, **Low** cost time.

| # | Area | Severity |
|---|---|---|
| 1 | Amazon Bedrock: model access | High |
| 2 | Amazon SageMaker: endpoint quota | High |
| 3 | Amazon EC2: GPU capacity | Medium |
| 4 | Amazon Nova and Twelve Labs Pegasus: video quality | Medium |
| 5 | Amazon Rekognition: tight face crops | Medium |
| 6 | Amazon Route 53: domain registration | Medium |
| 7 | Amazon EC2: SSH key on Windows | Low |
| 8 | AWS CLI and boto3: `aws login` | Low |

---

## 1. Amazon Bedrock: model access
- **Task:** use a managed video-understanding model (Amazon Nova or TwelveLabs Pegasus) through Bedrock to read camera clips.
- **Steps:** opened Bedrock in the account to call the models.
- **Expected:** model access within the hackathon window. **Actual:** no model access on the account in time.
- **Severity:** High.
- **Workaround:** compared the models directly (Pegasus through the Twelve Labs API, Nova on nova.amazon.com), then hosted an open vision model on a SageMaker endpoint instead.
- **Suggestion:** pre-enabled or same-day access to the models a challenge names, for registered participants.

## 2. Amazon SageMaker: endpoint quota shows APPROVED but applies as 0
- **Task:** create a real-time GPU endpoint (ml.g6.xlarge or ml.g5.xlarge) for the vision model.
- **Steps:** requested both "for endpoint usage" quotas with a desired value of 1; both showed APPROVED. Read the applied value with `aws service-quotas get-service-quota` over the following days.
- **Expected:** applied value 1. **Actual:** applied value **0.0** for five days, while `list-requested-service-quota-change-history` still said APPROVED. A support case fixed it the next day.
- **Severity:** High: the service the AWS Builder entry runs on was unavailable for five days.
- **Workaround:** ran the vision model on Ollama on the EC2 box until the quota applied. After that, the endpoint went from Creating to InService in under 10 minutes.
- **Suggestion:** don't show APPROVED until the applied value has changed (or show both), and flag or auto-escalate requests that haven't applied after a set time.

## 3. Amazon EC2: no GPU capacity, and no way back after Stop
- **Task:** launch one GPU instance in ap-south-1.
- **Steps:** console launch of g6.xlarge in ap-south-1a and 1b, then g5.xlarge. The G and VT vCPU quota was already approved.
- **Expected:** a running instance. **Actual:** `InsufficientInstanceCapacity` in every zone for both types. g4dn.xlarge launched. The next day, **Start** on the stopped g4dn failed with insufficient capacity too.
- **Severity:** Medium.
- **Workaround:** g4dn.xlarge (NVIDIA T4), an older and slower GPU; the instance was kept running on key days rather than risk a Stop.
- **Suggestion:** a per-zone capacity hint for GPU families in the launch wizard; on Stop for a GPU instance, warn that restarting isn't guaranteed and offer to create an AMI first.

## 4. Amazon Nova and Twelve Labs Pegasus: compressed video hallucinates
- **Task:** choose a model to describe short home-camera clips with timestamps.
- **Steps:** sent two versions of the same clip: the original webcam recording (720p, 24 fps) and a copy reduced to 480p, 8 fps with OpenCV to cut upload size and latency. Ran Pegasus 1.5 (Twelve Labs API) and Amazon Nova (low reasoning).
- **Expected:** similar answers, with the smaller file faster. **Actual:** the original gave correct events with good timestamps; the reduced copy hallucinated badly. Nova on low reasoning was faster and more detailed, with more about the surroundings. Pegasus took 46–260 s per 8 s clip.
- **Severity:** Medium: too slow for live alerts, so live tracking moved to a detector and tracker, and the model is called only on changes.
- **Workaround:** send full-quality frames, and only when something changes.
- **Suggestion:** document the minimum resolution and frame rate each video model is evaluated at, and a low-latency mode for short clips.

## 5. Amazon Rekognition: tight face crops return "no faces"
- **Task:** recognise a person from the crop a tracker produces.
- **Steps:** `SearchFacesByImage` on a tight 70×90 crop of a clearly visible face.
- **Expected:** a match. **Actual:** "There are no faces in the image", while the full frame matched the same face at 99.5%.
- **Severity:** Medium.
- **Workaround:** send a crop with margin (160×200 or the upper body).
- **Suggestion:** document the minimum face size and margin in pixels, and return a distinct error ("face too small or too tightly cropped") instead of "no faces". A way to change which person a face belongs to without re-indexing would also help.

## 6. Amazon Route 53: availability check and registration give no reason
- **Task:** register a domain for the hub's HTTPS address.
- **Steps:** `aws route53domains check-domain-availability` for several names; then registered `meanwhile.click` in the console.
- **Expected:** an availability answer and a registered domain. **Actual:** `DONT_KNOW` for every name, twice (while `list-prices` worked in the same session). The registration went to FAILED in under a second with only "Contact AWS Support".
- **Severity:** Medium.
- **Workaround:** a free wildcard DNS name (sslip.io) with a Let's Encrypt certificate.
- **Suggestion:** return the reason for a failed registration, and make the CLI availability check answer.

## 7. Amazon EC2: the console's `.pem` doesn't work with Windows OpenSSH
- **Task:** SSH from Windows to the new instance.
- **Steps:** downloaded the key pair from the console; ran the usual `icacls /inheritance:r /grant:r "<user>:(R)"`.
- **Expected:** a connection. **Actual:** `WARNING: UNPROTECTED PRIVATE KEY FILE! … Try removing permissions for user: NT AUTHORITY\Authenticated Users (S-1-5-11)`, then `Permission denied (publickey)`.
- **Severity:** Low.
- **Workaround:** also remove the Authenticated Users, Users and Everyone groups by SID (`/remove:g *S-1-5-11 *S-1-5-32-545 *S-1-1-0`).
- **Suggestion:** a one-line PowerShell command next to the key-pair download and in Connect → SSH client.

## 8. AWS CLI and boto3: `aws login` sessions
- **Task:** keep a working CLI and boto3 session through a day of development.
- **Steps:** `aws login --profile <profile>`, then CLI and boto3 calls.
- **Expected:** a session that lasts a working day. **Actual:** "Your session has expired" several times a day, mid-task. Once, the login looked done but the browser step hadn't completed, so the CLI still said expired. boto3 failed with `MissingDependencyException … pip install "botocore[crt]"` where the CLI worked.
- **Severity:** Low.
- **Workaround:** re-run the login and finish the browser step; `boto3[crt]` in `requirements.txt`.
- **Suggestion:** exit non-zero when the browser step didn't complete, allow longer sessions for development profiles, and mention `botocore[crt]` on the `aws login` page.
