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
type UiBlock = {
  id: string;
  screen: string;
  title: string;
  text: string;
  icon: string;
  url: string | null;
  button_label: string;
  color: string | null;
};
type UiButtonExtra = {
  id: string;
  screen: string;
  label: string;
  url: string;
  color: string | null;
  style: string;
};
type UiConf = {
  scale?: number;
  sections?: string[];
  hidden?: string[];
  buttons?: UiButtons;
  blocks?: UiBlock[];
  buttons_extra?: UiButtonExtra[];
};
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
  ui_screens: string[];
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
  custom: "Свои блоки и кнопки",
};
const SCREEN_LABELS: Record<string, string> = {
  home: "Главная",
  connect: "Подключение",
  account: "Кабинет",
};

let _cid = 0;
const newId = (p: string) => `${p}${Date.now().toString(36)}${(_cid++).toString(36)}`;

/** Effective section order: the admin's saved order, with any not-yet-ordered
 *  built-ins (e.g. "custom") appended — so every section is always listed. */
function sectionOrder(c: Config): string[] {
  const saved = c.ui.sections?.length ? c.ui.sections : c.ui_sections;
  const rest = c.ui_sections.filter((s) => !saved.includes(s));
  return [...saved, ...rest];
}

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
      const order = [...sectionOrder(c)];
      const j = idx + dir;
      if (j < 0 || j >= order.length) return c;
      [order[idx], order[j]] = [order[j], order[idx]];
      return { ...c, ui: { ...c.ui, sections: order } };
    });
    setDirty(true);
  }

  function toggleHidden(sec: string) {
    setCfg((c) => {
      if (!c) return c;
      const hidden = new Set(c.ui.hidden ?? []);
      hidden.has(sec) ? hidden.delete(sec) : hidden.add(sec);
      return { ...c, ui: { ...c.ui, hidden: [...hidden] } };
    });
    setDirty(true);
  }

  function addBlock() {
    patchUi({
      blocks: [
        ...(cfg?.ui.blocks ?? []),
        { id: newId("b"), screen: "home", title: "", text: "", icon: "", url: null, button_label: "", color: null },
      ],
    });
  }
  function patchBlock(i: number, field: keyof UiBlock, value: string | null) {
    setCfg((c) => {
      if (!c) return c;
      const blocks = [...(c.ui.blocks ?? [])];
      blocks[i] = { ...blocks[i], [field]: value };
      return { ...c, ui: { ...c.ui, blocks } };
    });
    setDirty(true);
  }
  function removeBlock(i: number) {
    patchUi({ blocks: (cfg?.ui.blocks ?? []).filter((_, j) => j !== i) });
  }

  function addButton() {
    patchUi({
      buttons_extra: [
        ...(cfg?.ui.buttons_extra ?? []),
        { id: newId("x"), screen: "home", label: "", url: "", color: null, style: "primary" },
      ],
    });
  }
  function patchButtonExtra(i: number, field: keyof UiButtonExtra, value: string | null) {
    setCfg((c) => {
      if (!c) return c;
      const arr = [...(c.ui.buttons_extra ?? [])];
      arr[i] = { ...arr[i], [field]: value };
      return { ...c, ui: { ...c.ui, buttons_extra: arr } };
    });
    setDirty(true);
  }
  function removeButtonExtra(i: number) {
    patchUi({ buttons_extra: (cfg?.ui.buttons_extra ?? []).filter((_, j) => j !== i) });
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
      toast((e as Error).message);
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
                <div className="caps" style={{ marginBottom: 8 }}>
                  {t.sectionsOrder} · {t.visibility}
                </div>
                <div className="grid" style={{ gap: 6 }}>
                  {sectionOrder(cfg).map((sec, i, arr) => {
                    const hidden = (cfg.ui.hidden ?? []).includes(sec);
                    return (
                      <div key={sec} className="row" style={{ fontSize: 13, opacity: hidden ? 0.5 : 1 }}>
                        <span className="mono dim" style={{ width: 18 }}>{i + 1}</span>
                        <span style={{ flex: 1 }}>{SECTION_LABELS[sec] ?? sec}</span>
                        <button
                          className="btn secondary sm"
                          title={hidden ? t.show : t.hide}
                          onClick={() => toggleHidden(sec)}
                        >
                          {hidden ? "🚫" : "👁"}
                        </button>
                        <button className="btn secondary sm" disabled={i === 0} onClick={() => moveSection(i, -1)}>↑</button>
                        <button className="btn secondary sm" disabled={i === arr.length - 1} onClick={() => moveSection(i, 1)}>↓</button>
                      </div>
                    );
                  })}
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

              {/* custom content blocks: title + text + optional link-button, per screen */}
              <div>
                <div className="row spread" style={{ marginBottom: 8 }}>
                  <span className="caps">{t.customBlocks}</span>
                  <button className="btn secondary sm" onClick={addBlock}>+ {t.add}</button>
                </div>
                <div className="grid" style={{ gap: 10 }}>
                  {(cfg.ui.blocks ?? []).map((b, i) => (
                    <div key={b.id} className="card" style={{ padding: 10, gap: 6, display: "grid", background: "var(--panel2, var(--panel))" }}>
                      <div className="row" style={{ gap: 6 }}>
                        <select
                          className="input"
                          style={{ width: 130 }}
                          value={b.screen}
                          onChange={(e) => patchBlock(i, "screen", e.target.value)}
                        >
                          {cfg.ui_screens.map((s) => (
                            <option key={s} value={s}>{SCREEN_LABELS[s] ?? s}</option>
                          ))}
                        </select>
                        <input
                          className="input"
                          style={{ width: 52, textAlign: "center" }}
                          placeholder="😀"
                          value={b.icon}
                          maxLength={8}
                          onChange={(e) => patchBlock(i, "icon", e.target.value)}
                        />
                        <input
                          className="input"
                          style={{ flex: 1 }}
                          placeholder={t.blockTitle}
                          value={b.title}
                          maxLength={64}
                          onChange={(e) => patchBlock(i, "title", e.target.value)}
                        />
                        <button className="btn danger sm" title={t.remove} onClick={() => removeBlock(i)}>✕</button>
                      </div>
                      <textarea
                        className="input"
                        style={{ minHeight: 46, resize: "vertical" }}
                        placeholder={t.blockText}
                        value={b.text}
                        maxLength={1000}
                        onChange={(e) => patchBlock(i, "text", e.target.value)}
                      />
                      <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
                        <input
                          className="input"
                          style={{ flex: "1 1 120px" }}
                          placeholder={t.buttonLabel}
                          value={b.button_label}
                          maxLength={32}
                          onChange={(e) => patchBlock(i, "button_label", e.target.value)}
                        />
                        <input
                          className="input mono"
                          style={{ flex: "2 1 180px" }}
                          placeholder="https://… / tg://…"
                          value={b.url ?? ""}
                          onChange={(e) => patchBlock(i, "url", e.target.value || null)}
                        />
                        <input
                          type="color"
                          value={b.color ?? "#2E63E7"}
                          title={t.buttonColor}
                          style={{ width: 34, height: 30, padding: 0, border: "1px solid var(--border2)", borderRadius: 3, background: "transparent", cursor: "pointer" }}
                          onChange={(e) => patchBlock(i, "color", e.target.value)}
                        />
                      </div>
                    </div>
                  ))}
                  {!(cfg.ui.blocks ?? []).length && (
                    <div className="dim" style={{ fontSize: 12 }}>{t.customBlocksHint}</div>
                  )}
                </div>
              </div>

              {/* custom link buttons: label + url, per screen */}
              <div>
                <div className="row spread" style={{ marginBottom: 8 }}>
                  <span className="caps">{t.customButtons}</span>
                  <button className="btn secondary sm" onClick={addButton}>+ {t.add}</button>
                </div>
                <div className="grid" style={{ gap: 8 }}>
                  {(cfg.ui.buttons_extra ?? []).map((x, i) => (
                    <div key={x.id} className="row" style={{ gap: 6, flexWrap: "wrap" }}>
                      <select
                        className="input"
                        style={{ width: 120 }}
                        value={x.screen}
                        onChange={(e) => patchButtonExtra(i, "screen", e.target.value)}
                      >
                        {cfg.ui_screens.map((s) => (
                          <option key={s} value={s}>{SCREEN_LABELS[s] ?? s}</option>
                        ))}
                      </select>
                      <input
                        className="input"
                        style={{ flex: "1 1 110px" }}
                        placeholder={t.buttonLabel}
                        value={x.label}
                        maxLength={32}
                        onChange={(e) => patchButtonExtra(i, "label", e.target.value)}
                      />
                      <input
                        className="input mono"
                        style={{ flex: "2 1 160px" }}
                        placeholder="https://… / tg://…"
                        value={x.url}
                        onChange={(e) => patchButtonExtra(i, "url", e.target.value)}
                      />
                      <select
                        className="input"
                        style={{ width: 92 }}
                        value={x.style}
                        onChange={(e) => patchButtonExtra(i, "style", e.target.value)}
                      >
                        <option value="primary">{t.stylePrimary}</option>
                        <option value="ghost">{t.styleGhost}</option>
                      </select>
                      <input
                        type="color"
                        value={x.color ?? "#2E63E7"}
                        title={t.buttonColor}
                        style={{ width: 34, height: 30, padding: 0, border: "1px solid var(--border2)", borderRadius: 3, background: "transparent", cursor: "pointer" }}
                        onChange={(e) => patchButtonExtra(i, "color", e.target.value)}
                      />
                      <button className="btn danger sm" title={t.remove} onClick={() => removeButtonExtra(i)}>✕</button>
                    </div>
                  ))}
                  {!(cfg.ui.buttons_extra ?? []).length && (
                    <div className="dim" style={{ fontSize: 12 }}>{t.customButtonsHint}</div>
                  )}
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
