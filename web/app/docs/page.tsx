import type { Metadata } from "next";

import { DocsArticle } from "@/components/docs/Docs";
import { Introduction } from "@/components/docs/pages/start";
import { docBySlug } from "@/lib/docs";

export const metadata: Metadata = {
  title: "Documentation",
  description: docBySlug("")?.summary,
};

export default function DocsHome() {
  return (
    <DocsArticle slug="">
      <Introduction />
    </DocsArticle>
  );
}
