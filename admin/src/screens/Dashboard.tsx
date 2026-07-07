/* Screen 01 — Дашборд: KPIs, revenue 14d, system panel, events, sources. */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { api, bytesFmt, dtTime, money } from "../api/client";
import { Bars, Kpi } from "../components/ui";
import { useApp } from "../state/app";

type Dash = {
  revenue_today_minor: number;
  revenue_yesterday_minor: number;
  active_subscriptions: number;
  total_users: number;
  new_users_24h: number;
  new_trials_24h: number;
  trial_conversion: { used: number; converted: number; pct: number };
  online_now: number;
  revenue_14d: { date: string; amount_minor: number }[];
  events: { id: number; at: string | null; actor: string; action: string; entity: string | null }[];
  sources_30d: { total: number; campaigns: number; referrals: number; organic: number };
};

type SystemInfo = {
  redis: string;
  db_size_bytes: number | null;
  maintenance_mode: boolean;
  backup_enabled: boolean;
  backup_time: string;
  panel: { status: string; version?: string; detail?: string };
};

const EVENT_GLYPHS: Record<string, string> = {
  create: "+",
  patch: "↻",
  delete: "✕",
  block: "✕",
  save: "✓",
  sync: "⟳",
  login: "✓",
};

function glyph(action: string): string {
  const suffix = action.split(".").pop() ?? "";
  return EVENT_GLYPHS[suffix] ?? "✓";
}

export default function Dashboard() {
  const { t, toast } = useApp();
  const nav = useNavigate();
  const qc = useQueryClient();
  const [syncing, setSyncing] = useState(false);

  const dash = useQuery({
    queryKey: ["dashboard"],
    queryFn: () => api.get<Dash>("/api/admin/dashboard"),
    refetchInterval: 30_000,
  });
  const sys = useQuery({
    queryKey: ["system"],
    queryFn: () => api.get<SystemInfo>("/api/admin/dashboard/system"),
  });

  const backup = useMutation({
    mutationFn: () => api.post("/api/admin/maintenance/backup"),
    onSuccess: () => toast(t.quickBackup + " ✓"),
    onError: (e) => toast(e.message),
  });

  async function syncPanel() {
    setSyncing(true);
    try {
      const res = await api.post<{ synced: number }>("/api/admin/servers/sync");
      toast(`${t.syncRemnawave}: ${res.synced} ${t.nodes}`);
      void qc.invalidateQueries({ queryKey: ["system"] });
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setSyncing(false);
    }
  }

  const d = dash.data;
  const s = sys.data;
  const deltaPct =
    d && d.revenue_yesterday_minor > 0
      ? Math.round(
          ((d.revenue_today_minor - d.revenue_yesterday_minor) / d.revenue_yesterday_minor) * 100,
        )
      : null;
  const now = new Date();

  return (
    <>
      <div className="page-head">
        <div>
          <h1 className="h1">{t.overview}</h1>
          <div className="caps sub">
            {now.toLocaleDateString("ru-RU", { weekday: "short" }).toUpperCase()} ·{" "}
            {now.toLocaleDateString("ru-RU")} · {t.updatedAt}{" "}
            {now.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })}
          </div>
        </div>
        <div className="actions">
          <button className="btn secondary" onClick={() => backup.mutate()}>
            {t.backupNow}
          </button>
          <button className="btn primary" onClick={() => nav("/broadcasts")}>
            {t.createBroadcast}
          </button>
        </div>
      </div>

      <div className="kpis">
        <Kpi
          label={t.revenueToday}
          value={d ? money(d.revenue_today_minor) : "…"}
          note={deltaPct !== null ? `${deltaPct >= 0 ? "+" : ""}${deltaPct}% ${t.vsYesterday}` : "—"}
        />
        <Kpi
          label={t.activeSubs}
          value={d ? d.active_subscriptions.toLocaleString("ru-RU") : "…"}
          note={
            d && d.total_users
              ? `${Math.round((d.active_subscriptions / d.total_users) * 100)}% ${t.ofUsers} ${d.total_users.toLocaleString("ru-RU")}`
              : "—"
          }
        />
        <Kpi
          label={t.new24h}
          value={d ? d.new_users_24h : "…"}
          note={d ? `${d.new_trials_24h} ${t.trials}` : "—"}
        />
        <Kpi label={t.online} value={d ? d.online_now : "…"} note="Remnawave" />
        <Kpi
          label={t.trialConversion}
          value={d ? `${d.trial_conversion.pct}%` : "…"}
          note={d ? `${d.trial_conversion.converted}/${d.trial_conversion.used}` : "—"}
        />
      </div>

      <div className="cols">
        <div className="main-col">
          <div className="card">
            <div className="row" style={{ justifyContent: "space-between", marginBottom: 14 }}>
              <span className="caps">{t.revenue14}</span>
              <span className="mono" style={{ fontSize: 13 }}>
                Σ {d ? money(d.revenue_14d.reduce((a, x) => a + x.amount_minor, 0)) : "…"}
              </span>
            </div>
            {d && (
              <Bars
                data={d.revenue_14d.map((x) => x.amount_minor)}
                tips={d.revenue_14d.map((x) => `${x.date} · ${money(x.amount_minor)}`)}
              />
            )}
          </div>

          <div className="card">
            <div className="caps" style={{ marginBottom: 12 }}>
              {t.lastEvents}
            </div>
            <div className="grid" style={{ gap: 8 }}>
              {(d?.events ?? []).slice(0, 12).map((e) => (
                <div key={e.id} className="row" style={{ fontSize: 13 }}>
                  <span className="mono dim" style={{ fontSize: 11, width: 84, flex: "0 0 auto" }}>
                    {dtTime(e.at)}
                  </span>
                  <span className="mono" style={{ width: 16, flex: "0 0 auto" }}>
                    {glyph(e.action)}
                  </span>
                  <span className="muted" style={{ overflow: "hidden", textOverflow: "ellipsis" }}>
                    {e.actor} · {e.action}
                    {e.entity ? ` · ${e.entity}` : ""}
                  </span>
                </div>
              ))}
              {d && d.events.length === 0 && <span className="dim">—</span>}
            </div>
          </div>
        </div>

        <div className="side-col">
          <div className="card">
            <div className="caps" style={{ marginBottom: 12 }}>
              {t.system}
            </div>
            <div className="grid" style={{ gap: 9, fontSize: 13 }}>
              <div className="row" style={{ justifyContent: "space-between" }}>
                <span className="muted">Remnawave API</span>
                <span className="mono st on">
                  {s?.panel.status === "ok" ? (s.panel.version ?? "OK") : "—"}
                </span>
              </div>
              <div className="row" style={{ justifyContent: "space-between" }}>
                <span className="muted">{t.dbSize}</span>
                <span className="mono">{s?.db_size_bytes ? bytesFmt(s.db_size_bytes) : "—"}</span>
              </div>
              <div className="row" style={{ justifyContent: "space-between" }}>
                <span className="muted">Redis</span>
                <span className={`st ${s?.redis === "ok" ? "on" : "off"}`}>
                  {s?.redis === "ok" ? "OK" : "ERR"}
                </span>
              </div>
              <div className="row" style={{ justifyContent: "space-between" }}>
                <span className="muted">{t.autoBackup}</span>
                <span className="mono">{s?.backup_enabled ? `✓ ${s.backup_time}` : "✕"}</span>
              </div>
              <div className="row" style={{ justifyContent: "space-between" }}>
                <span className="muted">{t.queue}</span>
                <span className="mono">0</span>
              </div>
              <div className="row" style={{ justifyContent: "space-between" }}>
                <span className="muted">{t.maintenanceMode}</span>
                <span className={`st ${s?.maintenance_mode ? "on" : "off"}`}>
                  {s?.maintenance_mode ? t.on : t.off}
                </span>
              </div>
            </div>
            <hr className="sep" />
            <div className="grid" style={{ gap: 8 }}>
              <button className="btn secondary" onClick={syncPanel} disabled={syncing}>
                {syncing ? <span className="spin">⟳</span> : "⟳"} {t.syncRemnawave}
              </button>
              <button className="btn secondary" onClick={() => nav("/promos")}>
                {t.createPromo}
              </button>
            </div>
          </div>

          <div className="card">
            <div className="caps" style={{ marginBottom: 12 }}>
              {t.sources30}
            </div>
            {d && d.sources_30d.total > 0 ? (
              <div className="grid" style={{ gap: 10 }}>
                {(
                  [
                    [t.organic, d.sources_30d.organic],
                    [t.referrals, d.sources_30d.referrals],
                    [t.fromCampaigns, d.sources_30d.campaigns],
                  ] as [string, number][]
                ).map(([label, v]) => (
                  <div key={label}>
                    <div className="row" style={{ justifyContent: "space-between", fontSize: 12 }}>
                      <span className="muted">{label}</span>
                      <span className="mono">
                        {Math.round((v / d.sources_30d.total) * 100)}%
                      </span>
                    </div>
                    <div className="prog" style={{ marginTop: 4 }}>
                      <i style={{ width: `${(v / d.sources_30d.total) * 100}%` }} />
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <span className="dim">—</span>
            )}
          </div>
        </div>
      </div>
    </>
  );
}
