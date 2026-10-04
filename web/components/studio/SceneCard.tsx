"use client";

import { useState } from "react";

import type { SceneEdit } from "@/lib/api";
import type { Card } from "@/lib/types";

import styles from "./studio.module.css";

type Props = {
  card: Card;
  /** Position in the film, from one. */
  number: number;
  /** The scene the eye should land on — currently drawing, or just finished. */
  active?: boolean;
  /** Editing is only offered once the storyboard is settled. */
  editable?: boolean;
  delay?: number;
  onSave?: (edit: SceneEdit) => Promise<void>;
};

export function SceneCard({ card, number, active, editable, delay = 0, onSave }: Props) {
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [title, setTitle] = useState(card.title);
  const [visual, setVisual] = useState(card.visual_prompt ?? "");
  const [lines, setLines] = useState<Record<string, string>>(
    Object.fromEntries(card.lines.map((l) => [l.line_id, l.text])),
  );

  const drawn = Boolean(card.preview_url);
  const visualChanged = visual.trim() !== (card.visual_prompt ?? "").trim();

  async function save() {
    if (!onSave) return;
    setSaving(true);
    const changedLines = Object.fromEntries(
      Object.entries(lines).filter(
        ([id, text]) => text.trim() !== card.lines.find((l) => l.line_id === id)?.text,
      ),
    );
    try {
      await onSave({
        ...(title.trim() !== card.title ? { title: title.trim() } : {}),
        ...(visualChanged ? { visual_prompt: visual.trim() } : {}),
        ...(Object.keys(changedLines).length ? { dialogue: changedLines } : {}),
      });
      setEditing(false);
    } finally {
      setSaving(false);
    }
  }

  return (
    <article
      className={`${styles.scene} ${active ? styles.active : ""} rise`}
      style={{ "--delay": `${delay}ms` } as React.CSSProperties}
      aria-busy={!drawn}
    >
      <div className={`${styles.frame} ${drawn ? "" : styles.pending}`}>
        {drawn ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={card.preview_url!} alt={card.setting || card.title} />
        ) : (
          <span className={`label ${styles.drawing}`}>drawing</span>
        )}
        <div className={styles.shade} />
        <span className={styles.num}>{String(number).padStart(2, "0")}</span>
        {card.move && <span className={styles.move}>{card.move}</span>}
        {editable && !editing && (
          <button className={styles.editBtn} onClick={() => setEditing(true)}>
            Edit
          </button>
        )}
      </div>

      {editing ? (
        <div className={styles.editor}>
          <label className="field">
            <span className="label">Scene title</span>
            <input className="input" value={title} onChange={(e) => setTitle(e.target.value)} />
          </label>
          <label className="field">
            <span className="label">What the camera sees</span>
            <textarea
              className="textarea"
              rows={3}
              value={visual}
              onChange={(e) => setVisual(e.target.value)}
            />
          </label>
          {card.lines.map((l) => (
            <label key={l.line_id} className="field">
              <span className="label">{l.character}</span>
              <textarea
                className="textarea"
                rows={2}
                value={lines[l.line_id] ?? ""}
                onChange={(e) => setLines((prev) => ({ ...prev, [l.line_id]: e.target.value }))}
              />
            </label>
          ))}
          <p className={styles.editNote}>
            {visualChanged
              ? "Changing what the camera sees redraws this frame: one image."
              : "Text changes are free; nothing is redrawn."}
          </p>
          <div className={styles.editActions}>
            <button className="btn primary small" onClick={save} disabled={saving}>
              {saving ? "Saving" : "Save scene"}
            </button>
            <button className="btn quiet small" onClick={() => setEditing(false)} disabled={saving}>
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <>
          <h3 className={styles.sceneTitle}>{card.title}</h3>
          {card.tone && <p className="label">{card.tone}</p>}
          {card.lines.map((l) => (
            <blockquote key={l.line_id} className={styles.line}>
              {l.text}
              <cite>
                {l.character}
                {l.voice ? ` · ${l.voice}` : ""}
              </cite>
            </blockquote>
          ))}
        </>
      )}
    </article>
  );
}
