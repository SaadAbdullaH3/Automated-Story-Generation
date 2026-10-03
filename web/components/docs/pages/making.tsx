import Link from "next/link";

import styles from "../docs.module.css";
import { H2, Note, Say } from "../Docs";

export function Prompts() {
  return (
    <>
      <p>
        Your sentence is the whole brief. The storyteller turns it into a title, a cast with names and
        voices, a look for the film, and scenes with lines — so the more it has to hold on to, the more the
        film feels like yours.
      </p>

      <H2 id="what-helps">What helps</H2>
      <ul>
        <li>
          <strong>A person.</strong> Someone with a job or a want: a clockmaker, a lighthouse keeper, two
          rival cooks.
        </li>
        <li>
          <strong>A place.</strong> One vivid setting gives every picture something in common: a flooded
          city, a night train, a market at dawn.
        </li>
        <li>
          <strong>A turn.</strong> Something that changes — that is what scenes are made of. <Say>…realises
          the sea has started writing back.</Say>
        </li>
        <li>
          <strong>A look, if you have one in mind.</strong> The film chooses its own visual style from the
          story — a ghost story and a children&rsquo;s fable come out looking different — but you can steer it:{" "}
          <Say>…in the style of a hand-painted storybook.</Say>
        </li>
      </ul>

      <H2 id="examples">Examples</H2>
      <div className={styles.tableWrap}>
        <table>
          <thead>
            <tr>
              <th>Prompt</th>
              <th>Why it works</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>
                <Say>A clockmaker in a flooded city repairs the hours people lose</Say>
              </td>
              <td>A person, a place, and an impossible job to build scenes around.</td>
            </tr>
            <tr>
              <td>
                <Say>Two rival street-food cooks discover they share the same grandmother&rsquo;s recipe</Say>
              </td>
              <td>Two characters to cast and voice, and a reveal for the last scene.</td>
            </tr>
            <tr>
              <td>
                <Say>A night train that only stops at stations that no longer exist</Say>
              </td>
              <td>Each scene can be a new station — natural chapters.</td>
            </tr>
          </tbody>
        </table>
      </div>

      <H2 id="length">Length and scenes</H2>
      <p>
        Choose <strong>20, 30, 45 or 60 seconds</strong> and <strong>2 to 6 scenes</strong>. The length is
        a word budget: the script is written to fit it, about two words for every second of speech, and the
        finished film usually lands within a few percent of what you chose.
      </p>
      <p>
        More scenes means more pictures to draw and more shots to render. Three scenes is a good default;
        use more when the idea has distinct places or moments.
      </p>

      <Note>
        Write in English for the best scripts. The subtitles can then be in any of{" "}
        <Link href="/docs/subtitles/">fourteen languages</Link>, including Urdu, Hindi and Arabic.
      </Note>

      <H2 id="avoid">What to avoid</H2>
      <p>
        Real, named people, and anything violent or explicit. The pictures come from image models with their
        own safety filters; a picture one of them refuses is drawn by the next, and occasionally an innocent
        prompt is refused too — that scene&rsquo;s picture may then look plainer than the rest.
      </p>
    </>
  );
}

export function Storyboard() {
  return (
    <>
      <p>
        The storyboard is the film before it is rendered: the cheap half, ready in about twenty seconds, so
        you can read and fix the story before spending minutes on pictures and voices.
      </p>

      <H2 id="reading">Reading a scene</H2>
      <p>Each scene is a card with:</p>
      <ul>
        <li>
          <strong>Its number and first picture</strong> — drawn at the size of the finished film, and used as
          the scene&rsquo;s opening shot when you render.
        </li>
        <li>
          <strong>Its camera move</strong>, on the picture — a push in, a pan, a pull back — chosen from what
          the shot is doing and the mood of the scene.
        </li>
        <li>
          <strong>Its mood</strong>, such as <em>tense</em> or <em>melancholic</em>, which also shapes the
          lighting and the music.
        </li>
        <li>
          <strong>Its lines</strong>, each with the character who speaks it and the voice they will speak in.
        </li>
      </ul>
      <p>
        The script appears before the pictures, so you can start reading while each frame is still being
        drawn.
      </p>

      <H2 id="editing">Fixing a scene</H2>
      <p>
        Press <strong>Edit</strong> on a scene&rsquo;s picture. You can change its <strong>title</strong>,{" "}
        <strong>what the camera sees</strong> (the description its pictures are drawn from) and any of its{" "}
        <strong>lines</strong>, then <strong>Save scene</strong>.
      </p>
      <ul>
        <li>Changing words — a title or a line — is free; nothing is redrawn.</li>
        <li>
          Changing what the camera sees redraws that scene&rsquo;s picture — one image, a few seconds.
        </li>
      </ul>
      <Note>
        Characters keep their faces. Each one&rsquo;s appearance is pinned when the script is written, so the
        same person looks the same in every shot, and still does after you re-render.
      </Note>

      <H2 id="before-render">Before you render</H2>
      <p>
        Under the storyboard, choose the <strong>voices</strong> (press <strong>Hear it</strong> for a sample)
        and the <strong>subtitle language</strong>. Next to <strong>Render this film</strong> you can see how
        many pictures the render will draw. Every saved scene is a version, so a storyboard edit can be undone
        like anything else.
      </p>
    </>
  );
}

export function Rendering() {
  return (
    <>
      <H2 id="what-happens">What happens</H2>
      <p>When you press <strong>Render this film</strong>, in order:</p>
      <ol>
        <li>
          <strong>The voices are recorded</strong>, every line in its character&rsquo;s voice, several at a
          time.
        </li>
        <li>
          <strong>The timeline is laid out.</strong> Each line gets its exact place in the film; the pictures,
          the music and the subtitles all follow this one timeline, which is why they stay in step to the
          frame.
        </li>
        <li>
          <strong>The pictures are drawn</strong>: a portrait of each character and a few shots per scene — a
          wide view, a detail, another angle. The storyboard picture is reused as each scene&rsquo;s opening
          shot.
        </li>
        <li>
          <strong>Every shot gets a camera move</strong>, and the scenes are cut together: a character&rsquo;s
          line shows that character, a narrator&rsquo;s line shows the scene, and every cut lands on a line.
        </li>
        <li>
          <strong>Music is mixed under the dialogue</strong> — it dips while someone speaks and rises in the
          gaps — and the whole film is levelled to a consistent loudness.
        </li>
        <li>
          <strong>Subtitles are added</strong> in the language you chose. See{" "}
          <Link href="/docs/subtitles/">Subtitles and languages</Link>.
        </li>
      </ol>

      <H2 id="how-long">How long it takes</H2>
      <p>
        Rendering is real work for the computer: every shot is computed frame by frame. On the free server
        this site runs on, a <strong>30-second, 3-scene film takes about three minutes</strong>, and a
        45-second film with 5 scenes about five. A film shorter, or with fewer scenes, is quicker.
      </p>
      <p>
        Films render one at a time. If someone else&rsquo;s render is running, yours waits its turn and
        starts on its own.
      </p>
      <Note kind="heads-up">
        You can close the page. The render carries on, and opening the film from your library picks the
        progress up where it is. <strong>Stop</strong> ends a render at its next step and leaves nothing
        half-made behind.
      </Note>

      <H2 id="result">What you get</H2>
      <ul>
        <li>An MP4 at 1280×720, 24 frames a second — <strong>Download</strong> under the player.</li>
        <li>Chapters in the player, one per scene, so you can jump straight to any of them.</li>
        <li>
          The subtitles burned into the picture, so they show in any player — and when that language
          isn&rsquo;t English, the English text as a track you can switch on.
        </li>
      </ul>
    </>
  );
}
