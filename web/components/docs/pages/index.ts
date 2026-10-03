// Page bodies by slug; the titles and order live in lib/docs.ts.
import { Editing, Versions } from "./changing";
import { Prompts, Rendering, Storyboard } from "./making";
import { Account, Developers, Faq } from "./reference";
import { Subtitles, Voices } from "./sound";
import { Introduction, Quickstart } from "./start";

export const PAGE_BODIES: Record<string, () => React.ReactNode> = {
  "": Introduction,
  quickstart: Quickstart,
  prompts: Prompts,
  storyboard: Storyboard,
  rendering: Rendering,
  editing: Editing,
  versions: Versions,
  voices: Voices,
  subtitles: Subtitles,
  account: Account,
  faq: Faq,
  developers: Developers,
};
