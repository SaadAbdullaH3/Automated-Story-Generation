import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { DocsArticle } from "@/components/docs/Docs";
import { PAGE_BODIES } from "@/components/docs/pages";
import { DOC_PAGES, docBySlug } from "@/lib/docs";

type Params = { params: Promise<{ slug: string }> };

// Every page is known at build time; anything else is a 404, not a guess.
export const dynamicParams = false;

export function generateStaticParams() {
  return DOC_PAGES.filter((p) => p.slug).map((p) => ({ slug: p.slug }));
}

export async function generateMetadata({ params }: Params): Promise<Metadata> {
  const page = docBySlug((await params).slug);
  return page ? { title: page.title, description: page.summary } : {};
}

export default async function DocPage({ params }: Params) {
  const { slug } = await params;
  const Body = PAGE_BODIES[slug];
  if (!Body || !docBySlug(slug)) notFound();
  return (
    <DocsArticle slug={slug}>
      <Body />
    </DocsArticle>
  );
}
