# What works

Every scenario that works today: what you do or say, what Meanwhile does or answers, and how it was verified.
Quoted replies are real ones that were observed.

**Verified:** **Box** = live on the AWS box (EC2 GPU instance) with phones and a laptop browser as cameras ·
**Laptop + SageMaker** = a core on the laptop calling the SageMaker endpoint (and Rekognition where faces are named) ·
**Laptop** = a core on the laptop (with Rekognition or a browser where noted) ·
**Replay** = a recorded run replayed through the core · **Tests** = automated tests only.
All voice checks used the simulated Alexa+ panel or MCP clients (see "Not yet").

## Setup and pairing
| You do or say | Meanwhile does or answers | Verified |
|---|---|---|
| Deploy with `infra/cloud/deploy.ps1` | Packs the code, updates the box, restarts the services and prints their health | Box |
| Open `/camera?label=Desk` in a laptop browser | The browser becomes a live camera named Desk | Box |
| Scan a pairing QR code with a phone, press **Pair**, open the camera page | The phone becomes a camera; the shared key is never shown or typed | Box |
| Pair the Android app by scanning the QR code | The app gets its own device token; the code works once, for 2 minutes, with a countdown | Box |
| Pair the same device again | Its old entry is replaced; names stay unique | Tests |
| Rename or remove a camera (app menu or the camera page) | Same camera id and past events under the new name; a removed camera's page stops | Laptop (browser), Tests |

## Watching an object
| You do or say | Meanwhile does or answers | Verified |
|---|---|---|
| "I am going out. Watch my controller" | Finds it on the cameras' latest frames and answers it is keeping an eye on it on that camera; the camera page draws its box | Box |
| Someone carries it out of view | Heads-up about 4 s later, pushed to the phone | Box |
| It shows up on another camera | Verdict "back in view" on that camera; "still tracking" and "taken" are cancelled | Box (verdict); Laptop + SageMaker, Tests (cancelling) |
| It stays away | "Still tracking" at 15 s, then "taken" at 30 s | Box, Tests |
| It is put back at its spot | Verdict that it is back, "Not taken." | Box |
| It is picked up and set down in view | Logged quietly, no alert | Replay, Tests |
| Something else is done to it ("turn the lamp off") | An event in the model's words: "At 10:22 pm, the lamp was turned off." | Laptop + SageMaker |
| A weakly detected object (detector never starts a track) | Locked at its step-away box, followed with the vision model's help; still gets a heads-up | Replay |
| Someone hands it over and the model can't name the giver | Credited to the person the session saw holding it | Tests |
| You move your own object | No alerts for your own handling | Box, Replay |

## Notes and monitor orders
| You do or say | Meanwhile does or answers | Verified |
|---|---|---|
| "Keep an eye on the night lamp and tell me when it is turned on or off" | A monitor order; alerts "The night lamp was turned on…" and "The night lamp turned off.", each written about 8 s after it happened | Box (typed through the API) |
| The same, with your face in the clip | "You picked up the game controller." instead of "someone" | Laptop + SageMaker |
| "Let me know who drops by" / "take notes of who comes into the room" | A notes order: clip events become notes, without interrupting you | Tests (order wording on the local model) |
| A question instead of an instruction ("did I lock the door?") | Starts nothing and says it sounds like a question | Tests |
| An unclear instruction ("keep an eye on that") | Asks you to say it again, with an example | Tests |
| A camera that doesn't exist ("…on the garage camera") | Says it has no such camera and offers the ones it has | Tests |

## Things' states (doors, lights, windows)
A camera's label assigns it to a place: label one camera **Door** and it is the door camera. From then on every door
ask ("watch the front door…", "is the front door locked?", "what happened at the door?") refers to that camera. The
same works for any label: a camera labelled **Garage** answers "is the garage open?".

| You do or say | Meanwhile does or answers | Verified |
|---|---|---|
| "Watch the front door and tell me when it's locked or unlocked" (with a camera labelled Door) | An ordinary monitor order on the Door camera; its clip question comes from your words ("…locked or unlocked…") | Tests (order made by the local model) |
| "Keep watch over the house while I'm gone" | A notes order on every camera | Tests (local model) |
| Someone locks the door during that watch | The event is noted, and the door's state ("locked", by whom, when) is recorded | Tests |
| "Is the front door locked?" | "Yes, the front door is locked. The last change I saw was at 3:02 pm, by Ravi." from the recorded state | Tests |
| The same with no stored status, one older than `MEANWHILE_STATE_MAX_AGE_S`, or cameras that disagree | Checks the video (the clip of the last change, else the last 5 minutes, else the camera now) and, when sure, stores what it saw, so asking again answers at once: "The front door is open. I checked the camera at 3:05 pm." | Tests |

### Try it: the door lock
A specific setup for testers: one camera is assigned as the door camera by its label. Meanwhile has no door-only
code; the order, what is noted and the door's state come from what you say, on the camera you labelled Door.
1. **Make one camera the door camera:** label it **Door** (name it so on the camera page, or rename an existing camera
   in the app). Point it at one door whose lock (thumb-turn, bolt or handle) is visible, with no other door in view.
   From now on every door ask refers to this camera. Keep a second camera (Desk) running somewhere else, and register
   your face (owner) in the app.
2. Say: **"Alexa, I'm stepping away. Watch the front door and tell me when it's locked or unlocked."**
   Expect: "Sure. I'm watching the front door…" (the Door camera; the panel's card shows what was understood).
3. In front of the Door camera: lock the door, wait about 10 seconds, unlock it, then leave it open.
4. Say **"Is the front door locked?"** Expect the last change it recorded, e.g. "The front door is unlocked. The last
   change I saw was at …, by you." (it may say "open" if it recorded that last; no name when your face wasn't
   visible).
5. Say **"What did I miss?"** Expect the lock and unlock in order, with times, and the clips on the screen; **"show me"**
   plays them.
6. Say **"I'm back."**, then with no watch running ask **"is the front door open?"** Expect an answer from the last
   recorded state while it is fresh; after `MEANWHILE_STATE_MAX_AGE_S` (or with nothing recorded) it checks the video
   ("…the front door is open"), and asking again right away answers from that check ("I checked the camera at …").

Verified: steps 2, 4 and 5 by automated tests (the order on the local model, the state answer and the catch-up with
a scripted video model); routing of every sentence above on the local model. Not yet run end to end on a real door.

## People and faces
| You do or say | Meanwhile does or answers | Verified |
|---|---|---|
| Register a person with a name, role and 1 to 3 photos | Photos indexed in your Rekognition collection; the owner is spoken as "you" | Laptop (Rekognition), Tests |
| A registered person is in view | Named by face ("you" for the owner) in events and answers | Box (look-back), Laptop + SageMaker |
| Someone nobody registered | "An unnamed person (Person 1)", the same person next time | Laptop (Rekognition), Tests |
| Name an unnamed person, or merge two that are the same | Faces, photos and past events move ("Ravi picked up…", or "You picked up…") | Laptop (Rekognition), Tests |
| Face ID off | Nobody is named; everyone is "someone I don't recognise" | Tests |

## Asking: look and look-back
| You do or say | Meanwhile does or answers | Verified |
|---|---|---|
| "Do you see me?" | "I can see you on the Desk camera." (or "…can't tell if it's you" when no face is visible) | Laptop + SageMaker |
| Any question about a camera now ("is the lamp on?") | A short answer from each camera's newest frame; the panel shows that frame | Laptop + SageMaker, Tests |
| "What did I do?" / "what just happened?" | An answer from the last 5 minutes on every camera: "In the last 5 minutes, you picked up the game controller…" | Box |
| A question about 10 minutes ago | "I can only look back over the last 5 minutes" | Tests |
| "What am I doing?" | Describes actions, never clothing or body; "what am I wearing?" still answers | Laptop + SageMaker |

## Catch-up and show me
| You do or say | Meanwhile does or answers | Verified |
|---|---|---|
| "What did I miss?" | One outcome sentence and an offer of details | Box |
| "Yes" (details) | The session's events in order | Box |
| "Show me" / "show me the hand-over" | Plays the matching clips on the home screen | Tests |
| "I'm back" (panel or app) | Ends the session | Box |
| "What did I miss?" with no session | Answers like "what did I do?" from the last few minutes | Tests |
| "What did I miss?" again, after being told | Nothing from the session again: the last few minutes, else "Nothing new since I last told you." (an old session is never read out again) | Tests |
| "What did I miss?" during a watch, asked twice | The second time, only what happened since the first | Tests |

## The Android app
| You do or say | Meanwhile does or answers | Verified |
|---|---|---|
| Open the app the first time | A five-card tutorial with example instructions; reopen it from Settings | Phone |
| Open a camera | Live video over WebRTC | Box (phone) |
| A heads-up, verdict or alert | A push notification (opening the event from it: Tests) | Box (phone) |
| Ask from the app ("What did I miss?") | The agent's reply, spoken aloud (mute toggle) | Box (phone) |
| Home, Timeline, People, Settings | Load from the box; Settings lists paired devices and can revoke them | Box (phone) |
| A host-name hub | Talks to `https://<host>` only, media links used as given | Tests |

## Access and security
| You do or say | Meanwhile does or answers | Verified |
|---|---|---|
| Call `/api`, `/mcp` or the panel without a key or token | 401 | Box |
| Open a media file without its signed link, or with a tampered one | 401; the signed link from the API loads | Box |
| Revoke a paired device | Its token stops working at once | Tests |
| Try many different wrong keys from one address | 429 with `Retry-After` until the window passes | Tests |
| Start the core without `MEANWHILE_KEY` | It refuses to start | Tests |

## Not yet / known limits
- **Several identical things in one view** (two doors on one camera): things can't be named apart yet, so "is the door
  closed?" describes each by where it is ("the door on the left is open, the one in front is closed") and stores no
  status; naming each one (a label per door) is planned.
- **Several door cameras:** only one camera can be the door camera (labelled Door); two cameras on two doors need
  their own labels ("Front door", "Back door") and asks that name them.
- **Asking about a specific session** ("what happened last night?") is planned: "what did I miss?" only tells what you
  haven't been told yet, so an earlier session can't be read out again by voice for now (the app's history shows it).
- **Real Alexa+:** not connected. Every voice check used the simulated Alexa+ panel (typed or spoken in Chrome) or
  MCP clients calling `/mcp`; the MCP server accepts Alexa+'s protocol version but no Alexa+ device or console called it.
- **States come from watches and checks:** a change is recorded while a watch is running and its clips show it; without
  one, the first "is the door locked?" checks the video and stores what it is sure of.
- **Notes and door watches** were checked by tests and the scenario dataset only, not live.
- **Face ID needs a visible face:** desk cameras that show only hands can't name anyone; the name then comes from
  another camera or stays unknown.
- **A detector that only sees an object weakly** (small or low-quality frames) gives some false "still tracking" alerts in
  replays.
- **The SageMaker endpoint and the project's box are not kept running;** see the README to run your own.
