import type { Metadata, Viewport } from "next";

import { monoFont, titleFont, uiFont } from "@/lib/fonts";

import "./globals.css";

export const metadata: Metadata = {
  title: "Agentic Video Generator",
  description: "One sentence in. A finished short film out — script, voices, pictures, music and cuts.",
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
