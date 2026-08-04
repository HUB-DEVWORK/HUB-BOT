/* Screen — Статистика (Продукт): user & subscription breakdowns + 14-day new-users chart. */

import { useQuery } from "@tanstack/react-query";

import { api } from "../api/client";
import { Bars, Kpi } from "../components/ui";
import { useApp } from "../state/app";

type Stats = {
  users: {
    total: number;
    new_today: number;
    new_week: number;
    new_month: number;
    with_sub: number;
    without_sub: number;
    with_trial: number;
    blocked: number;
    bot_blocked: number;
    chart: { date: string; count: number }[];
  };
  subs: {
    active: number;
    disabled: number;
    expired: number;
    trial: number;
    unlimited_traffic: number;
    limited_traffic: number;
  };
};

const n = (v: number | undefined) => (v ?? 0).toLocaleString("ru-RU");

export default function StatsScreen() {
  const { t } = useApp();
  const q = useQuery({
    queryKey: ["stats"],
    queryFn: () => api.get<Stats>("/api/admin/stats"),
    refetchInterval: 60_000,
  });
  const d = q.data;
  const u = d?.users;
  const s = d?.subs;

  return (
    <>
      <div className="page-head">
        <h1 className="h1">{t.statsTitle}</h1>
      </div>

      {/* New-users chart */}
      <div className="card" style={{ marginBottom: 14 }}>
        <div className="row" style={{ justifyContent: "space-between", marginBottom: 14 }}>
          <span className="caps">{t.statsNewUsers14}</span>
          <span className="mono" style={{ fontSize: 13 }}>
            Σ {u ? n(u.chart.reduce((a, x) => a + x.count, 0)) : "…"}
          </span>
        </div>
        {u && (
          <Bars
            data={u.chart.map((x) => x.count)}
            tips={u.chart.map((x) => `${x.date} · ${x.count}`)}
          />
        )}
      </div>

      {/* Users */}
      <div className="caps" style={{ margin: "4px 2px 8px" }}>
        {t.statsUsers}
      </div>
      <div className="kpis" style={{ marginBottom: 14 }}>
        <Kpi label={t.statsTotal} value={u ? n(u.total) : "…"} />
        <Kpi
          label={t.statsNew}
          value={u ? n(u.new_today) : "…"}
          note={u ? `${t.statsWeek} ${n(u.new_week)} · ${t.statsMonth} ${n(u.new_month)}` : ""}
        />
        <Kpi label={t.statsWithSub} value={u ? n(u.with_sub) : "…"} />
        <Kpi label={t.statsWithoutSub} value={u ? n(u.without_sub) : "…"} />
        <Kpi label={t.statsWithTrial} value={u ? n(u.with_trial) : "…"} />
        <Kpi label={t.statsBlocked} value={u ? n(u.blocked) : "…"} outlined />
        <Kpi label={t.statsBotBlocked} value={u ? n(u.bot_blocked) : "…"} outlined />
      </div>

      {/* Subscriptions */}
      <div className="caps" style={{ margin: "4px 2px 8px" }}>
        {t.statsSubs}
      </div>
      <div className="kpis">
        <Kpi label={t.active} value={s ? n(s.active) : "…"} />
        <Kpi label={t.statsDisabled} value={s ? n(s.disabled) : "…"} />
        <Kpi label={t.expired} value={s ? n(s.expired) : "…"} />
        <Kpi label={t.trial} value={s ? n(s.trial) : "…"} />
        <Kpi label={t.statsUnlimited} value={s ? n(s.unlimited_traffic) : "…"} />
        <Kpi label={t.statsLimited} value={s ? n(s.limited_traffic) : "…"} />
      </div>
    </>
  );
}
