/* Screen 10 — Платежи: transactions + live net-profit math; providers config. */

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, dtTime, money } from "../api/client";
import { Kpi, Seg, Toggle } from "../components/ui";
import { useApp } from "../state/app";

type Stats = {
  turnover_minor: number;
  fees_minor: number;
  tax_percent: number;
  tax_minor: number;
  net_profit_minor: number;
  providers: { gateway: string; amount_minor: number; count: number; fee_minor: number }[];
};
type Tx = {
  id: number;
  tx: string;
  user: string;
  type: string;
  amount_minor: number;
  gateway: string | null;
  status: string;
  created_at: string | null;
};
type Provider = {
  id: number | null;
  type: string;
  title: string;
  emoji: string;
  methods: string;
  fields: string[];
  ready: boolean;
  display_name: string;
  is_active: boolean;
  fee_bp: number;
  configured_keys: string[];
  forms: string[];
  enabled_forms: string[];
  brand: string;
};

const FORM_LABELS: Record<string, string> = {
  card: "Карта",
  sbp: "СБП",
  crypto: "Крипта",
  stars: "Stars",
  wallet: "Кошельки",
  balance: "Баланс",
};

function Monogram({ title, brand }: { title: string; brand: string }) {
  return (
    <span
      style={{
        width: 30,
        height: 30,
        borderRadius: 7,
        background: brand,
        color: "#fff",
        display: "grid",
        placeItems: "center",
        fontFamily: "'Arial Black',Arial,sans-serif",
        fontWeight: 900,
        fontSize: 13,
        flex: "0 0 auto",
      }}
    >
      {title.replace(/[^A-Za-zА-Яа-я0-9]/g, "").slice(0, 1).toUpperCase()}
    </span>
  );
}

const ST_GLYPH: Record<string, string> = {
  completed: "✓",
  pending: "◌",
  failed: "✕",
  canceled: "✕",
  refunded: "↩",
};

export default function Payments() {
  const { t, toast } = useApp();
  const qc = useQueryClient();
  const [tab, setTab] = useState<"tx" | "providers">("tx");
  const [filter, setFilter] = useState<"all" | "ok" | "pending" | "failed" | "refund">("all");
  const [tax, setTax] = useState<number | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [draft, setDraft] = useState<Record<string, string>>({});

  const stats = useQuery({
    queryKey: ["pay-stats", tax],
    queryFn: () => api.get<Stats>(`/api/admin/payments/stats${tax !== null ? `?tax=${tax}` : ""}`),
  });
  const txs = useQuery({
    queryKey: ["payments", filter],
    queryFn: () => api.get<{ items: Tx[] }>(`/api/admin/payments?status=${filter}&limit=50`),
  });
  const providers = useQuery({
    queryKey: ["providers"],
    queryFn: () => api.get<{ items: Provider[] }>("/api/admin/providers"),
  });

  const s = stats.data;

  async function saveProvider(type: string, patch: Record<string, unknown>) {
    try {
      await api.post("/api/admin/providers", { type, ...patch });
      void qc.invalidateQueries({ queryKey: ["providers"] });
      toast(t.saved);
    } catch (e) {
      toast((e as Error).message);
    }
  }

  async function testProvider(type: string) {
    try {
      const r = await api.post<{ ok: boolean; detail: string }>(
        `/api/admin/providers/${type}/test`,
      );
      toast(`${type}: ${r.ok ? "OK" : "✕"} · ${r.detail}`);
    } catch (e) {
      toast((e as Error).message);
    }
  }

  const allProviders: Provider[] = providers.data?.items ?? [];

  return (
    <>
      <div className="page-head">
        <h1 className="h1">{t.payments}</h1>
        <div className="actions">
          <button className="btn secondary" onClick={() => toast(t.exportCsv + " ✓")}>
            {t.exportCsv}
          </button>
        </div>
      </div>

      <div style={{ marginBottom: 16 }}>
        <Seg
          value={tab}
          options={[
            { id: "tx" as const, label: t.transactions },
            { id: "providers" as const, label: t.providers },
          ]}
          onChange={setTab}
        />
      </div>

      {tab === "tx" && (
        <>
          <div className="kpis">
            <Kpi label={t.turnoverToday} value={s ? money(s.turnover_minor) : "…"} />
            <Kpi label={t.fees} value={s ? `− ${money(s.fees_minor)}` : "…"} />
            <div className="kpi">
              <div className="caps">{t.tax}</div>
              <div className="row" style={{ margin: "6px 0 4px" }}>
                <input
                  className="input num"
                  style={{ width: 70 }}
                  type="number"
                  min={0}
                  max={100}
                  value={tax ?? s?.tax_percent ?? 6}
                  onChange={(e) => setTax(Number(e.target.value) || 0)}
                />
                <span className="mono" style={{ fontSize: 20 }}>
                  % · − {s ? money(s.tax_minor) : "…"}
                </span>
              </div>
            </div>
            <Kpi label={t.netProfit} value={s ? money(s.net_profit_minor) : "…"} outlined />
          </div>

          <div className="cols">
            <div className="main-col">
              <div className="row" style={{ marginBottom: 4 }}>
                <Seg
                  value={filter}
                  options={[
                    { id: "all" as const, label: t.all },
                    { id: "ok" as const, label: t.success },
                    { id: "pending" as const, label: t.processing },
                    { id: "failed" as const, label: t.errors },
                    { id: "refund" as const, label: t.refunds },
                  ]}
                  onChange={setFilter}
                />
              </div>
              <div className="tbl">
                <div className="tr head" style={{ gridTemplateColumns: "1fr 1.3fr 1fr 1fr 0.6fr 1fr" }}>
                  <span>TX</span>
                  <span>{t.colUser}</span>
                  <span>Тип</span>
                  <span>Сумма</span>
                  <span>ST</span>
                  <span>Время</span>
                </div>
                {(txs.data?.items ?? []).map((x) => (
                  <div key={x.id} className="tr" style={{ gridTemplateColumns: "1fr 1.3fr 1fr 1fr 0.6fr 1fr" }}>
                    <span className="mono dim">TX-{x.tx}</span>
                    <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{x.user}</span>
                    <span className="muted">{x.type}</span>
                    <span className="mono">{money(x.amount_minor)}</span>
                    <span className="mono">{ST_GLYPH[x.status] ?? "?"}</span>
                    <span className="dim" style={{ fontSize: 12 }}>
                      {dtTime(x.created_at)}
                    </span>
                  </div>
                ))}
                {txs.data && txs.data.items.length === 0 && <div className="tr dim">—</div>}
              </div>
            </div>
            <div className="side-col">
              <div className="card">
                <div className="caps" style={{ marginBottom: 12 }}>
                  {t.providerShares}
                </div>
                <div className="grid" style={{ gap: 10 }}>
                  {(s?.providers ?? []).map((p) => (
                    <div key={p.gateway}>
                      <div className="row" style={{ justifyContent: "space-between", fontSize: 12 }}>
                        <span className="muted">{p.gateway}</span>
                        <span className="mono">{money(p.amount_minor)}</span>
                      </div>
                      <div className="prog" style={{ marginTop: 4 }}>
                        <i
                          style={{
                            width: `${s && s.turnover_minor ? (p.amount_minor / s.turnover_minor) * 100 : 0}%`,
                          }}
                        />
                      </div>
                    </div>
                  ))}
                  {s && s.providers.length === 0 && <span className="dim">—</span>}
                </div>
              </div>
            </div>
          </div>
        </>
      )}

      {tab === "providers" && (
        <div className="tbl">
          {allProviders.map((p) => (
            <div key={p.type}>
              <div
                className="tr click"
                style={{ gridTemplateColumns: "auto auto 1.5fr 1.2fr 1fr auto auto" }}
                onClick={() => setExpanded(expanded === p.type ? null : p.type)}
              >
                <span className={`st ${p.is_active ? "on" : "off"}`}>
                  {p.is_active ? "●" : "○"}
                </span>
                <Monogram title={p.title} brand={p.brand} />
                <span style={{ minWidth: 0 }}>
                  <b style={{ fontWeight: 600 }}>{p.display_name}</b>
                  <div className="row" style={{ gap: 4, marginTop: 3, flexWrap: "wrap" }}>
                    {p.forms.map((f) => (
                      <span
                        key={f}
                        className="cap-pill"
                        style={{
                          opacity: p.enabled_forms.includes(f) ? 1 : 0.35,
                          borderColor: p.enabled_forms.includes(f) ? p.brand : undefined,
                        }}
                      >
                        {FORM_LABELS[f] ?? f}
                      </span>
                    ))}
                  </div>
                </span>
                <span className="mono dim" style={{ fontSize: 11 }}>
                  {p.configured_keys.length
                    ? `✓ ${p.configured_keys.join(" · ")}`
                    : p.fields.length
                      ? "не настроен"
                      : "без ключей"}
                </span>
                <span className={`cap-pill ${p.ready ? "" : "dim"}`}>
                  {p.ready ? "встроен" : "drop-in"}
                </span>
                <span onClick={(e) => e.stopPropagation()}>
                  <Toggle
                    on={p.is_active}
                    onChange={(v) => void saveProvider(p.type, { is_active: v })}
                  />
                </span>
                <span className="dim">{expanded === p.type ? "▴" : "▾"}</span>
              </div>
              {expanded === p.type && (
                <div
                  className="tr"
                  style={{ gridTemplateColumns: "1fr", background: "var(--panel2)" }}
                >
                  <div className="grid" style={{ gap: 10, maxWidth: 560 }}>
                    <label className="row">
                      <span className="caps" style={{ width: 130, flex: "0 0 auto" }}>
                        {t.providerTitle}
                      </span>
                      <input
                        className="input"
                        style={{ flex: 1 }}
                        placeholder={p.title}
                        defaultValue={p.display_name}
                        onBlur={(e) => {
                          const v = e.target.value.trim();
                          if (v && v !== p.display_name)
                            void saveProvider(p.type, { display_name: v });
                        }}
                      />
                    </label>
                    <div className="row" style={{ flexWrap: "wrap" }}>
                      <span className="caps" style={{ width: 130, flex: "0 0 auto" }}>
                        {t.payforms}
                      </span>
                      {p.forms.map((f) => {
                        const on = p.enabled_forms.includes(f);
                        return (
                          <button
                            key={f}
                            className="chip-btn"
                            style={{
                              border: `1.5px solid ${on ? p.brand : "var(--border2)"}`,
                              background: on ? p.brand + "22" : "transparent",
                              color: "var(--text)",
                              borderRadius: 99,
                              padding: "5px 12px",
                              fontSize: 12,
                              cursor: "pointer",
                            }}
                            onClick={() =>
                              void saveProvider(p.type, {
                                enabled_forms: on
                                  ? p.enabled_forms.filter((x) => x !== f)
                                  : [...p.enabled_forms, f],
                              })
                            }
                          >
                            {FORM_LABELS[f] ?? f}
                          </button>
                        );
                      })}
                    </div>
                    <label className="row">
                      <span className="caps" style={{ width: 130, flex: "0 0 auto" }}>
                        {t.feePct}
                      </span>
                      <input
                        className="input num"
                        style={{ width: 90 }}
                        type="number"
                        step="0.1"
                        defaultValue={(p.fee_bp / 100).toFixed(1)}
                        onBlur={(e) =>
                          void saveProvider(p.type, {
                            fee_bp: Math.round(Number(e.target.value) * 100) || 0,
                          })
                        }
                      />
                    </label>
                    {p.fields.map((k) => (
                      <label key={k} className="row">
                        <span className="caps" style={{ width: 130, flex: "0 0 auto" }}>
                          {k.replace(/_/g, " ")}
                        </span>
                        <input
                          className="input mono"
                          style={{ flex: 1 }}
                          type={/id$/.test(k) ? "text" : "password"}
                          placeholder={p.configured_keys.includes(k) ? "••••••••" : ""}
                          value={draft[`${p.type}.${k}`] ?? ""}
                          onChange={(e) =>
                            setDraft((d) => ({ ...d, [`${p.type}.${k}`]: e.target.value }))
                          }
                        />
                      </label>
                    ))}
                    <div className="row">
                      <button className="btn secondary sm" onClick={() => void testProvider(p.type)}>
                        {t.checkApi}
                      </button>
                      <button
                        className="btn primary sm"
                        onClick={() => {
                          const settings: Record<string, string> = {};
                          for (const k of p.fields) {
                            const v = draft[`${p.type}.${k}`];
                            if (v) settings[k] = v;
                          }
                          void saveProvider(p.type, { settings });
                        }}
                      >
                        {t.save}
                      </button>
                      {!p.ready && (
                        <span className="dim" style={{ fontSize: 11.5 }}>
                          {t.providerDropinNote}
                        </span>
                      )}
                    </div>
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </>
  );
}
