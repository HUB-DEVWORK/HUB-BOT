/* Screen 07 — Рассылки: presets, media upload, styled button, Telegram-like preview,
   history with live progress. */

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";

import { api, dtTime, getToken } from "../api/client";
import { Prog, Seg, Toggle } from "../components/ui";
import { useApp } from "../state/app";

type Audiences = Record<"all" | "active" | "trial" | "expired", number>;
type Broadcast = {
  id: number;
  audience: string;
  media: string;
  text: string;
  status: string;
  total: number;
  sent: number;
  failed: number;
  progress_pct: number;
  created_at: string | null;
};

type MediaKind = "text" | "photo" | "video" | "gif";
type BtnStyle = "" | "#2E63E7" | "#31A24C" | "#E53935";

const TG_BTN_COLORS: { id: BtnStyle; label: string; bg: string }[] = [
  { id: "", label: "Обычная", bg: "#2b3b4d" },
  { id: "#2E63E7", label: "Primary", bg: "#2E63E7" },
  { id: "#31A24C", label: "Success", bg: "#31A24C" },
  { id: "#E53935", label: "Danger", bg: "#E53935" },
];

export default function Broadcasts() {
  const { t, toast, confirm } = useApp();
  const qc = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);

  const [audience, setAudience] = useState<"all" | "active" | "trial" | "expired">("all");
  const [media, setMedia] = useState<MediaKind>("text");
  const [mediaPath, setMediaPath] = useState<string | null>(null);
  const [mediaUrl, setMediaUrl] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [btnOn, setBtnOn] = useState(false);
  const [btnText, setBtnText] = useState("Продлить со скидкой");
  const [btnKind, setBtnKind] = useState<"url" | "action">("action");
  const [btnUrl, setBtnUrl] = useState("");
  const [btnAction, setBtnAction] = useState("buy");
  const [btnColor, setBtnColor] = useState<BtnStyle>("#31A24C");
  const [emojiId, setEmojiId] = useState("");
  const [uploading, setUploading] = useState(false);

  const audiences = useQuery({
    queryKey: ["broadcast-audiences"],
    queryFn: () => api.get<Audiences>("/api/admin/broadcasts/audiences"),
  });
  const history = useQuery({
    queryKey: ["broadcasts"],
    queryFn: () => api.get<{ items: Broadcast[] }>("/api/admin/broadcasts"),
    refetchInterval: (q) =>
      q.state.data?.items.some((b) => b.status === "running" || b.status === "pending")
        ? 1500
        : false,
  });

  const a = audiences.data;
  const targetCount = a?.[audience] ?? 0;

  // Presets: one click fills the composer with a ready campaign.
  const presets: { id: string; label: string; apply: () => void }[] = [
    {
      id: "expiring",
      label: "⏳ Скоро закончится",
      apply: () => {
        setAudience("active");
        setMedia("text");
        setText(
          "⏳ <b>Подписка скоро закончится!</b>\n\nНе теряй защиту — продли сейчас и оставайся онлайн без ограничений. Продление занимает меньше минуты 👇",
        );
        setBtnOn(true);
        setBtnText("🔄 Продлить подписку");
        setBtnKind("action");
        setBtnAction("buy");
        setBtnColor("#31A24C");
      },
    },
    {
      id: "expired",
      label: "❌ Подписка истекла",
      apply: () => {
        setAudience("expired");
        setMedia("text");
        setText(
          "😔 <b>Твоя подписка закончилась</b>\n\nДоступ приостановлен, но всё легко вернуть: продли подписку — и защита снова заработает через минуту.",
        );
        setBtnOn(true);
        setBtnText("💳 Вернуть доступ");
        setBtnKind("action");
        setBtnAction("buy");
        setBtnColor("#2E63E7");
      },
    },
    {
      id: "trial-idle",
      label: "🎁 Триал без подключения",
      apply: () => {
        setAudience("trial");
        setMedia("text");
        setText(
          "🎁 <b>Ты взял пробный период, но ещё не подключился!</b>\n\nЭто занимает 2 минуты: скачай приложение, получи ссылку и нажми «Подключить». Поможем, если что-то не получается 👇",
        );
        setBtnOn(true);
        setBtnText("⚡ Подключиться");
        setBtnKind("action");
        setBtnAction("connect");
        setBtnColor("#31A24C");
      },
    },
  ];

  async function uploadFile(f: File) {
    setUploading(true);
    try {
      const form = new FormData();
      form.append("file", f);
      const res = await fetch("/api/admin/upload", {
        method: "POST",
        headers: { Authorization: `Bearer ${getToken()}` },
        body: form,
      });
      if (!res.ok) throw new Error(await res.text());
      const data = (await res.json()) as { path: string; url: string; kind: MediaKind };
      setMediaPath(data.path);
      setMediaUrl(data.url);
      if (data.kind !== "text") setMedia(data.kind);
      toast("✓ " + f.name);
    } catch (e) {
      toast(`${t.error}: ${(e as Error).message.slice(0, 80)}`);
    } finally {
      setUploading(false);
    }
  }

  async function send() {
    if (!text.trim()) return;
    if (!(await confirm(`${t.sendConfirm} → ${targetCount}`))) return;
    try {
      await api.post("/api/admin/broadcasts", {
        audience,
        media,
        text,
        media_path: media === "text" ? null : mediaPath,
        button_enabled: btnOn,
        button_text: btnOn ? btnText : null,
        button_url: btnOn && btnKind === "url" ? btnUrl : null,
        button_action: btnOn && btnKind === "action" ? btnAction : null,
        button_color: btnOn && btnColor ? btnColor : null,
        emoji_id: emojiId || null,
      });
      setText("");
      setMediaPath(null);
      setMediaUrl(null);
      setMedia("text");
      void qc.invalidateQueries({ queryKey: ["broadcasts"] });
      toast("✓");
    } catch (e) {
      toast(`${t.error}: ${(e as Error).message}`);
    }
  }

  // --- Telegram-like preview ------------------------------------------------
  function tgHtml(src: string): string {
    // minimal safe-ish renderer for b/i/u/s/code tags Telegram supports
    const esc = src
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
    return esc
      .replace(/&lt;(\/?)(b|strong|i|em|u|s|code|pre)&gt;/g, "<$1$2>")
      .replace(/\n/g, "<br/>");
  }

  return (
    <>
      <div className="page-head">
        <h1 className="h1">{t.broadcasts}</h1>
        <div className="actions">
          {presets.map((p) => (
            <button key={p.id} className="btn secondary sm" onClick={p.apply}>
              {p.label}
            </button>
          ))}
        </div>
      </div>

      <div className="cols" style={{ marginBottom: 14 }}>
        <div className="card main-col">
          <div className="grid" style={{ gap: 14 }}>
            <div className="row" style={{ flexWrap: "wrap" }}>
              <span className="caps">{t.audience}</span>
              <Seg
                value={audience}
                options={[
                  { id: "all" as const, label: t.all, count: a?.all },
                  { id: "active" as const, label: t.active, count: a?.active },
                  { id: "trial" as const, label: t.trial, count: a?.trial },
                  { id: "expired" as const, label: t.expired, count: a?.expired },
                ]}
                onChange={setAudience}
              />
            </div>

            <div className="row" style={{ flexWrap: "wrap" }}>
              <button
                className="btn secondary sm"
                disabled={uploading}
                onClick={() => fileRef.current?.click()}
              >
                {uploading ? "…" : "📎 " + t.addMedia}
              </button>
              <input
                ref={fileRef}
                type="file"
                accept=".jpg,.jpeg,.png,.webp,.gif,.mp4"
                style={{ display: "none" }}
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) void uploadFile(f);
                  e.target.value = "";
                }}
              />
              {mediaPath && (
                <>
                  <span className="cap-pill">{media.toUpperCase()}</span>
                  <button
                    className="btn danger sm"
                    onClick={() => {
                      setMediaPath(null);
                      setMediaUrl(null);
                      setMedia("text");
                    }}
                  >
                    ✕
                  </button>
                </>
              )}
              <span className="row" style={{ marginLeft: "auto", gap: 6 }}>
                <span className="caps">EMOJI ID</span>
                <input
                  className="input mono"
                  style={{ width: 170 }}
                  placeholder="5368324170671202286"
                  value={emojiId}
                  onChange={(e) => setEmojiId(e.target.value.trim())}
                />
              </span>
            </div>

            <textarea
              className="input"
              rows={7}
              placeholder="Текст рассылки (HTML-разметка Telegram)…"
              value={text}
              maxLength={4096}
              onChange={(e) => setText(e.target.value)}
            />

            <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap" }}>
              <label className="row" style={{ cursor: "pointer" }}>
                <Toggle on={btnOn} onChange={setBtnOn} />
                <span style={{ fontSize: 13 }}>{t.attachButton}</span>
              </label>
              <span className="mono dim" style={{ fontSize: 11 }}>
                {text.length} / 4096
              </span>
            </div>

            {btnOn && (
              <div
                className="grid"
                style={{ gap: 10, border: "1px solid var(--border)", borderRadius: 4, padding: 12 }}
              >
                <div className="row" style={{ flexWrap: "wrap" }}>
                  <input
                    className="input"
                    style={{ flex: "1 1 180px" }}
                    placeholder="Текст кнопки"
                    value={btnText}
                    maxLength={64}
                    onChange={(e) => setBtnText(e.target.value)}
                  />
                  <Seg
                    value={btnKind}
                    options={[
                      { id: "action" as const, label: t.btnAction },
                      { id: "url" as const, label: "URL" },
                    ]}
                    onChange={setBtnKind}
                  />
                </div>
                {btnKind === "url" ? (
                  <input
                    className="input mono"
                    placeholder="https://…"
                    value={btnUrl}
                    onChange={(e) => setBtnUrl(e.target.value)}
                  />
                ) : (
                  <div className="row" style={{ flexWrap: "wrap" }}>
                    <span className="caps">{t.btnAction}</span>
                    <Seg
                      value={btnAction}
                      options={[
                        { id: "buy", label: "Купить/Продлить" },
                        { id: "connect", label: "Подключиться" },
                        { id: "trial", label: "Триал" },
                        { id: "referral", label: "Рефералка" },
                        { id: "support", label: "Поддержка" },
                      ]}
                      onChange={setBtnAction}
                    />
                  </div>
                )}
                <div className="row" style={{ flexWrap: "wrap" }}>
                  <span className="caps">{t.btnColor}</span>
                  {TG_BTN_COLORS.map((c) => (
                    <button
                      key={c.id || "none"}
                      title={c.label}
                      onClick={() => setBtnColor(c.id)}
                      style={{
                        width: 26,
                        height: 26,
                        borderRadius: 4,
                        cursor: "pointer",
                        background: c.bg,
                        border:
                          btnColor === c.id ? "2px solid var(--text)" : "1px solid var(--border2)",
                      }}
                    />
                  ))}
                  <span className="dim" style={{ fontSize: 11.5 }}>
                    {t.tgColorsNote}
                  </span>
                </div>
              </div>
            )}

            <button className="btn primary" disabled={!text.trim()} onClick={send}>
              {t.send} → {targetCount.toLocaleString("ru-RU")}
            </button>
          </div>
        </div>

        {/* Telegram-like preview */}
        <div className="side-col" style={{ maxWidth: 420 }}>
          <div
            style={{
              background: "#0e1621",
              borderRadius: 10,
              padding: "18px 12px",
              minHeight: 220,
              backgroundImage:
                "radial-gradient(circle at 20% 20%, rgba(255,255,255,0.03) 0 2px, transparent 2px)",
              backgroundSize: "26px 26px",
            }}
          >
            <div className="caps" style={{ color: "#5b6b7d", marginBottom: 10 }}>
              {t.preview} · TELEGRAM
            </div>
            <div style={{ maxWidth: 320 }}>
              <div
                style={{
                  background: "#182533",
                  borderRadius: "4px 14px 14px 14px",
                  overflow: "hidden",
                  color: "#f1f5f9",
                }}
              >
                {media !== "text" &&
                  (mediaUrl && media !== "video" ? (
                    <img
                      src={mediaUrl}
                      alt=""
                      style={{ width: "100%", maxHeight: 180, objectFit: "cover", display: "block" }}
                    />
                  ) : (
                    <div
                      style={{
                        height: 140,
                        background:
                          "repeating-linear-gradient(45deg,#20303f,#20303f 8px,#182533 8px,#182533 16px)",
                        display: "grid",
                        placeItems: "center",
                        color: "#5b6b7d",
                        fontFamily: "JetBrains Mono, monospace",
                        fontSize: 11,
                      }}
                    >
                      {media.toUpperCase()}
                    </div>
                  ))}
                <div style={{ padding: "8px 12px 6px", fontSize: 13.5, lineHeight: 1.45 }}>
                  {emojiId && <span title={`custom emoji ${emojiId}`}>⭐ </span>}
                  <span dangerouslySetInnerHTML={{ __html: tgHtml(text) || "<span style='color:#5b6b7d'>…</span>" }} />
                  <div style={{ textAlign: "right", fontSize: 10.5, color: "#5b6b7d", marginTop: 3 }}>
                    {new Date().toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })}
                  </div>
                </div>
              </div>
              {btnOn && btnText && (
                <div
                  style={{
                    marginTop: 4,
                    background: btnColor || "rgba(43,59,77,0.9)",
                    color: "#fff",
                    borderRadius: 8,
                    textAlign: "center",
                    padding: "9px 0",
                    fontSize: 13.5,
                    fontWeight: 500,
                  }}
                >
                  {btnText}
                </div>
              )}
            </div>
          </div>
        </div>
      </div>

      <div className="card">
        <div className="caps" style={{ marginBottom: 12 }}>
          {t.history}
        </div>
        <div className="grid" style={{ gap: 12 }}>
          {(history.data?.items ?? []).map((b) => {
            const running = b.status === "running" || b.status === "pending";
            return (
              <div key={b.id} className="grid" style={{ gap: 6 }}>
                <div className="row" style={{ justifyContent: "space-between", fontSize: 13 }}>
                  <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    <span className="mono dim">#{b.id}</span>{" "}
                    {b.media !== "text" && <span className="cap-pill">{b.media}</span>}{" "}
                    {b.text.slice(0, 60)}
                  </span>
                  <span className="mono muted" style={{ flex: "0 0 auto" }}>
                    {running
                      ? `${t.sending} ${b.progress_pct}%`
                      : `${t.done} · ${b.sent}/${b.total} · ✕${b.failed}`}
                  </span>
                </div>
                <Prog pct={running ? b.progress_pct : 100} />
                <div className="caps">
                  {b.audience} · {dtTime(b.created_at)}
                </div>
              </div>
            );
          })}
          {history.data && history.data.items.length === 0 && <span className="dim">—</span>}
        </div>
      </div>
    </>
  );
}
