// The documentation's table of contents: the sidebar, the page titles, the
// previous/next links and the filter all read from here, so a page is added in
// one place. The page bodies are in components/docs/pages/.

export type DocPage = {
  /** "" is the introduction at /docs/. */
  slug: string;
  title: string;
  /** One line under the title, and the description link previews show. */
  summary: string;
  section: string;
  /** Extra words the sidebar filter matches on. */
  keywords: string[];
};

export const DOC_PAGES: DocPage[] = [
  {
    slug: "",
    title: "Introduction",
    summary: "What Dastango makes, and how a film comes together.",
    section: "Getting started",
    keywords: ["overview", "what", "about"],
  },
  {
    slug: "quickstart",
    title: "Your first film",
    summary: "From one sentence to a finished film in a few minutes.",
    section: "Getting started",
    keywords: ["start", "begin", "tutorial", "first"],
  },
  {
    slug: "prompts",
    title: "Writing a prompt",
    summary: "What a good sentence gives the storyteller to work with.",
    section: "Making a film",
    keywords: ["sentence", "idea", "length", "scenes", "style"],
  },
  {
    slug: "storyboard",
    title: "The storyboard",
    summary: "Read every scene and fix it before anything is rendered.",
    section: "Making a film",
    keywords: ["scene", "frame", "edit", "lines", "picture", "camera"],
  },
  {
    slug: "rendering",
    title: "Rendering",
    summary: "What happens when you press render, and how long it takes.",
    section: "Making a film",
    keywords: ["render", "time", "queue", "download", "stop", "cancel"],
  },
  {
    slug: "editing",
    title: "Changing it in words",
    summary: "Say what to change; it shows what it understood, then does exactly that.",
    section: "Changing a film",
    keywords: ["edit", "change", "voice", "music", "filter", "darker", "speed", "genre"],
  },
  {
    slug: "versions",
    title: "Versions",
    summary: "Every change is kept, and going back is one click.",
    section: "Changing a film",
    keywords: ["undo", "revert", "history", "go back"],
  },
  {
    slug: "voices",
    title: "Voices",
    summary: "Who speaks each line, and how to hear a voice before you choose it.",
    section: "Sound and text",
    keywords: ["kokoro", "edge", "tts", "tone", "narrator", "sample"],
  },
  {
    slug: "subtitles",
    title: "Subtitles and languages",
    summary: "Fourteen languages, burned into the picture and as tracks.",
    section: "Sound and text",
    keywords: ["urdu", "language", "translate", "captions", "rtl", "arabic", "hindi"],
  },
  {
    slug: "account",
    title: "Accounts and sign-in",
    summary: "Signing in with an email or GitHub, and who can see your films.",
    section: "Account",
    keywords: ["login", "github", "password", "privacy", "sign up"],
  },
  {
    slug: "faq",
    title: "Limits and FAQ",
    summary: "What Dastango does not do yet, and answers to common questions.",
    section: "Reference",
    keywords: ["help", "problem", "placeholder", "quota", "limits", "questions"],
  },
  {
    slug: "developers",
    title: "For developers",
    summary: "The architecture, the API, and running your own copy.",
    section: "Reference",
    keywords: ["api", "architecture", "self-host", "github", "source", "license"],
  },
];

export const SECTIONS = Array.from(new Set(DOC_PAGES.map((p) => p.section)));

export function docHref(slug: string): string {
  return slug ? `/docs/${slug}/` : "/docs/";
}

export function docBySlug(slug: string): DocPage | undefined {
  return DOC_PAGES.find((p) => p.slug === slug);
}

export function neighbours(slug: string): { prev?: DocPage; next?: DocPage } {
  const i = DOC_PAGES.findIndex((p) => p.slug === slug);
  return { prev: DOC_PAGES[i - 1], next: DOC_PAGES[i + 1] };
}

export const REPO_URL = "https://github.com/SaadAbdullaH3/Automated-Story-Generation";
