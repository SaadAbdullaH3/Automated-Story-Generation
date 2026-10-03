# web — the Dastango interface

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

Watching is not the end. Under the film, **Change something** takes one
sentence — "make the voices in scene 2 whispered" — and first says what it took
that to mean ("whispered voices · scene 2"), so a misread shows before a minute
of rendering the wrong thing. The film keeps playing while the change is made.
Every cut is listed under **Versions** in the creator's own words, and going
back to one is itself a new version, so trying it costs nothing.

## Where things are

| Path | What |
|---|---|
| `lib/api.ts` | Every call to the server. Typed; same-origin cookies. |
| `lib/useProgress.ts` | Follows a project's current job. The server replays the whole job on connect, so a reload or a dropped socket loses nothing; events are de-duplicated by row id. |
| `lib/copy.ts` | Pipeline phases in a creator's words — "Recording the voices", not `phase2_audio`. |
| `lib/fonts.ts` | The one place the title face is chosen. `docs/mockups/fonts.html` shows all eight candidates set in place. |
| `components/studio/Studio.tsx` | Decides which moment you're in purely from server state. |
| `components/studio/EditPanel.tsx`, `Versions.tsx` | Editing a finished film in a sentence, and going back. Edits are jobs, followed over the same socket as a render. |
| `app/docs/`, `components/docs/`, `lib/docs.ts` | The public user guide at `/docs`: one registry drives the sidebar, titles, filter and previous/next links; page bodies are in `components/docs/pages/`. |
| `components/Logo.tsx`, `app/icon.svg`, `app/opengraph-image.png` | The mark (a Mughal arch with the storyteller's lamp), the favicons and the link-preview card. Source files for the brand are in `docs/brand/`. |

## Decisions

- **No Tailwind, no component library.** Three CSS modules and a token file are
  the whole design system; fewer dependencies, smaller attack surface, faster CI.
- **Fonts are self-hosted at build time** through `next/font`, so a page load
  never calls Google.
- **TypeScript is pinned to 5.9.** 6.x/7.x are the native-compiler transition,
  and Next 16's type-check path is proven on 5.9.
