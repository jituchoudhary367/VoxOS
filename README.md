# VoxOS

VoxOS is a voice-first, multilingual, permissioned desktop AI agent for Windows.
You talk to it; it plans a sequence of registered tools, checks each action
through a risk engine, runs it, and answers out loud.

The product is **VoxOS**. The agent you talk to is **Vox**.

---

## Contents

- [How a request flows](#how-a-request-flows)
- [The voice pipeline](#the-voice-pipeline)
- [Waking Vox](#waking-vox)
- [Keeping noise out](#keeping-noise-out)
- [Providers](#providers)
- [Quick start](#quick-start)
- [Configuration](#configuration)
- [Tests and checks](#tests-and-checks)
- [Project layout](#project-layout)
- [Known limitations](#known-limitations)

---

## How a request flows

Four agent stages turn one utterance into executed work:

| Stage | Responsibility |
|---|---|
| **Listener** | Normalizes the request, detects language, extracts intent and entities |
| **Strategist** | Decomposes it into a plan of registered tool calls |
| **Executor** | Runs each step against the tool registry, enforcing permissions |
| **Overseer** | Verifies the outcome and decides whether to retry |

High-risk actions require approval. Critical shell and filesystem patterns are
blocked outright. Unknown tools are denied by default. Production REST and
WebSocket routes require a Bearer JWT; development mode keeps the local
dashboard convenient while still enforcing tool permissions.

PostgreSQL stores task, conversation and audit data. Redis distributes events
across processes when available — the API still starts without it, with
cross-process events disabled.

---

## The voice pipeline

Built on [Pipecat](https://github.com/pipecat-ai/pipecat) 1.9. Every stage is an
explicit processor, and the order matters:

```
microphone
  │
  ├─ MicGate ............. drops audio while Vox speaks, or while
  │                        push-to-talk is not held
  ├─ VADProcessor ........ Silero voice activity detection
  ├─ Deepgram STT ........ nova-3, streaming, `multi` language
  ├─ UserTurnProcessor ... decides when your turn ended
  ├─ VoxBridge ........... assembles the turn, vets it, runs the agents
  ├─ Deepgram TTS ........ Aura, streamed as it generates
  └─ speaker
```

**Why VAD is its own processor.** Pipecat 1.x moved voice activity detection out
of the transport. Passing `vad_analyzer=` to `LocalAudioTransportParams` — which
1.0 accepted — is now silently dropped by pydantic. A pipeline built that way
runs with no VAD, never emits `UserStoppedSpeakingFrame`, and therefore never
finalizes an utterance into a task: it hears you and does nothing.
`tests/unit/test_voice_pipeline_build.py` guards against that regression.

**Why TTS is in the pipeline.** Streaming the reply through
`DeepgramTTSService` means audio starts playing within a few hundred
milliseconds instead of after the whole reply is synthesized. It also means the
output transport broadcasts `BotStartedSpeakingFrame` / `BotStoppedSpeakingFrame`
in both directions, so the microphone gate closes and reopens on its own.

Aura speaks English only. `choose_tts_route()` sends Hindi and Hinglish replies
to edge-tts instead, which covers the Indian-language voices and is played out
of band with the gate frames raised by hand.

---

## Waking Vox

Two ways in, both ending at the same listening session.

**1. Wake word** — local, always-on, no network. Runs
[openwakeword](https://github.com/dscripka/openWakeWord) on a background thread
and releases the microphone the moment a session starts.

**2. Hotkey** — `Ctrl+Space` by default:

| Gesture | Behaviour |
|---|---|
| **Tap** | Toggles a hands-free session. Press again to end it. |
| **Hold** | Push-to-talk. The mic opens while the key is down and closes on release. |

Push-to-talk is the most noise-immune mode: nothing is captured outside the
press. Releasing the key closes the microphone but keeps the session alive, so
the turn still finalizes through the normal speech timeout and your last words
are not cut off.

Both paths can run at once. Set `VOICE_WAKE_WORD_ENABLED=false` for hotkey only.

---

## Keeping noise out

A desktop microphone in a real room hears fans, traffic, a TV, and Vox's own
voice through the speakers. Speech-to-text will happily transcribe all of it,
and the agent cannot tell an invented command from a real one. Regex blocklists
over the transcript treat the symptom. VoxOS layers four defences instead:

1. **Silero VAD** gates the STT, so silence and room tone never reach it.
   `VOICE_VAD_CONFIDENCE` and `VOICE_VAD_MIN_VOLUME` are the two knobs that
   decide what counts as speech.
2. **The mic gate** mutes input while Vox is speaking, plus a hangover after
   playback ends — speakers ring and the OS audio buffer drains late. Without
   this, Vox transcribes its own reply and answers itself.
3. **The turn assembler** judges the complete utterance rather than each
   streaming fragment, tracking the weakest confidence across the turn.
4. **The speech quality gate** drops what is left: fillers and known
   hallucinations, transcripts below a confidence floor, non-speech markers
   like `[Music]`, and anything that closely matches what Vox just said.

Every rejection is logged with a stable reason (`filler`, `low_confidence`,
`self_echo`, `too_short`, `non_speech`), so the noise floor can be tuned from
real sessions rather than guesses.

---

## Providers

| Role | Default | Why |
|---|---|---|
| **STT** | Deepgram `nova-3`, `multi` | Streaming, and handles Hindi↔English code-switching mid-sentence |
| **TTS (English)** | Deepgram Aura 2 | Streams into the pipeline; same API key as STT |
| **TTS (Hindi et al.)** | edge-tts | Free, covers Indian-language voices Aura cannot speak |
| **Wake word** | openwakeword | Local, offline, no per-minute cost |
| **VAD** | Silero (via Pipecat) | Local, fast, tunable |
| **LLM** | Groq | Configurable via `LLM_PROVIDER` / `LLM_BASE_URL` |

Batch Whisper — from any host — is a poor fit for this pipeline. It cannot start
until the clip is finished, and it is trained on subtitles, so it invents
plausible text like `Thank you.` or `[Music]` when handed silence or noise.

`DEEPGRAM_KEYTERMS` biases nova-3 towards terms it would otherwise mangle:
the assistant name, app names, Indian proper nouns.

---

## Quick start

```bash
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# Windows:     .venv\Scripts\Activate.ps1

python -m pip install -e ".[dev]"
cp .env.example .env          # PowerShell: Copy-Item .env.example .env
```

Fill in `DEEPGRAM_API_KEY`, `GROQ_API_KEY`, and a `POSTGRES_URL`, then run the
migration once:

```bash
python -m alembic upgrade head
```

Start the API:

```bash
python -m voxos
```

- API: `http://localhost:8000`
- OpenAPI docs (development): `http://localhost:8000/docs`
- Health: `http://localhost:8000/health`

Start the desktop UI, which launches the backend itself:

```bash
python launcher.py
```

For a no-Docker setup, use a managed PostgreSQL provider such as Neon and a
managed Redis provider such as Upstash. Set `POSTGRES_URL` to the provider's
PostgreSQL connection string and `REDIS_URL` to its native Redis TCP/TLS
connection string — the Upstash REST URL/token pair is for an HTTP Redis client
and is not used by the Pub/Sub event bus. Docker Compose remains available as an
optional local alternative.

`make help` lists every convenience target if GNU Make is available.

---

## Configuration

`.env.example` documents every supported setting. Keep `.env` out of version
control. The settings most worth tuning:

### Voice quality and latency

| Setting | Default | Raise it when | Lower it when |
|---|---|---|---|
| `VOICE_VAD_CONFIDENCE` | `0.8` | Room noise triggers listening | Vox misses quiet speech |
| `VOICE_VAD_MIN_VOLUME` | `0.65` | Distant sounds get picked up | You speak softly or sit far away |
| `VOICE_USER_SPEECH_TIMEOUT` | `0.6` | Vox interrupts you mid-sentence | Replies feel sluggish |
| `VOICE_ECHO_HANGOVER_SECS` | `0.4` | Vox still hears itself | Its reply delays your next turn |
| `VOICE_MIN_TRANSCRIPT_CONFIDENCE` | `0.35` | Garbage commands get through | Real commands get rejected |

Rejected turns are logged as `voice_turn_rejected` with the reason — read those
before changing a threshold.

### Wake-up

| Setting | Default |
|---|---|
| `VOICE_HOTKEY` | `ctrl+space` |
| `VOICE_HOTKEY_HOLD_SECS` | `0.35` — below this a press is a tap, at or above it is a hold |
| `VOICE_WAKE_WORD_ENABLED` | `true` |
| `VOICE_WAKE_WORD_MODELS` | `hey_jarvis` — see [Known limitations](#known-limitations) |
| `VOICE_WAKE_WORD_SENSITIVITY` | `0.6` |

### Environment names

Application settings use `VOX_*`: `VOX_ENV`, `VOX_DEBUG`, `VOX_HOST`, `VOX_PORT`,
`VOX_SECRET_KEY`, `VOX_WORKSPACE_ROOT`. The older `JARVIS_*` spelling still
resolves, so existing `.env` files keep working; `VOX_*` wins when both are set.

Before production, set strong `VOX_SECRET_KEY` and `JWT_SECRET_KEY` values,
provide an LLM key, configure PostgreSQL and Redis, and set `VOX_ENV=production`.

---

## Tests and checks

```bash
pytest -q                    # full suite
pytest tests/unit -q         # unit tests only
ruff check voxos tests
mypy voxos
```

The voice pipeline is covered without audio hardware: the gate, turn assembly,
noise filtering, hotkey gestures, TTS routing, wake-word selection and the
processor chain itself are all pure seams with their own tests. What tests
cannot cover — real microphone capture, real synthesized audio, real global key
events — is called out below.

---

## Project layout

```
voxos/
  agents/      listener, strategist, executor, overseer
  api/         FastAPI routes, auth dependencies, WebSocket events
  core/        task orchestration, conversation, permissions, risk, events
  database/    SQLAlchemy models and repositories
  llm/         provider routing and context assembly
  tools/       filesystem, shell, browser, desktop, documents, memory, code
  ui/          PySide6 tray app, dashboard, global hotkey
  voice/       pipeline, bridge, mic gate, turn assembly, filters, STT/TTS/VAD/wake word
  vision/      screenshots and screen understanding
apps/frontend/ static dashboard, served separately
alembic/       database migrations
tests/         unit, tools, security, integration
```

---

## Known limitations

- **The wake phrase is not yet "hey vox."** openwakeword ships six pretrained
  models — `alexa`, `hey_mycroft`, `hey_rhasspy`, `hey_jarvis`, `timer`,
  `weather` — and none of them is Vox. `hey_jarvis` is the placeholder acoustic
  trigger; everything else in the product calls the agent Vox. Training a custom
  model with openwakeword's notebook produces an `.onnx` file, and
  `VOICE_WAKE_WORD_MODELS` accepts a file path, so adopting it needs no code
  change. A warning is logged whenever the placeholder is in use.
- **`timer` and `weather` must never be loaded.** They are intent classifiers,
  not wake words, and score high on ordinary conversation. The engine derives
  its trigger allowlist from the models it actually loaded to prevent this.
- **Deepgram Aura is English-only.** Hindi and Hinglish take the slower
  edge-tts path, which synthesizes in full before playback starts.
- **Streaming STT is billed per minute.** A session left open listens — and
  bills — until it is toggled off. Push-to-talk avoids this.
- **Ruff reports pre-existing issues** in areas outside the voice package,
  carried over from the repository's original scaffold.
