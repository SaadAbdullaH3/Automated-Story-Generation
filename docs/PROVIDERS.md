# Models and providers

Which model does each job is configuration, not code. This page explains the
configuration file, the free keys worth adding, the voices, and how to turn on
the paid options when the budget allows.

## How it works

Agents ask for a **role**; `config/providers.yaml` lists the providers for
each role in order of preference, with the environment variables each needs.
A provider whose credentials are missing is skipped; one that fails at run
time hands over to the next; every chain ends in something that works offline.
`.env` holds only credentials.

```yaml
roles:
  image:
    - provider: cloudflare              # first choice
      model: "@cf/black-forest-labs/flux-1-schnell"
      params: {steps: 8}
      requires: [CLOUDFLARE_ACCOUNT_ID, CLOUDFLARE_API_TOKEN]
      concurrency: 4                    # calls it tolerates at once
      retries: 3                        # attempts before the next provider
    - provider: pollinations
      concurrency: 1
      retries: 3
    - provider: placeholder             # offline, always works
```

`requires: [A|B]` means either variable will do. See what this machine would
use, and why the rest are skipped:

```bash
python main.py providers           # the chain per role and what each needs
python main.py providers --check   # one real call per role, to verify keys
```

**Overrides.** `PROVIDERS_FILE=/path/to.yaml` swaps the whole file;
`PROVIDER_IMAGE=placeholder` (or `PROVIDER_STORY`, `PROVIDER_TTS`, …) forces
one provider for a role; `LLM_PROVIDER=mock` forces the offline script and
edit paths (the tests use this).

## The free setup

Everything below is free and needs no card. With none of it, the product
still runs: template scripts, keyword edits, placeholder images, and Kokoro or
online voices.

| Key in `.env` | Unlocks | Where |
|---|---|---|
| `GEMINI_API_KEY` | Gemini Flash writes scripts, reads edits, translates subtitles | [aistudio.google.com](https://aistudio.google.com/app/apikey) |
| `GROQ_API_KEY` | Fast fallback for the same roles (gpt-oss-120b / 20b) | [console.groq.com](https://console.groq.com/keys) |
| `OPENROUTER_API_KEY` | A third fallback (`openrouter/free`) | [openrouter.ai](https://openrouter.ai/keys) |
| `CLOUDFLARE_ACCOUNT_ID` + `CLOUDFLARE_API_TOKEN` | FLUX images, four at a time; 10,000 neurons a day ≈ 170 images | [dash.cloudflare.com](https://dash.cloudflare.com), a Workers AI token |
| `POLLINATIONS_API_KEY` | Pollinations' real models at full size instead of the keyless endpoint | [enter.pollinations.ai](https://enter.pollinations.ai) |
| `MYMEMORY_EMAIL` | Raises the free translation fallback from ~5,000 to ~50,000 characters a day | any address |
| `OLLAMA_HOST` | Local models through Ollama, fully offline | `ollama serve` |

What running it live taught, now handled in the adapters:

- Reasoning models (Gemini Flash, gpt-oss) spend small token budgets thinking
  and return nothing, so every call asks for at least 600 tokens.
- Cloudflare's FLUX rejects a `seed`; the provider drops it and retries once.
- A Pollinations key with no budget left answers 402; the keyless endpoint
  takes over for the rest of the run.
- The keyless Pollinations endpoint answers one request per IP, which is why
  each provider has its own concurrency limit.
- Cloudflare's safety filter occasionally refuses an innocent prompt; the next
  provider draws that image.

## Voices

| Engine | Cost | Notes |
|---|---|---|
| **Kokoro** (default) | free, offline | Apache-2.0, 82M parameters on onnxruntime; no GPU, no PyTorch. `pip install -r requirements-voices.txt` then `python scripts/get_kokoro.py` (≈340 MB, SHA-256 verified) and set `KOKORO_MODEL` / `KOKORO_VOICES`. The container does all of this itself. |
| Edge TTS | free, online | Microsoft neural voices, mapped to character archetypes |
| ElevenLabs | paid | `ELEVENLABS_API_KEY` |
| gTTS | free, online | Clear but flat |
| System voices (pyttsx3) | free, offline | Only where the OS has a speech engine (Windows, macOS, or Linux with `espeak-ng`) |
| Silent | free, offline | Silence of the right length, so timing stays correct (tests) |

The creator can choose the engine per film; the voice picker says which
engines work on this machine and why the others don't, and plays a cached
one-line sample before anything is rendered.

## Real motion and lip sync (paid, opt-in)

By default every shot is a still with a supersampled camera move: free,
offline and frame-exact. Animating stills well costs money per clip with every
provider, so these sit **behind** the free path:

| Provider | Role | Needs | Cost |
|---|---|---|---|
| fal.ai (Stable Video Diffusion, SadTalker) | `video`, `lipsync` | `FAL_KEY` | ~$1 trial credit, then per clip |
| Hugging Face | `video` | `HF_TOKEN` | free tier, noticeably lower quality |
| **Veo 3.1** (via the Gemini API) | `video` | `GEMINI_API_KEY` **and** `VIDEO_BUDGET_OK=1` | billed per second; not in the free tier |
| Replicate | `video`, `lipsync` | `REPLICATE_API_TOKEN` | per second |

Veo needs the second variable on purpose: a Gemini key held for the free text
models must never start a per-second video bill by itself. The real-motion
providers are unit-tested with mocked HTTP; none has been run end to end,
because each costs money per clip.

## Adding a provider

1. An adapter: a `_provider_<name>` method in
   `mcp/tools/vision_tools/image_gen_tool.py` for images, or a branch in
   `mcp/tools/llm_tools/llm_client.py` for a language model (anything
   OpenAI-compatible needs only a base URL and a key name).
2. An entry in `config/providers.yaml` under the role, with `requires`,
   `concurrency` and `retries`.
3. A test with the HTTP mocked, including what a failure looks like.

Never an `if os.getenv(...)` inside an agent. That is what this layer
replaced.
