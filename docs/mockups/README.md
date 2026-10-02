# Frontend direction — three mockups

> **Decided:** `creator.html`, built in `web/` with Fraunces as the title face.
> These stay as the record of what was considered and why.

Decision aids, not code. The frontend is being rebuilt (Next.js, static export,
served by FastAPI) and these are the three directions it could take. Every
value in them is real: "Hours of the Flood", its logline, the three scene
titles with the tones the model actually chose and the camera moves those
tones produced, the real cast and the Kokoro voice each was given.

View them:

```bash
python -m http.server 8021 --directory docs/mockups
# then open http://127.0.0.1:8021/creator.html
```

| File | Direction | Built from |
|---|---|---|
| `creator.html` | **Storyboard as the interface.** The filmstrip fills in while the film is made; the dialogue is readable before the pictures arrive. Primary action is "Render this film", with the image cost stated next to it. Hides job ids, frame counts and the provider chain. | the product argument below |
| `editorial.html` | Minimal editorial dashboard. Warm bone canvas, serif display, hairline borders, pastel status tags. The frame is the only saturated thing on screen. | `minimalist-ui` skill |
| `terminal.html` | Industrial terminal. Archivo Black at 7rem, JetBrains Mono, one hazard red, zero border-radius, CRT scanlines. Treats the render as machinery. | `industrial-brutalist-ui` skill |

## Why `creator.html` exists

The other two are engineer dashboards — one calm, one loud. Both put the
machine in the middle: phases, providers, job ids, frame counts. A content
creator does not care that sync is frame-exact.

The pipeline already produces something better to look at during a render: the
**script exists before the images do**. Scene titles, tones, the spoken lines
and who says them are known seconds in; the pictures then arrive one at a time.
So the wait becomes reading your own film, and none of it is theatre — every
word on screen is state the pipeline already has.

The product bet underneath: the differentiator is not "AI makes video", it is
**see it before you pay for it**. Planning costs 3 images, rendering adds 7.
That is invisible in the other two designs and is the first thing `creator.html`
says.

## What it deliberately drops

Job ids, frame counts, worker numbers, the provider chain, "sync: exact" —
all of it. Those are how we know it works, not why anyone would use it. They
belong in a details panel for when a render goes wrong, and in the README,
where an engineer reads deliberately.
