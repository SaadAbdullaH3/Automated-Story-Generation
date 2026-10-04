import Link from "next/link";

import styles from "../docs.module.css";
import { H2, Note, Say } from "../Docs";

const TONES = ["whispered", "soft", "deep", "warm", "sad", "cheerful", "anxious", "angry", "loud"];
const MOODS = ["ambient", "tense", "joyful", "mysterious", "epic", "sad", "ominous", "ethereal", "energetic", "neutral"];
const LOOKS = ["cinematic", "noir", "vintage", "sepia", "grayscale", "dreamy", "pastel", "anime", "cold thriller", "warm", "cool"];

function List({ words }: { words: string[] }) {
  return (
    <>
      {words.map((w, i) => (
        <span key={w}>
          <code>{w}</code>
          {i < words.length - 1 ? " " : ""}
        </span>
      ))}
    </>
  );
}

export function Editing() {
  return (
    <>
      <p>
        Under a finished film, type what you want changed: one sentence, the way you would say it to an
        editor. Dastango first shows what it took that to mean, for example{" "}
        <em>whispered voices · scene 2</em>, then makes exactly that change and nothing else.
      </p>

      <H2 id="how">How a change is made</H2>
      <ol>
        <li>
          <strong>It is understood first.</strong> Your sentence is matched to one of the changes below, with
          where it applies. The interpretation appears before any work starts, so a misreading is visible
          straight away.
        </li>
        <li>
          <strong>Only what it touches is redone.</strong> Whispering the voices in scene 2 re-records scene
          2&rsquo;s lines and re-cuts the film to their new length; the other scenes&rsquo; pictures are kept.
          Most changes take seconds to a couple of minutes.
        </li>
        <li>
          <strong>It is saved as a new version.</strong> See <Link href="/docs/versions/">Versions</Link>.
        </li>
      </ol>

      <H2 id="what-you-can-ask">What you can ask for</H2>
      <div className={styles.tableWrap}>
        <table>
          <thead>
            <tr>
              <th>Change</th>
              <th>Say something like</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>How voices sound</td>
              <td>
                <Say>make the voices in scene 2 whispered</Say>, <Say>change voice tone to deep</Say>
              </td>
            </tr>
            <tr>
              <td>Different voices</td>
              <td>
                <Say>change voice for the narrator</Say>
              </td>
            </tr>
            <tr>
              <td>Louder or quieter</td>
              <td>
                <Say>make it louder</Say>, <Say>make everyone quieter</Say>
              </td>
            </tr>
            <tr>
              <td>Background music</td>
              <td>
                <Say>add background music, tense</Say>, <Say>remove background music</Say>
              </td>
            </tr>
            <tr>
              <td>Record the voices again</td>
              <td>
                <Say>regenerate the audio</Say>
              </td>
            </tr>
            <tr>
              <td>Lighter, darker, warmer, cooler</td>
              <td>
                <Say>make scene 2 darker</Say>, <Say>make scene 1 brighter</Say>,{" "}
                <Say>apply a warm filter</Say>
              </td>
            </tr>
            <tr>
              <td>A look over the pictures</td>
              <td>
                <Say>apply a vintage filter</Say>, <Say>noir style</Say>
              </td>
            </tr>
            <tr>
              <td>New pictures</td>
              <td>
                <Say>regenerate scene 1</Say>
              </td>
            </tr>
            <tr>
              <td>A character&rsquo;s appearance</td>
              <td>
                <Say>change character design</Say>
              </td>
            </tr>
            <tr>
              <td>Subtitles on or off</td>
              <td>
                <Say>remove the subtitles</Say>, <Say>add subtitles please</Say>
              </td>
            </tr>
            <tr>
              <td>Faster or slower</td>
              <td>
                <Say>speed up</Say> (1.5×), <Say>slow down the video</Say> (0.75×), or name a speed:{" "}
                <Say>speed up 1.25x</Say>
              </td>
            </tr>
            <tr>
              <td>A new story</td>
              <td>
                <Say>regenerate the script</Say>, <Say>change the genre to comedy</Say>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <h3>The words it knows</h3>
      <p>
        <strong>Voice tones:</strong> <List words={TONES} />. Close words work too: <em>whisper</em>,{" "}
        <em>gentle</em>, <em>happy</em>, <em>nervous</em>.
      </p>
      <p>
        <strong>Music moods:</strong> <List words={MOODS} />.
      </p>
      <p>
        <strong>Looks:</strong> <List words={LOOKS} />, plus <code>darker</code> and <code>brighter</code>.
      </p>

      <H2 id="where">Saying where</H2>
      <p>A change applies to the whole film unless you say otherwise:</p>
      <ul>
        <li>
          <strong>A scene, by number</strong>: <Say>make scene 3 darker</Say>.
        </li>
        <li>
          <strong>A scene, by what it&rsquo;s called</strong>: <Say>the recipe scene should feel darker</Say>.
        </li>
        <li>
          <strong>A character, by name</strong>: <Say>make Mira&rsquo;s voice softer</Say>.
        </li>
      </ul>
      <p>
        Changes build on each other: whisper scene 2, then make everyone louder, and scene 2 is a louder
        whisper, not undone.
      </p>

      <H2 id="refusals">When it says no</H2>
      <p>
        If it can&rsquo;t tell what you mean (<Say>make it better</Say>), it says so, suggests the kinds of
        thing you can ask for, and changes nothing. If something it needs is missing, it asks:{" "}
        <em>which tone? whispered, soft, …</em>
      </p>
      <Note kind="heads-up">
        If a change fails part-way, the film is put back exactly as it was before you asked. A change is
        never reported as made when it wasn&rsquo;t.
      </Note>

      <H2 id="story-changes">Changes that rewrite the story</H2>
      <p>
        <Say>Regenerate the script</Say> and genre changes write a new story, so the whole film is made
        again: voices, pictures and all. Voice changes you made to particular scenes don&rsquo;t carry over,
        because those scenes no longer exist.
      </p>
    </>
  );
}

export function Versions() {
  return (
    <>
      <p>
        Every step that changes a film is saved as a version: the storyboard, each scene you edit, the
        render, and every change you ask for. They are listed under the film in your own words{" "}
        (<em>“make the voices in scene 2 whispered”</em>), newest first.
      </p>

      <H2 id="going-back">Going back</H2>
      <p>
        Press <strong>Go back</strong> on any version. The film returns to exactly how it was then (not just
        the script, but the recordings, the pictures and the cut) and plays straight away.
      </p>
      <p>
        Going back is itself saved as a <strong>new</strong> version. Nothing is overwritten, so you can go
        back, try something else, and still return to anything in between.
      </p>

      <H2 id="what-is-kept">What a version keeps</H2>
      <p>
        Everything the film is made of: the script and cast, every voice recording, every picture and shot,
        the music, the subtitles and the finished video. That is what makes going back exact: the film you
        get is identical to the one you had.
      </p>
      <Note>Trying a change costs nothing: if you don&rsquo;t like it, go back one version.</Note>
    </>
  );
}
