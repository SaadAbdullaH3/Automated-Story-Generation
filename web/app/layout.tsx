import type { Metadata, Viewport } from "next";

import { monoFont, titleFont, uiFont } from "@/lib/fonts";

import "./globals.css";

const DESCRIPTION =
  "One sentence in. A finished short film out: script, voices, pictures, music and cuts. Then change it by saying what you want.";

// Link previews (chat apps, email) need absolute URLs. The deployment's own
// address is baked in at build time; SITE_URL overrides it.
const SITE_URL = process.env.SITE_URL || "https://139-185-59-132.sslip.io";

export const metadata: Metadata = {
  metadataBase: new URL(SITE_URL),
  title: { default: "Dastango: a short film from one sentence", template: "%s · Dastango" },
  description: DESCRIPTION,
  applicationName: "Dastango",
  openGraph: {
    type: "website",
    siteName: "Dastango",
    title: "Dastango: a short film from one sentence",
    description: DESCRIPTION,
  },
  twitter: { card: "summary_large_image" },
};

export const viewport: Viewport = {
  themeColor: "#0d0c0b",
  colorScheme: "dark",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${titleFont.variable} ${uiFont.variable} ${monoFont.variable}`}>
      <body>{children}</body>
    </html>
  );
}
