import Link from "next/link";

import { CardLinks, H2, Note, Say, Step, Steps } from "../Docs";

export function Introduction() {
  return (
    <>
      <p>
        A <strong>dastango</strong> is a teller of <em>dastans</em>, the long tales once performed by
        lamplight in the old cities of South Asia. Dastango does the same with one sentence from you: it
        writes the story, casts it, gives every character a voice, draws the pictures, moves the camera,
        scores it, adds subtitles, and cuts it into a short film.
      </p>
      <p>Then you can talk to it. Say what to change, and it changes only that.</p>

      <H2 id="four-moments">How a film comes together</H2>
      <p>Making a film has four moments, and you are in charge at each one.</p>
      <Steps>
        <Step title="Write">
          One sentence and a length: 20 seconds to a minute, two to six scenes.
        </Step>
        <Step title="Storyboard">
          In about twenty seconds you can read the whole film: every scene, its mood, its camera move, its
          lines and who speaks them, with a first picture for each. Fix anything here. It costs nothing yet.
        </Step>
        <Step title="Render">
          Voices are recorded, every shot is drawn and given a camera move, the music is mixed under the
          dialogue, and the subtitles are laid in. A few minutes later you have the film, with its scenes as
          chapters.
        </Step>
        <Step title="Change it in words">
          <Say>Make the voices in scene 2 whispered.</Say> It tells you what it understood, then re-renders
          only what that touches. Every version is kept, and you can go back to any of them.
        </Step>
      </Steps>

      <H2 id="where-to-go">Where to go next</H2>
      <CardLinks
        items={[
          { href: "/docs/quickstart/", title: "Your first film", text: "Step by step, with what to expect at each point." },
          { href: "/docs/prompts/", title: "Writing a prompt", text: "What a good sentence gives the storyteller." },
          { href: "/docs/editing/", title: "Changing it in words", text: "Everything you can ask for, with examples." },
          { href: "/docs/faq/", title: "Limits and FAQ", text: "What it doesn't do yet, and common questions." },
        ]}
      />

      <H2 id="what-it-is-not">What it is and isn&rsquo;t</H2>
      <p>
        Dastango makes <strong>animated short films from still pictures</strong>: every shot is a generated
        image brought to life with a camera move (a push in, a slow pan, a pull back) chosen for what the
        shot is doing. It is not a video model; faces don&rsquo;t lip-sync and nothing in the frame moves on
        its own. What it does carefully is everything around the pictures: one timeline that keeps voices,
        cuts and subtitles in step to the frame, characters that keep their faces from shot to shot, and
        edits that are understood before they are made.
      </p>
      <Note>
        Building or hosting it yourself? Start with <Link href="/docs/developers/">For developers</Link>.
      </Note>
    </>
  );
}

export function Quickstart() {
  return (
    <>
      <H2 id="before">Before you start</H2>
      <p>
        You need an account. Sign in with an email and a password of at least ten characters, or with{" "}
        <strong>Continue with GitHub</strong>. See <Link href="/docs/account/">Accounts and sign-in</Link>.
      </p>

      <H2 id="steps">Make a film</H2>
      <Steps>
        <Step title="Write one sentence">
          <p>
            On the home page, type an idea, or tap one of the suggestions to start from it. A person, a
            place and something that changes is plenty:
          </p>
          <p>
            <Say>A clockmaker in a flooded city repairs the hours people lose.</Say>
          </p>
        </Step>
        <Step title="Choose a length">
          <p>
            Pick <strong>20, 30, 45 or 60 seconds</strong> and <strong>2 to 6 scenes</strong>. For a first
            film, 30 seconds and 3 scenes is a good size.
          </p>
        </Step>
        <Step title="Write the storyboard">
          <p>
            The script arrives first, in a few seconds: every scene with its mood, camera move and lines. Then
            each scene&rsquo;s first picture develops in. The whole storyboard takes about twenty seconds.
          </p>
        </Step>
        <Step title="Read it, and fix what you like">
          <p>
            Open any scene to change its title, what the camera sees, or its lines. Changing words is free;
            changing what the camera sees redraws that one picture. See{" "}
            <Link href="/docs/storyboard/">The storyboard</Link>.
          </p>
        </Step>
        <Step title="Choose voices and subtitles, then render">
          <p>
            Pick a voice engine (press <strong>Hear it</strong> to listen first) and a subtitle language, then{" "}
            <strong>Render this film</strong>. The number of pictures it will draw is shown next to the button.
          </p>
        </Step>
        <Step title="Watch">
          <p>
            A few minutes later the film plays, with its scenes as chapters you can jump between. Download it
            with <strong>Download</strong>.
          </p>
        </Step>
        <Step title="Change something">
          <p>
            Under the film, type what to change: <Say>make scene 2 darker</Say>, <Say>remove the music</Say>.
            See <Link href="/docs/editing/">Changing it in words</Link>.
          </p>
        </Step>
      </Steps>

      <Note kind="heads-up">
        You can leave the page while a film renders. It keeps going on the server; open it again from your
        library and the progress picks up where it is.
      </Note>
    </>
  );
}
