import Link from "next/link";

import { REPO_URL } from "@/lib/docs";

import styles from "../docs.module.css";
import { H2, Note } from "../Docs";

export function Account() {
  return (
    <>
      <H2 id="signing-in">Signing in</H2>
      <ul>
        <li>
          <strong>With an email and password.</strong> Any address works — nothing is sent to it — with a
          password of at least ten characters.
        </li>
        <li>
          <strong>With GitHub.</strong> Press <strong>Continue with GitHub</strong> and approve on GitHub. Your
          account is tied to your GitHub account itself, so renaming yourself on GitHub doesn&rsquo;t matter.
        </li>
      </ul>
      <p>
        Whether new accounts can be made is up to whoever runs the server; when sign-ups are closed, the
        sign-in page only offers signing in.
      </p>

      <H2 id="github">Adding GitHub to an existing account</H2>
      <p>
        If you made your account with an email, sign in with it and press <strong>Connect GitHub</strong> in
        the top bar. After that, <strong>Continue with GitHub</strong> signs you straight in.
      </p>
      <Note kind="heads-up">
        Signing in with GitHub never joins an account just because the email address matches. Addresses
        aren&rsquo;t verified here, so matching them would hand your account to whoever typed your address
        first. Connecting is always something you do while signed in.
      </Note>

      <H2 id="privacy">Who can see your films</H2>
      <p>
        Only you. Your films, their storyboards and every version are private to your account — down to the
        video file itself, which is only served to you. Asking for anyone else&rsquo;s film is answered as if
        it didn&rsquo;t exist.
      </p>

      <H2 id="security">Staying signed in</H2>
      <ul>
        <li>You stay signed in for 14 days on a device, unless you sign out.</li>
        <li>Changing your password signs you out everywhere else.</li>
        <li>Eight wrong passwords in a row lock the account for 15 minutes.</li>
        <li>
          There is no password reset by email yet — the server&rsquo;s administrator can set a new one for
          you.
        </li>
      </ul>
    </>
  );
}

export function Faq() {
  return (
    <>
      <H2 id="limits">Limits</H2>
      <div className={styles.tableWrap}>
        <table>
          <thead>
            <tr>
              <th>What</th>
              <th>Limit</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>Film length</td>
              <td>20, 30, 45 or 60 seconds</td>
            </tr>
            <tr>
              <td>Scenes</td>
              <td>2 to 6</td>
            </tr>
            <tr>
              <td>Picture</td>
              <td>1280×720 at 24 frames a second</td>
            </tr>
            <tr>
              <td>Renders at once</td>
              <td>One — others wait their turn</td>
            </tr>
            <tr>
              <td>Subtitle languages</td>
              <td>14</td>
            </tr>
          </tbody>
        </table>
      </div>

      <H2 id="questions">Questions</H2>

      <h3>Is it real video?</h3>
      <p>
        No — every shot is a still picture brought to life with a camera move, and faces don&rsquo;t move
        their lips. Real motion is built in but switched off, because every service that does it well charges
        per clip, and this site runs at no cost.
      </p>

      <h3>Why does a picture look plain, or not match its scene?</h3>
      <p>
        The pictures come from free image services with daily limits. When the best one has run out for the
        day, the next one draws the picture — usually a little less detailed — and if every one is out, a
        plain placeholder keeps the film whole. The limits reset every day. Occasionally a service&rsquo;s
        safety filter refuses an innocent description, with the same result for that one picture.
      </p>

      <h3>Why did my render wait before starting?</h3>
      <p>
        Films render one at a time on this server. Yours started as soon as the one ahead of it finished.
      </p>

      <h3>What if something goes wrong part-way?</h3>
      <p>
        The page says what happened. A failed change leaves the film exactly as it was. If the server
        restarts during a render, the render starts again by itself once it is back.
      </p>

      <h3>Can I download my film?</h3>
      <p>
        Yes — <strong>Download</strong> under the player gives you the MP4, with the subtitles you chose
        burned in.
      </p>

      <h3>Who can see what I make?</h3>
      <p>
        Only you. See <Link href="/docs/account/#privacy">Accounts and sign-in</Link>.
      </p>

      <h3>Why do the voices sound different from the sample?</h3>
      <p>
        The sample is one fixed sentence in the engine&rsquo;s first voice; in the film each character gets the
        voice that suits them, and the tone you ask for changes it further.
      </p>
    </>
  );
}

export function Developers() {
  return (
    <>
      <p>
        Dastango is open source under the MIT licence. Its source, and the documentation of how it is built,
        are on <a href={REPO_URL}>GitHub</a>.
      </p>

      <H2 id="architecture">How it is built</H2>
      <p>
        A Next.js interface served by a FastAPI backend. Starting a film writes a job to a PostgreSQL
        database and returns at once; worker processes claim jobs and run four agents — story, audio, video
        and edit — through an orchestrator, with every model behind a configurable chain of providers that
        ends in something offline. Progress is written to the database too, which is how the page can be
        closed and reopened mid-render. Every step is saved as a version with copies of its files.
      </p>
      <div className={styles.tableWrap}>
        <table>
          <thead>
            <tr>
              <th>Read</th>
              <th>For</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>
                <a href={`${REPO_URL}/blob/main/docs/ARCHITECTURE.md`}>Software architecture</a>
              </td>
              <td>Processes, request flows, the job lifecycle, the data model, deployment</td>
            </tr>
            <tr>
              <td>
                <a href={`${REPO_URL}/blob/main/docs/AGENTS.md`}>Agentic architecture</a>
              </td>
              <td>The orchestrator, the agents, provider chains, the timeline, the edit agent</td>
            </tr>
            <tr>
              <td>
                <a href={`${REPO_URL}/blob/main/docs/TECH_STACK.md`}>Technology stack</a>
              </td>
              <td>Every framework and model, where it is used and why</td>
            </tr>
            <tr>
              <td>
                <a href={`${REPO_URL}/blob/main/docs/DECISIONS.md`}>Architecture decisions</a>
              </td>
              <td>The choices that shape it, with the alternatives and their costs</td>
            </tr>
          </tbody>
        </table>
      </div>

      <H2 id="api">The API</H2>
      <p>
        Everything the interface does goes through a REST API with a progress WebSocket. The interactive
        reference is at <a href="/api/docs">/api/docs</a>; sign in to the app in the same browser and its
        requests work there too. The full description is in{" "}
        <a href={`${REPO_URL}/blob/main/docs/API.md`}>API.md</a>.
      </p>

      <H2 id="self-host">Running your own</H2>
      <p>On a laptop, with Python 3.11 and FFmpeg:</p>
      <pre>
        <code>{`pip install -r requirements.txt
cd web && npm ci && npm run build && cd ..
python main.py serve        # http://localhost:8000`}</code>
      </pre>
      <p>
        With no keys at all it runs offline, with template scripts and placeholder pictures; free keys for
        the language and image models are listed in{" "}
        <a href={`${REPO_URL}/blob/main/docs/PROVIDERS.md`}>PROVIDERS.md</a>. On a server, the whole stack —
        HTTPS, PostgreSQL, workers and nightly backups — runs from one Docker Compose file; the runbook goes
        from an empty free cloud VM to a working address in{" "}
        <a href={`${REPO_URL}/blob/main/deploy/README.md`}>deploy/README.md</a>.
      </p>
    </>
  );
}
