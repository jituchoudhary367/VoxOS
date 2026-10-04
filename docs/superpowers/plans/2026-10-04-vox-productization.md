# Vox productization plan — from repo to a product people download

**Status:** planned, not started. Start **after the feature-complete build** is signed off.
**Owner:** Abhijeet · **Written:** 2026-10-04 · **Branch at time of writing:** `abhijit-v4` (`dc297e0`)

## Goal

A person who has never seen the repo can **download one installer, double-click, and be talking to
Vox within two minutes** — no Git, no Python, no `.env`, no API keys, no terminal. Then they can pay
for more, and we can update and support them.

**Definition of done (whole plan):** on a fresh Windows 10 and Windows 11 machine (VM), a stranger
installs from the website, signs in, completes setup, runs the five demo commands below, gets an
update pushed to them, and buys Pro — without help.

Demo commands used as the acceptance check in every phase:
1. "What's my battery level?"
2. "Open Notepad and type hello"
3. "Search Amazon for a gaming laptop under 60,000"
4. "What's on my screen?" (button)
5. A voice request via Ctrl+Space, answered aloud

---

## Decision to confirm before Phase 2: who pays for the AI

| Option | How it works | Verdict |
|---|---|---|
| A. Bring your own keys | User creates Groq/Deepgram keys and pastes them | Developers only; most buyers drop off |
| **B. Vox cloud gateway** | App calls our server; server holds Groq/Deepgram keys, enforces per-user quota | **Recommended** — no keys for users, we control cost, abuse, billing |
| **C. Local mode (Ollama)** | Everything on the PC, free and private | **Recommended as the free/privacy tier** alongside B |

Proposed plans (to validate on pricing calls): **Free** (≈30 cloud requests/day + unlimited Local),
**Pro ₹299/month** (high daily cap, faster models, priority), **Lifetime/Team** later.
Developer mode keeps option A available behind Settings → Advanced.

---

## Known blockers found in the current code (fix in Phase 1)

| # | Blocker | Where | Effect on an installed copy |
|---|---|---|---|
| B1 | Backend started with `[sys.executable, "-m", "uvicorn", ...]` | `voxos/ui/app.py` `start_backend` | In a frozen exe `sys.executable` is `VoxOS.exe` → backend never starts (or relaunches the app) |
| B2 | `build.py` **overwrites `launcher.py`** with the old `uuid4()` launcher and regenerates the spec | `build.py` | Every release build brings back "forgets my chats" and drops the icon / spec fixes |
| B3 | Config read from `.env` next to the working directory | `voxos/config.py` `_env_file_paths` | Program Files is read-only; users have no `.env`; keys in plain text |
| B4 | Browser needs `playwright install chromium` (a terminal step) | `voxos/tools/browser/tools.py` | Every browser task fails on a clean PC |
| B5 | Postgres (Neon URL) and Redis assumed | `database/engine.py`, `events/bus.py` | Timeouts / errors on a clean PC; our Neon URL must never ship |
| B6 | Wake-word models, Silero VAD, ONNX runtime collected ad hoc | `VoxOS.spec` | Missing-file crashes only seen on clean machines |
| B7 | No first-run experience | — | Mic permission, voice, hotkey never explained |
| B8 | Logs go to the console only | `observability/logger.py` | No way to get a log from a user's machine |

---

## Phase 1 — An installer that works on a clean PC

**Outcome:** `Vox-Setup-<version>.exe` that installs per-user and passes the 5 demo commands on a
fresh VM, still using our own keys via a dev-only setting (gateway comes in Phase 2).
**Estimate:** ~1–1.5 weeks.

### 1.1 Run the backend in-process (B1)
- Start uvicorn/FastAPI as an asyncio task inside the Qt (qasync) loop, or drop the HTTP backend from
  the desktop app entirely if nothing in the app calls it (check `voxos/api` consumers first).
- Port 8000 clash: pick a free port at start, or bind only when the API is needed.
- **Done when:** `VoxOS.exe` from `dist/` starts with no Python on PATH and `/health` answers.

### 1.2 One reproducible build (B2, B6)
- Delete the file-writing parts of `build.py`; `VoxOS.spec` and `launcher.py` are source files.
- `python -m voxos.build` (or `make release`): clean venv → `python -m voxos.ui.brand` (assets) →
  PyInstaller (`--onedir`) → Inno Setup → `dist/Vox-Setup-<version>.exe`.
- Spec collects: `voxos/ui/assets`, openwakeword models, Silero VAD onnx, `onnxruntime` DLLs,
  PySide6 plugins (qoffscreen not needed, svg/imageformats needed), certifi bundle.
- Version comes from one place (`pyproject.toml`) into the exe metadata, installer and About.
- **Done when:** a clean checkout builds the installer with one command, twice, identically.

### 1.3 Settings and secrets in the user profile (B3)
- Config load order: built-in defaults → `%APPDATA%\VoxOS\settings.json` → environment variables
  (dev) → `.env` only when running from source.
- Secrets (keys, Postgres URL, vault) in Windows Credential Manager via `keyring` (already used by
  the vault); Settings page writes there instead of `.env`.
- Migration: on first run from source, copy existing `.env` values into the new store once.
- **Done when:** installed app runs with no `.env` anywhere; Settings edits survive restart.

### 1.4 Browser that just works (B4)
- Option 1 (preferred): use the user's installed **Microsoft Edge** via Playwright's `channel="msedge"`
  — present on every Windows 10/11 PC, zero download.
- Fallback: download Chromium in the background on first browser use, with a progress toast.
- **Done when:** demo command 3 works on a fresh VM with no manual step.

### 1.5 Local-only storage by default (B5)
- Sessions already local (`core/sessions.py`). Make task records, memories and audit log local too
  (SQLite in `%APPDATA%\VoxOS\vox.db` via SQLAlchemy — same models), Postgres optional.
- Event bus: in-process only unless `REDIS_URL` is set (already mostly true; remove startup waits).
- **Never ship our Neon URL or keys** — add a release check that greps the build for `gsk_`, `postgres://`.
- **Done when:** a fresh VM with no network database runs all demo commands; no DB timeouts in logs.

### 1.6 First-run setup
Screens (one window, our design system, ~1 minute total):
1. Welcome + what Vox can do (10-second animation of the mark)
2. Sign in (Phase 2; in Phase 1: "Continue")
3. Microphone check (live level meter) and Windows mic permission help if blocked
4. Try the hotkey: "Press Ctrl+Space and say *what time is it*"
5. Choose AI mode: Cloud (recommended) / Local (offers Ollama install + model download, already built)
6. Optional: start with Windows, wake word on/off
- **Done when:** a first-time tester gets through it without asking anything.

### 1.7 Logs and diagnostics (B8)
- Rotating file logs in `%APPDATA%\VoxOS\logs` (secrets already redacted); Settings → "Export
  diagnostics" zips logs + versions (no transcripts unless the user ticks it).

### 1.8 Clean-machine test
- Windows 10 and 11 VMs (Hyper-V / VirtualBox), standard user account, no Python.
- Script the 5 demo commands; record time-to-first-answer and install size.
- Targets: installer ≤ 350 MB (trim torch-free deps, exclude unused Qt modules), cold start ≤ 5 s
  to window, ≤ 20 s to wake-word ready.

---

## Phase 2 — Make it sellable

**Outcome:** accounts, a free and a paid plan, payments, updates and a trusted installer.
**Estimate:** ~2–3 weeks.

### 2.1 Vox cloud gateway (option B)
- Small service (FastAPI on Railway/Render/Fly, or Vercel functions) — OpenAI-compatible
  `/v1/chat/completions` and a Deepgram token/stream proxy, so the app's existing
  `OpenAICompatibleProvider` just points at it.
- Holds our Groq key pool server-side (reuse `key_pool.py` + `rate_limiter.py` there).
- Per-user quota (requests/day, tokens/day), per-plan model access, abuse limits, request logs
  without content by default.
- App: Cloud mode = gateway; Developer mode = own keys (today's behaviour).

### 2.2 Accounts
- Sign in with Google and email OTP (Clerk/Supabase Auth/Firebase — pick one; Clerk skills exist).
- Token stored in Credential Manager; "Signed in as …" chip already in the sidebar becomes real.
- Profile/sessions stay **on the PC** (privacy promise); optional encrypted cloud backup later.

### 2.3 Plans and payments
- Razorpay subscriptions (India: UPI, cards) — Stripe later for international.
- Webhook → gateway updates the user's plan; app reads plan from the gateway, shows usage in the
  Usage tab ("12 of 30 free requests today"), upgrade button opens checkout in the browser.
- GST invoices via Razorpay.

### 2.4 Auto-update
- Version manifest on our server/GitHub Releases; app checks on start and daily.
- Download the new installer in the background, verify signature, offer "Restart to update".
- Staged rollout (10% → 100%) and a kill-switch to stop a bad release.

### 2.5 Code signing
- Buy an OV/EV code-signing certificate (or Azure Trusted Signing) in the company name
  (Soulplex Studios); sign `VoxOS.exe`, DLLs we build, and the installer in the release script.
- **Done when:** SmartScreen shows the publisher name, no "unknown publisher" warning.

### 2.6 Crash and usage reporting (opt-in)
- Sentry (or similar) for crashes with secrets/transcripts stripped; anonymous feature counts to
  learn what people use. Clear consent screen in first-run and Settings → Privacy.

### 2.7 Legal
- Privacy policy (what leaves the PC: requests to the gateway; what doesn't: profile, vault,
  transcripts), Terms of Service, refund policy, EULA in the installer.

---

## Phase 3 — Distribution and launch

**Estimate:** ~1–2 weeks, overlaps Phase 2.

1. **Landing page** — hero with the mark and a 30-second demo video, Hinglish voice demo, features,
   pricing, download button (detects Windows), FAQ, privacy promise.
2. **Download channels** — website (signed installer), **Microsoft Store** (MSIX package; Store
   handles updates and trust), `winget install Soulplex.Vox`.
3. **Positioning** — "Your Windows assistant that understands Hinglish": voice-first, does things on
   your PC (apps, browser, shopping, files), private Local mode. India-first pricing in ₹.
4. **Beta** — 20–50 users from the network: installer link, feedback form in-app (Settings → Send
   feedback), weekly builds via auto-update; fix top crashes before public launch.
5. **Launch** — Product Hunt, LinkedIn (company + personal posts), YouTube demo, college/dev
   communities; track installs → first command → day-7 retention → upgrade.

---

## Order of work and checkpoints

| Step | Checkpoint | Proves |
|---|---|---|
| 1.1–1.2 | `dist/VoxOS/VoxOS.exe` runs without Python | Packaging works |
| 1.3–1.5 | Fresh VM runs demo 1–5 with no manual steps | Clean-machine readiness |
| 1.6–1.8 | First-time tester installs + uses alone | Phase 1 done → private beta possible |
| 2.1–2.2 | Signed-in user works with zero keys | Gateway + accounts |
| 2.3 | Test payment upgrades plan, quota rises | Sellable |
| 2.4–2.5 | Signed update reaches a beta machine | Shippable at scale |
| 3 | Public download page live | Launch |

## Risks to watch

- **Cloud cost per user** — gateway quotas and the token reductions already made (product cards,
  no re-reads, pacing) must hold; measure tokens/request in beta before setting prices.
- **Groq free-tier terms** — reselling access needs a paid Groq plan for the gateway.
- **Antivirus false positives** — global keyboard hook + PyInstaller exes get flagged; signing and
  submitting to Microsoft Defender for analysis before launch.
- **Install size** — PyInstaller + Qt + ONNX + Pipecat is heavy; measure early in 1.8.
- **Mic/voice variance** — test cheap laptop mics and Hinglish speakers in beta, not only dev setup.
