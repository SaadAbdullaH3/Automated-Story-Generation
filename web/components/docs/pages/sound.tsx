import Link from "next/link";

import styles from "../docs.module.css";
import { H2, Note, Say } from "../Docs";

export function Voices() {
  return (
    <>
      <p>
        Every character speaks in their own voice, chosen to suit who they are — a narrator gets a
        storyteller&rsquo;s voice, a child a young one, an old man an older one — and keeps it for the whole
        film.
      </p>

      <H2 id="engines">Voice engines</H2>
      <p>
        Before rendering you can choose which engine records the voices. The picker shows which ones work on
        this server and, for any that don&rsquo;t, why.
      </p>
      <div className={styles.tableWrap}>
        <table>
          <thead>
            <tr>
              <th>Engine</th>
              <th>What it&rsquo;s like</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>
                <strong>Kokoro</strong> (default)
              </td>
              <td>
                Natural, expressive voices from an open-source model that runs on the server itself — eight
                voices, US and British, from <em>Heart</em> (warm) to <em>George</em> (a narrator).
              </td>
            </tr>
            <tr>
              <td>Edge Neural</td>
              <td>Microsoft&rsquo;s neural voices, online, with several accents.</td>
            </tr>
            <tr>
              <td>Google Translate</td>
              <td>Clear but flat — no emotion or pacing. A fallback more than a choice.</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p>
        Press <strong>Hear it</strong> next to the picker to hear a one-line sample before committing to a
        render. If the engine you chose couldn&rsquo;t speak and another stepped in, the sample says so.
      </p>

      <H2 id="tone">Changing how they sound</H2>
      <p>
        After the film is made, change the voices in words: <Say>make the voices in scene 2 whispered</Say>,{" "}
        <Say>make Mira&rsquo;s voice softer</Say>, <Say>make it louder</Say>. Tones it knows: whispered, soft,
        deep, warm, sad, cheerful, anxious, angry and loud. See{" "}
        <Link href="/docs/editing/">Changing it in words</Link>.
      </p>
      <Note>
        A tone you give one scene stays with that scene: later changes to everyone build on it rather than
        resetting it.
      </Note>
    </>
  );
}

const LANGUAGES = [
  "English",
  "Urdu",
  "Hindi",
  "Arabic",
  "French",
  "Spanish",
  "German",
  "Italian",
  "Portuguese",
  "Russian",
  "Turkish",
  "Japanese",
  "Korean",
  "Chinese",
];

export function Subtitles() {
  return (
    <>
      <H2 id="languages">Fourteen languages</H2>
      <p>Choose the subtitle language before you render:</p>
      <p>
        {LANGUAGES.map((l, i) => (
          <span key={l}>
            <code>{l}</code>
            {i < LANGUAGES.length - 1 ? " " : ""}
          </span>
        ))}
      </p>
      <p>
        The dialogue is translated by a language model, line by line, keeping each line short enough to read
        on screen.
      </p>

      <H2 id="burned-in">Burned in, and as tracks</H2>
      <p>
        The language you choose is <strong>drawn into the picture</strong>, because most video players
        don&rsquo;t show subtitle tracks unless you switch them on — so it appears wherever the film is
        played, including after you download it. When that language isn&rsquo;t English, the English text
        comes along as a track you can turn on in the player.
      </p>

      <H2 id="scripts">Right-to-left and other scripts</H2>
      <p>
        Urdu and Arabic are shaped and set right to left, and Hindi, Japanese, Korean and Chinese are drawn
        with fonts that contain their characters. That is checked automatically: every script is rendered
        and inspected for the empty boxes that appear when a font is missing a character.
      </p>
      <Note kind="heads-up">
        If a translation fails, that language is left out rather than shown as English under the wrong name.
        You can add subtitles again later with <Say>add subtitles</Say>.
      </Note>
    </>
  );
}
