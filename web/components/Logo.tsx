import styles from "./logo.module.css";

/** The name, in one place. */
export const BRAND = "Dastango";

/*
 * The mark: a Mughal arch — the storyteller's niche, a stage, a screen — with
 * the lamp a dastango tells by. The arch takes the text colour and the flame
 * is the one accent, so it sits right on any surface the interface has.
 */
const ARCH = "M14 56 V32 C14 24 20 20 25 16.5 C28.5 14 30.8 11 32 6 C33.2 11 35.5 14 39 16.5 C44 20 50 24 50 32 V56 M8 56 H56";
const FLAME = "M32 25 C36.5 31 39 35 39 39 C39 43 36 45.5 32 45.5 C28 45.5 25 43 25 39 C25 35 27.5 31 32 25 Z";

export function LogoMark({ size = 28, label }: { size?: number; label?: string }) {
  return (
    <svg
      className={styles.mark}
      width={size}
      height={size}
      viewBox="0 0 64 64"
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
    >
      <path
        d={ARCH}
        fill="none"
        stroke="currentColor"
        strokeWidth={size < 24 ? 6 : 4.5}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path d={FLAME} fill="var(--ember)" />
    </svg>
  );
}

/** Mark and name together, as the header uses it. */
export function Logo({ size = 28 }: { size?: number }) {
  return (
    <span className={styles.logo}>
      <LogoMark size={size} />
      <span className={`title ${styles.word}`}>{BRAND}</span>
    </span>
  );
}
