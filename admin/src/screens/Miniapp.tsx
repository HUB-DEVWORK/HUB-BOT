/* Screen 06 — Кастомизация Miniapp: LIVE previews of the real mini-app.

   Every template card and the phone on the right embed the actual app served at
   /app/ with ?mock=1&variant=…&mode=…&accent=… — what the admin sees is exactly
   what end users get. */

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api } from "../api/client";
import { Field } from "../components/ui";
import { useApp } from "../state/app";

type UiButtons = Record<string, { text: string; color: string | null }>;
type UiConf = { scale?: number; sections?: string[]; buttons?: UiButtons };
type Config = {
  template: string;
  title: string | null;
  greeting: string | null;
  accent_color: string | null;
  photo_scale_pct: number;
  ui: UiConf;
  published_at: string | null;
  templates: string[];
  ui_button_keys: string[];
  ui_sections: string[];
};

const BTN_LABELS: Record<string, string> = {
  renew: "Продлить / Купить",
  share: "Поделиться (рефералка)",
  open_app: "Открыть в приложении",
  get_link: "Получить ссылку",
  connect_proxy: "Подключить прокси",
  trial: "Попробовать бесплатно",
};
const SECTION_LABELS: Record<string, string> = {
  status: "Статус подписки",
  plans: "Тарифы и оплата",
  referral: "Пригласи друга",
  proxy: "MTProto-прокси",
};

const SWATCHES = ["#2E63E7", "#31A24C", "#7C5CFF", "#EF7048", "#C03B2D", "#0C8F4E", "#111111"];

const TEMPLATE_NAMES: Record<string, string> = {
  minimal: "Минимал",
  private: "Прайват",
  buddy: "Бадди",
  native: "Нативный",
  terminal: "Терминал",
  magazine: "Журнал",
  neon: "Неон",
  pop: "Поп",
};

/** Live mini-app preview: real /app/ in an iframe, scaled down, non-interactive. */
function LivePreview({
  variant,
  mode,
  accent,
  ui,
  width,
  height,
  interactive = false,
}: {
  variant: string;
  mode: "dark" | "light";
  accent?: string | null;
  ui?: UiConf;
  width: number;
  height: number;
  interactive?: boolean;
}) {
  const scale = width / 375;
  const src =
    `/app/?mock=1&variant=${encodeURIComponent(variant)}&mode=${mode}` +
    (accent ? `&accent=${encodeURIComponent(accent)}` : "") +
    (ui ? `&ui=${encodeURIComponent(btoa(unescape(encodeURIComponent(JSON.stringify(ui)))))}` : "");
  return (
    <div
      style={{
        width,
        height,
        overflow: "hidden",
        borderRadius: 10,
        position: "relative",
        pointerEvents: interactive ? "auto" : "none",
        background: mode === "dark" ? "#111" : "#f5f5f5",
      }}
    >
      <iframe
        src={src}
        title={`preview-${variant}`}
        loading="lazy"
        style={{
          width: 375,
          height: height / scale,
          border: 0,
          transform: `scale(${scale})`,
          transformOrigin: "top left",
        }}
      />
    </div>
  );
}

export default function Miniapp() {
  const { t, theme, toast } = useApp();
  const qc = useQueryClient();
  const [cfg, setCfg] = useState<Config | null>(null);
  const [dirty, setDirty] = useState(false);

  const data = useQuery({
    queryKey: ["miniapp"],
    queryFn: () => api.get<Config>("/api/admin/miniapp"),
  });

  useEffect(() => {
    if (data.data && !dirty) setCfg(data.data);
  }, [data.data, dirty]);

  function patch(p: Partial<Config>) {
    setCfg((c) => (c ? { ...c, ...p } : c));
    setDirty(true);
  }

  function patchUi(p: Partial<UiConf>) {
    setCfg((c) => (c ? { ...c, ui: { ...c.ui, ...p } } : c));
    setDirty(true);
  }

  function patchBtn(key: string, field: "text" | "color", value: string | null) {
    setCfg((c) => {
      if (!c) return c;
      const buttons: UiButtons = { ...(c.ui.buttons ?? {}) };
      const prev = buttons[key] ?? { text: "", color: null };
      buttons[key] = { ...prev, [field]: value };
      return { ...c, ui: { ...c.ui, buttons } };
    });
    setDirty(true);
  }

  function moveSection(idx: number, dir: -1 | 1) {
    setCfg((c) => {
      if (!c) return c;
      const order = [...(c.ui.sections?.length ? c.ui.sections : c.ui_sections)];
      const j = idx + dir;
      if (j < 0 || j >= order.length) return c;
      [order[idx], order[j]] = [order[j], order[idx]];
      return { ...c, ui: { ...c.ui, sections: order } };
    });
    setDirty(true);
  }

  async function save(publish = false) {
    if (!cfg) return;
    try {
      await api.patch("/api/admin/miniapp", {
        template: cfg.template,
        title: cfg.title,
        greeting: cfg.greeting,
        accent_color: cfg.accent_color,
        photo_scale_pct: cfg.photo_scale_pct,
        ui: cfg.ui,
      });
      if (publish) await api.post("/api/admin/miniapp/publish");
      setDirty(false);
      void qc.invalidateQueries({ queryKey: ["miniapp"] });
      toast(publish ? t.published : t.saved);
    } catch (e) {
      toast(`${t.error}: ${(e as Error).message}`);
    }
  }

  const mode = theme === "dark" ? "dark" : "light";
  const accent = cfg?.accent_color ?? null;

  return (
    <>
      <div className="page-head">
        <h1 className="h1">{t.miniapp}</h1>
        <div className="actions">
          <button className="btn primary" onClick={() => void save(true)}>
            {t.publish}
          </button>
        </div>
      </div>

      {/* template cards: live scaled previews of the real app */}
      <div
        className="kpis"
        style={{ gridTemplateColumns: "repeat(auto-fill, minmax(150px, 1fr))", marginBottom: 20 }}
      >
        {(cfg?.templates ?? []).map((id) => (
          <button
            key={id}
            onClick={() => patch({ template: id })}
            style={{
              background: "var(--panel)",
              border: cfg?.template === id ? "2px solid var(--text)" : "1px solid var(--border)",
              borderRadius: 4,
              padding: 8,
              cursor: "pointer",
              textAlign: "left",
              color: "var(--text)",
            }}
          >
            {/* cards show each theme's NATIVE accent; the admin accent applies in the phone */}
            <LivePreview variant={id} mode={mode} width={134} height={168} />
            <div style={{ fontSize: 12.5, marginTop: 8, fontWeight: 500 }}>
              {TEMPLATE_NAMES[id] ?? id}
            </div>
            <div className="mono dim" style={{ fontSize: 9.5 }}>
              {id}
            </div>
          </button>
        ))}
      </div>

      <div className="cols">
        {/* settings */}
        <div className="card main-col" style={{ maxWidth: 520 }}>
          {cfg && (
            <div className="grid" style={{ gap: 14 }}>
              <Field label={t.appTitle}>
                <input
                  className="input"
                  value={cfg.title ?? ""}
                  placeholder="My VPN"
                  onChange={(e) => patch({ title: e.target.value })}
                />
              </Field>
              <Field label={t.greeting}>
                <input
                  className="input"
                  value={cfg.greeting ?? ""}
                  placeholder="Привет! Твоя защита активна"
                  onChange={(e) => patch({ greeting: e.target.value })}
                />
              </Field>
              <Field label={t.accent}>
                <div className="row" style={{ flexWrap: "wrap" }}>
                  {SWATCHES.map((c) => (
                    <button
                      key={c}
                      onClick={() => patch({ accent_color: c })}
                      style={{
                        width: 24,
                        height: 24,
                        borderRadius: 3,
                        cursor: "pointer",
                        background: c,
                        border:
                          accent === c ? "2px solid var(--text)" : "1px solid var(--border2)",
                      }}
                    />
                  ))}
                  <button
                    title="сброс — цвет темы"
                    onClick={() => patch({ accent_color: null })}
                    style={{
                      width: 24,
                      height: 24,
                      borderRadius: 3,
                      cursor: "pointer",
                      background: "transparent",
                      border:
                        accent === null ? "2px solid var(--text)" : "1px solid var(--border2)",
                    }}
                  >
                    ✕
                  </button>
                  <input
                    className="input mono"
                    style={{ width: 100 }}
                    value={cfg.accent_color ?? ""}
                    placeholder="#HEX"
                    onChange={(e) => patch({ accent_color: e.target.value || null })}
                  />
                </div>
              </Field>
              <Field label={`${t.photoScale} · ${cfg.photo_scale_pct}%`}>
                <input
                  type="range"
                  min={70}
                  max={130}
                  value={cfg.photo_scale_pct}
                  onChange={(e) => patch({ photo_scale_pct: Number(e.target.value) })}
                />
              </Field>
              <Field label={`${t.uiScale} · ${cfg.ui.scale ?? 100}%`}>
                <input
                  type="range"
                  min={85}
                  max={115}
                  value={cfg.ui.scale ?? 100}
                  onChange={(e) => patchUi({ scale: Number(e.target.value) })}
                />
              </Field>
              <div>
                <div className="caps" style={{ marginBottom: 8 }}>{t.sectionsOrder}</div>
                <div className="grid" style={{ gap: 6 }}>
                  {(cfg.ui.sections?.length ? cfg.ui.sections : cfg.ui_sections).map((sec, i, arr) => (
                    <div key={sec} className="row" style={{ fontSize: 13 }}>
                      <span className="mono dim" style={{ width: 18 }}>{i + 1}</span>
                      <span style={{ flex: 1 }}>{SECTION_LABELS[sec] ?? sec}</span>
                      <button className="btn secondary sm" disabled={i === 0} onClick={() => moveSection(i, -1)}>↑</button>
                      <button className="btn secondary sm" disabled={i === arr.length - 1} onClick={() => moveSection(i, 1)}>↓</button>
                    </div>
                  ))}
                </div>
              </div>
              <div>
                <div className="caps" style={{ marginBottom: 8 }}>{t.buttonsCustom}</div>
                <div className="grid" style={{ gap: 8 }}>
                  {cfg.ui_button_keys.map((key) => {
                    const b = cfg.ui.buttons?.[key];
                    return (
                      <div key={key} className="row" style={{ flexWrap: "wrap" }}>
                        <span className="dim" style={{ width: 168, fontSize: 12 }}>
                          {BTN_LABELS[key] ?? key}
                        </span>
                        <input
                          className="input"
                          style={{ flex: "1 1 140px" }}
                          placeholder="текст по умолчанию"
                          value={b?.text ?? ""}
                          maxLength={32}
                          onChange={(e) => patchBtn(key, "text", e.target.value)}
                        />
                        <input
                          type="color"
                          value={b?.color ?? "#2E63E7"}
                          title="цвет кнопки"
                          style={{ width: 34, height: 30, padding: 0, border: "1px solid var(--border2)", borderRadius: 3, background: "transparent", cursor: "pointer" }}
                          onChange={(e) => patchBtn(key, "color", e.target.value)}
                        />
                        {b?.color && (
                          <button className="btn danger sm" title="сбросить цвет" onClick={() => patchBtn(key, "color", null)}>✕</button>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>
              {dirty && (
                <button className="btn primary" onClick={() => void save(false)}>
                  {t.save}
                </button>
              )}
              <div className="caps">
                LIVE · {t.preview} = /app/?variant={cfg.template} · mock
              </div>
            </div>
          )}
        </div>

        {/* interactive phone preview: the real app, full size */}
        <div className="side-col" style={{ alignItems: "center" }}>
          {cfg && (
            <div
              style={{
                width: 316,
                border: "1px solid var(--border2)",
                borderRadius: 28,
                padding: 8,
                background: "var(--panel)",
                margin: "0 auto",
              }}
            >
              <LivePreview
                key={`${cfg.template}-${mode}-${accent ?? "auto"}-${JSON.stringify(cfg.ui)}`}
                variant={cfg.template}
                mode={mode}
                accent={accent}
                ui={cfg.ui}
                width={300}
                height={620}
                interactive
              />
            </div>
          )}
          <div className="caps" style={{ textAlign: "center" }}>
            {t.preview} · {TEMPLATE_NAMES[cfg?.template ?? ""] ?? cfg?.template} ·{" "}
            {mode.toUpperCase()}
          </div>
        </div>
      </div>
    </>
  );
}
