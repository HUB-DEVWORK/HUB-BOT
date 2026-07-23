/* Screen — Картинки бота: on/off, one-vs-per-screen mode, and a media slot per screen.
   Each slot takes a URL / file_id / uploaded file (jpg/png/webp/gif/mp4 — a GIF animates). */

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";

import { api, getToken } from "../api/client";
import { Seg, Toggle } from "../components/ui";
import { useApp } from "../state/app";

type Slot = { key: string; label: string; value: string };
type Banners = { enabled: boolean; mode: string; slots: Slot[] };

export default function BotImages() {
  const { t, toast } = useApp();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["banners"], queryFn: () => api.get<Banners>("/api/admin/bot-menu/banners") });
  const [draft, setDraft] = useState<Partial<Banners> & { slotVals?: Record<string, string> }>({});
  const uploadingKey = useRef<string | null>(null);
  const [, force] = useState(0);
  const fileRefs = useRef<Record<string, HTMLInputElement | null>>({});

  const enabled = draft.enabled ?? q.data?.enabled ?? true;
  const mode = draft.mode ?? q.data?.mode ?? "one";
  const slotVal = (k: string): string =>
    draft.slotVals?.[k] ?? q.data?.slots.find((s) => s.key === k)?.value ?? "";
  const setSlot = (k: string, v: string) =>
    setDraft((d) => ({ ...d, slotVals: { ...(d.slotVals ?? {}), [k]: v } }));

  async function upload(key: string, f: File) {
    uploadingKey.current = key;
    force((n) => n + 1);
    try {
      const form = new FormData();
      form.append("file", f);
      const res = await fetch("/api/admin/upload", {
        method: "POST",
        headers: { Authorization: `Bearer ${getToken()}` },
        body: form,
      });
      if (!res.ok) throw new Error(await res.text());
      const data = (await res.json()) as { path: string };
      setSlot(key, data.path);
      toast("✓");
    } catch (e) {
      toast((e as Error).message);
    } finally {
      uploadingKey.current = null;
      force((n) => n + 1);
    }
  }

  async function save() {
    try {
      await api.put("/api/admin/bot-menu/banners", { enabled, mode, slots: draft.slotVals ?? {} });
      setDraft({});
      void qc.invalidateQueries({ queryKey: ["banners"] });
      toast(t.saved);
    } catch (e) {
      toast((e as Error).message);
    }
  }

  const slots = q.data?.slots ?? [];
  // In «one» mode only the default slot matters; per_screen shows all.
  const shown = mode === "one" ? slots.filter((s) => s.key === "BANNER_DEFAULT") : slots;

  function isImg(v: string): boolean {
    return /\.(jpg|jpeg|png|webp|gif)$/i.test(v) || v.startsWith("http") || v.startsWith("uploads/");
  }

  return (
    <>
      <div className="page-head">
        <h1 className="h1">🖼 {t.botImages}</h1>
        <div className="actions">
          <button className="btn primary" onClick={save}>{t.save}</button>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 14 }}>
        <div className="row" style={{ justifyContent: "space-between" }}>
          <div>
            <div className="caps">{t.showImage}</div>
            <div className="muted" style={{ fontSize: 12.5 }}>{t.showImageHint}</div>
          </div>
          <Toggle on={enabled} onChange={(v) => setDraft((d) => ({ ...d, enabled: v }))} />
        </div>
      </div>

      <div className="card" style={{ marginBottom: 14, opacity: enabled ? 1 : 0.5 }}>
        <div className="caps" style={{ marginBottom: 8 }}>{t.imageMode}</div>
        <Seg
          value={mode}
          options={[
            { id: "one", label: t.imageModeOne },
            { id: "per_screen", label: t.imageModePer },
          ]}
          onChange={(v) => setDraft((d) => ({ ...d, mode: v }))}
        />
        <div className="muted" style={{ fontSize: 12.5, marginTop: 8 }}>{t.imageModeHint}</div>
      </div>

      <div className="grid" style={{ gap: 10, opacity: enabled ? 1 : 0.5 }}>
        {shown.map((s) => {
          const v = slotVal(s.key);
          return (
            <div key={s.key} className="card">
              <div className="row" style={{ gap: 14, alignItems: "flex-start" }}>
                <div style={{ width: 90, height: 60, borderRadius: 8, border: "1px solid var(--border2)", overflow: "hidden", flex: "0 0 auto", display: "grid", placeItems: "center", background: "var(--panel2)" }}>
                  {v && isImg(v) ? (
                    <img src={v.startsWith("uploads/") ? "/" + v : v} alt="" style={{ maxWidth: "100%", maxHeight: "100%" }} />
                  ) : (
                    <span className="dim" style={{ fontSize: 20 }}>🚫</span>
                  )}
                </div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div className="caps" style={{ marginBottom: 4 }}>{s.label}{s.key !== "BANNER_DEFAULT" && <span className="dim"> · {t.imageFallbackDefault}</span>}</div>
                  <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
                    <input
                      className="input mono"
                      style={{ flex: 1, minWidth: 220 }}
                      placeholder="URL, file_id или загрузите файл"
                      value={v}
                      onChange={(e) => setSlot(s.key, e.target.value)}
                    />
                    <button className="btn secondary sm" disabled={uploadingKey.current === s.key} onClick={() => fileRefs.current[s.key]?.click()}>
                      {uploadingKey.current === s.key ? "…" : "⬆ " + t.uploadImage}
                    </button>
                    {v && (
                      <button className="btn danger sm" onClick={() => setSlot(s.key, "")}>✕</button>
                    )}
                    <input
                      ref={(el) => { fileRefs.current[s.key] = el; }}
                      type="file"
                      accept=".jpg,.jpeg,.png,.webp,.gif,.mp4"
                      style={{ display: "none" }}
                      onChange={(e) => { const f = e.target.files?.[0]; if (f) void upload(s.key, f); e.target.value = ""; }}
                    />
                  </div>
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </>
  );
}
