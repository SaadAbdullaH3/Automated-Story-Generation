// Fonts are downloaded at build time and served with the app — nothing is
// fetched from Google when a page loads.
//
// The title face is the one design decision still open (see
// docs/mockups/fonts.html for all eight candidates set in place), so it is
// chosen here and nowhere else. Swapping it is this one import.
import { Fraunces, JetBrains_Mono, Outfit } from "next/font/google";

export const titleFont = Fraunces({
  subsets: ["latin"],
  variable: "--font-title",
  // SOFT rounds the terminals, WONK gives the italic-leaning n/m/h their lean;
  // together they are what makes it read as storytelling rather than software.
  axes: ["SOFT", "WONK", "opsz"],
  display: "swap",
});

export const uiFont = Outfit({
  subsets: ["latin"],
  variable: "--font-ui",
  display: "swap",
});

export const monoFont = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-mono",
  display: "swap",
});
