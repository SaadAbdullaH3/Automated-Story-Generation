# Benchmark

Regenerate with `python scripts/benchmark.py` (add `--offline` to run without
network, `--out docs/BENCHMARK.md` to refresh this file).

## Run — 2026-09-22T22:48:18

`python scripts/benchmark.py --duration 24 --scenes 3 --prompts 1 --subtitle-lang Urdu --out docs/BENCHMARK.md`

- providers: image **pollinations (tongyi-mai/z-image-turbo)**, llm **mock**, tts **edge**
- 1 prompt(s), target 24s, 3 scenes

| prompt | total | story | audio | video | film | vs target | in sync | lines on a cut | fallback images |
|---|---|---|---|---|---|---|---|---|---|
| A young astronaut discovers a hidden o | 46.5s | 0.0s | 6.4s | 39.9s | 23.5s | 2.1% | yes | 3/3 | 0 |

**Totals** — 1 runs, 46.5s, all in sync: yes, every line on a cut: yes, mean length error 2.1%, fallback images 0.
