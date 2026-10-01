# Benchmark — 2026-10-01T06:02:55

`python scripts/benchmark.py --duration 24 --scenes 3 --prompts 1 --subtitle-lang Urdu --out docs/BENCHMARK.md`

- providers: image **cloudflare (@cf/black-forest-labs/flux-1-schnell)**, llm **gemini (gemini-flash-latest)**, tts **edge**
- 1 prompt(s), target 24s, 3 scenes

| prompt | total | story | audio | video | film | vs target | in sync | lines on a cut | fallback images |
|---|---|---|---|---|---|---|---|---|---|
| A young astronaut discovers a hidden o | 87.8s | 26.6s | 8.6s | 52.4s | 25.4s | 5.8% | yes | 4/4 | 1 |

**Totals** — 1 runs, 87.8s, all in sync: yes, every line on a cut: yes, mean length error 5.8%, fallback images 1.
