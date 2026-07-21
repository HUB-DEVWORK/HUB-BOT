/* Screen 13 — Настройки: category blocks first, drill-in per block, global search.

   Block view keeps non-tech admins oriented; search flattens across categories. */

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { api } from "../api/client";
import { Toggle } from "../components/ui";
import { useApp } from "../state/app";

type Param = {
  key: string;
  category: string;
  type: "bool" | "int" | "str" | "secret";
  name: string;
  description: string;
  value: unknown;
  is_overridden: boolean;
};
type Resp = { categories: { id: string; name: string }[]; params: Param[]; total: number };

const CAT_META: Record<string, { icon: string; ru: string; en: string }> = {
  main: { icon: "⚙️", ru: "Приветствие, язык, техработы, админы", en: "Greeting, language, maintenance, admins" },
  subs: { icon: "📦", ru: "Триал, автопродление, лимиты устройств", en: "Trial, auto-renewal, device limits" },
  pay: { icon: "💳", ru: "Пополнения, налог, чеки, курс Stars", en: "Deposits, tax, receipts, Stars rate" },
  notif: { icon: "🔔", ru: "Алерты, отчёты, напоминания", en: "Alerts, reports, reminders" },
  ref: { icon: "🎁", ru: "Бонусы и проценты за приглашения", en: "Referral bonuses and percents" },
  sec: { icon: "🛡️", ru: "Чёрный список, обязательный канал, HWID", en: "Blacklist, required channel, HWID" },
  backup: { icon: "💾", ru: "Расписание и хранение бэкапов", en: "Backup schedule and retention" },
  ui: { icon: "🤖", ru: "Кнопки бота, прокси, поддержка", en: "Bot buttons, proxy, support" },
  support: { icon: "💬", ru: "ИИ-поддержка: модель и база знаний", en: "AI support: model and knowledge base" },
};

export default function Settings() {
  const { t, lang, toast } = useApp();
  const qc = useQueryClient();
  const [q, setQ] = useState(() => {
    const handoff = sessionStorage.getItem("settings_q") ?? "";
    sessionStorage.removeItem("settings_q");
    return handoff;
  });
  const [cat, setCat] = useState<string | null>(null);
  const [dirty, setDirty] = useState<Record<string, unknown>>({});

  const data = useQuery({
    queryKey: ["settings", lang],
    queryFn: () => api.get<Resp>(`/api/admin/settings?lang=${lang}`),
  });

  const all = data.data?.params ?? [];
  const searching = q.trim().length > 0;

  const filtered = useMemo(() => {
    if (!searching) return all;
    const needle = q.toLowerCase();
    return all.filter(
      (p) =>
        p.key.toLowerCase().includes(needle) ||
        p.name.toLowerCase().includes(needle) ||
        p.description.toLowerCase().includes(needle),
    );
  }, [all, q, searching]);

  const visible = useMemo(() => {
    if (searching) return filtered;
    if (cat) return all.filter((p) => p.category === cat);
    return [];
  }, [searching, filtered, cat, all]);

  function valueOf(p: Param): unknown {
    return p.key in dirty ? dirty[p.key] : p.value;
  }
  function setValue(key: string, v: unknown) {
    setDirty((d) => ({ ...d, [key]: v }));
  }

  async function save() {
    try {
      await api.patch("/api/admin/settings", { changes: dirty });
      setDirty({});
      void qc.invalidateQueries({ queryKey: ["settings"] });
      toast(t.applied);
    } catch (e) {
      toast((e as Error).message);
    }
  }

  const dirtyCount = Object.keys(dirty).length;
  const cats = data.data?.categories ?? [];
  const currentCat = cats.find((c) => c.id === cat);

  function renderRow(p: Param) {
    const v = valueOf(p);
    const changed = p.key in dirty;
    return (
      <div key={p.key} className="tr" style={{ gridTemplateColumns: "1fr auto" }}>
        <div style={{ minWidth: 0 }}>
          <div className="row" style={{ gap: 7 }}>
            <b style={{ fontSize: 13.5, fontWeight: 500 }}>{p.name}</b>
            {(changed || p.is_overridden) && (
              <span
                title={changed ? t.changed : "override"}
                style={{
                  width: 6,
                  height: 6,
                  borderRadius: "50%",
                  background: changed ? "var(--text)" : "var(--dim)",
                  flex: "0 0 auto",
                }}
              />
            )}
          </div>
          {p.description && (
            <div className="muted" style={{ fontSize: 12, marginTop: 1 }}>
              {p.description}
            </div>
          )}
          <div className="mono dim" style={{ fontSize: 10.5, marginTop: 3 }}>
            {p.key}
          </div>
        </div>
        <div className="row">
          {p.type === "bool" ? (
            <Toggle on={Boolean(v)} onChange={(nv) => setValue(p.key, nv)} />
          ) : (
            <input
              className={`input ${p.type === "int" ? "num" : "mono"}`}
              style={{ width: p.type === "int" ? 110 : 220 }}
              type={p.type === "secret" ? "password" : "text"}
              value={String(v ?? "")}
              onChange={(e) =>
                setValue(p.key, p.type === "int" ? Number(e.target.value) || 0 : e.target.value)
              }
            />
          )}
        </div>
      </div>
    );
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1 className="h1">
            {cat && !searching ? (
              <span className="row" style={{ gap: 10 }}>
                <button className="btn secondary sm" onClick={() => setCat(null)}>
                  ‹
                </button>
                {CAT_META[cat]?.icon} {currentCat?.name}
              </span>
            ) : (
              t.settings
            )}
          </h1>
          {cat && !searching && (
            <div className="caps sub">{lang === "ru" ? CAT_META[cat]?.ru : CAT_META[cat]?.en}</div>
          )}
        </div>
        <div className="actions">
          {dirtyCount > 0 && (
            <>
              <span className="cap-pill">
                {t.changed}: {dirtyCount}
              </span>
              <button className="btn secondary" onClick={() => setDirty({})}>
                {t.cancel}
              </button>
              <button className="btn primary" onClick={save}>
                {t.saveApply}
              </button>
            </>
          )}
        </div>
      </div>

      <div className="row" style={{ marginBottom: 16 }}>
        <div style={{ position: "relative", flex: 1 }}>
          <span
            className="dim"
            style={{ position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)" }}
          >
            ⌕
          </span>
          <input
            className="input"
            style={{ width: "100%", paddingLeft: 30, paddingRight: 30 }}
            placeholder={t.searchParams}
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
          {q && (
            <button
              style={{
                position: "absolute",
                right: 8,
                top: "50%",
                transform: "translateY(-50%)",
                background: "none",
                border: 0,
                cursor: "pointer",
                color: "var(--dim)",
              }}
              onClick={() => setQ("")}
            >
              ✕
            </button>
          )}
        </div>
        <span className="caps" style={{ whiteSpace: "nowrap" }}>
          {searching ? `${t.found} ${filtered.length}` : `${data.data?.total ?? "…"} ${t.paramsHot}`}
        </span>
      </div>

      {/* category blocks */}
      {!searching && !cat && (
        <div
          className="kpis"
          style={{ gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))" }}
        >
          {cats.map((c) => {
            const params = all.filter((p) => p.category === c.id);
            const overridden = params.filter((p) => p.is_overridden || p.key in dirty).length;
            return (
              <button
                key={c.id}
                onClick={() => setCat(c.id)}
                style={{
                  background: "var(--panel)",
                  border: "1px solid var(--border)",
                  borderRadius: 4,
                  padding: 18,
                  cursor: "pointer",
                  textAlign: "left",
                  color: "var(--text)",
                }}
              >
                <div className="row" style={{ justifyContent: "space-between" }}>
                  <span style={{ fontSize: 22 }}>{CAT_META[c.id]?.icon ?? "⚙️"}</span>
                  <span className="cap-pill">{params.length}</span>
                </div>
                <div style={{ fontSize: 15, fontWeight: 600, margin: "10px 0 4px" }}>{c.name}</div>
                <div className="muted" style={{ fontSize: 12.5, minHeight: 34 }}>
                  {lang === "ru" ? CAT_META[c.id]?.ru : CAT_META[c.id]?.en}
                </div>
                <div className="caps" style={{ marginTop: 8 }}>
                  {overridden > 0 ? `● ${t.changed}: ${overridden}` : "—"}
                </div>
              </button>
            );
          })}
        </div>
      )}

      {/* params of the selected block, or flat search results */}
      {(searching || cat) && (
        <div className="tbl">
          {searching
            ? cats
                .map((c) => ({ c, list: visible.filter((p) => p.category === c.id) }))
                .filter((g) => g.list.length > 0)
                .map((g) => (
                  <div key={g.c.id}>
                    <div
                      className="tr"
                      style={{ background: "var(--panel2)", gridTemplateColumns: "1fr auto" }}
                    >
                      <span className="caps">
                        {CAT_META[g.c.id]?.icon} {g.c.name}
                      </span>
                      <span className="caps">{g.list.length}</span>
                    </div>
                    {g.list.map(renderRow)}
                  </div>
                ))
            : visible.map(renderRow)}
          {(searching || cat) && visible.length === 0 && <div className="tr dim">—</div>}
        </div>
      )}

      <div className="caps" style={{ marginTop: 14, textAlign: "center" }}>
        {t.hotReloadNote}
      </div>
    </>
  );
}
