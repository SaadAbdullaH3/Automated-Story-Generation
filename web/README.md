# web — the creator interface

Next.js 16, App Router, built as a **static export** that FastAPI serves itself.
One container, one origin, no CORS, no Node in production.

```bash
npm ci
npm run build        # -> web/out, which `python main.py serve` picks up at /
npm run dev          # :3000 with hot reload, proxying the API to :8000
```

`python main.py serve` serves `web/out` at `/` whenever it has been built, and
the original page stays at `/classic/` either way.

## The idea

The screen is the storyboard. A plan streams in over the WebSocket: the script
arrives first — every scene, its tone, its camera move and its lines with the
voice that will speak them — and then each frame develops in as it is drawn.
The wait becomes reading your own film, and none of it is decoration: every
word on screen is state the pipeline already has.

The primary action is **Render this film**, under a storyboard you have already
read, with the image cost stated next to it from the real cast and scene count.

Three moments, one screen: **write → storyboard → watch**. The player turns the
scenes into chapters you can jump between.

## Where things are

| Path | What |
|---|---|
| `lib/api.ts` | Every call to the server. Typed; same-origin cookies. |
| `lib/useProgress.ts` | Follows a project's current job. The server replays the whole job on connect, so a reload or a dropped socket loses nothing; events are de-duplicated by row id. |
| `lib/copy.ts` | Pipeline phases in a creator's words — "Recording the voices", not `phase2_audio`. |
| `lib/fonts.ts` | The one place the title face is chosen. `docs/mockups/fonts.html` shows all eight candidates set in place. |
| `components/studio/Studio.tsx` | Decides which moment you're in purely from server state. |

## Decisions

- **No Tailwind, no component library.** Three CSS modules and a token file are
  the whole design system; fewer dependencies, smaller attack surface, faster CI.
- **Fonts are self-hosted at build time** through `next/font`, so a page load
  never calls Google.
- **TypeScript is pinned to 5.9.** 6.x/7.x are the native-compiler transition,
  and Next 16's type-check path is proven on 5.9.
