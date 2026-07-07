/* Screen 02 — Пользователи: search + segment filter + table + 460px drawer. */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api, bytesFmt, dt, dtTime, money } from "../api/client";
import { Drawer, Seg } from "../components/ui";
import { useApp } from "../state/app";

type Row = {
  id: number;
  telegram_id: number | null;
  username: string | null;
  name: string | null;
  status: string;
  balance_minor: number;
  plan_name: string | null;
  expire_at: string | null;
  traffic_used_bytes: number;
  traffic_limit_bytes: number;
  device_limit: number | null;
  created_at: string | null;
  last_seen_at: string | null;
};

type Detail = Row & {
  referral_code: string;
  referral_invited: number;
  referral_earned_minor: number;
  personal_discount_pct: number;
  subscription: {
    short_id: string;
    status: string;
    subscription_url: string | null;
    device_limit: number | null;
  } | null;
  transactions: {
    id: number;
    type: string;
    status: string;
    amount_minor: number;
    gateway: string | null;
    created_at: string | null;
  }[];
};

type Counters = { all: number; active: number; trial: number; expired: number; blocked: number };

const ST_GLYPH: Record<string, [string, string]> = {
  active: ["●", "on"],
  trial: ["◐", "mid"],
  expired: ["○", "off"],
  blocked: ["✕", "off"],
  none: ["—", "off"],
};

function StatusCell({ status, t }: { status: string; t: Record<string, string> }) {
  const [g, cls] = ST_GLYPH[status] ?? ["—", "off"];
  const label =
    status === "active"
      ? t.activeOne
      : status === "trial"
        ? t.trial
        : status === "expired"
          ? t.expiredOne
          : status === "blocked"
            ? t.blocked
            : t.none;
  return (
    <span className={`st ${cls}`}>
      {g} {label}
    </span>
  );
}

export default function Users() {
  const { t, toast, confirm } = useApp();
  const qc = useQueryClient();
  const [q, setQ] = useState("");
  const [qDebounced, setQDebounced] = useState("");
  const [filter, setFilter] = useState<"all" | "active" | "trial" | "expired" | "blocked">("all");
  const [selId, setSelId] = useState<number | null>(null);
  const [tab, setTab] = useState<"overview" | "finance" | "tickets" | "actions">("overview");

  useEffect(() => {
    const h = setTimeout(() => setQDebounced(q), 300);
    return () => clearTimeout(h);
  }, [q]);

  const counters = useQuery({
    queryKey: ["users", "counters"],
    queryFn: () => api.get<Counters>("/api/admin/users/counters"),
  });
  const list = useQuery({
    queryKey: ["users", qDebounced, filter],
    queryFn: () =>
      api.get<{ items: Row[]; total: number }>(
        `/api/admin/users?q=${encodeURIComponent(qDebounced)}&status=${filter}&limit=100`,
      ),
  });
  const detail = useQuery({
    queryKey: ["user", selId],
    queryFn: () => api.get<Detail>(`/api/admin/users/${selId}`),
    enabled: selId !== null,
  });

  function invalidate() {
    void qc.invalidateQueries({ queryKey: ["users"] });
    void qc.invalidateQueries({ queryKey: ["user", selId] });
  }

  const act = useMutation({
    mutationFn: ({ path, body }: { path: string; body?: unknown }) =>
      api.post(`/api/admin/users/${selId}${path}`, body),
    onSuccess: () => {
      invalidate();
      toast("✓");
    },
    onError: (e) => toast(e.message),
  });

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setSelId(null);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const c = counters.data;
  const d = detail.data;
  const cols = "2fr 1fr 1fr 1.4fr 0.8fr 1fr 0.9fr";

  return (
    <>
      <div className="page-head">
        <h1 className="h1">{t.users}</h1>
        <div className="actions">
          <button
            className="btn secondary"
            onClick={() => {
              void (async () => {
                try {
                  const data = await api.get<{ items: Row[] }>(
                    `/api/admin/users?q=${encodeURIComponent(qDebounced)}&status=${filter}&limit=10000`,
                  );
                  const head = ["id", "telegram_id", "username", "first_name", "status", "balance_minor", "subscription", "created_at"];
                  const cell = (v: unknown) => `"${String(v ?? "").replace(/"/g, '""')}"`;
                  const lines = [head.join(",")].concat(
                    data.items.map((r) =>
                      head.map((k) => cell((r as unknown as Record<string, unknown>)[k])).join(","),
                    ),
                  );
                  const blob = new Blob(["\ufeff" + lines.join("\n")], { type: "text/csv;charset=utf-8" });
                  const a = document.createElement("a");
                  a.href = URL.createObjectURL(blob);
                  a.download = `users-${new Date().toISOString().slice(0, 10)}.csv`;
                  a.click();
                  URL.revokeObjectURL(a.href);
                  toast(t.exportCsv + " ✓");
                } catch (e) {
                  toast(String(e));
                }
              })();
            }}
          >
            {t.exportCsv}
          </button>
        </div>
      </div>

      <div className="row" style={{ marginBottom: 14, flexWrap: "wrap" }}>
        <input
          className="input"
          style={{ flex: "1 1 260px" }}
          placeholder={t.userSearchPh}
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <Seg
          value={filter}
          options={[
            { id: "all" as const, label: t.all, count: c?.all },
            { id: "active" as const, label: t.active, count: c?.active },
            { id: "trial" as const, label: t.trial, count: c?.trial },
            { id: "expired" as const, label: t.expired, count: c?.expired },
            { id: "blocked" as const, label: t.blocked, count: c?.blocked },
          ]}
          onChange={setFilter}
        />
      </div>

      <div className="tbl">
        <div className="tr head" style={{ gridTemplateColumns: cols }}>
          <span>{t.colUser}</span>
          <span>Telegram ID</span>
          <span>{t.colStatus}</span>
          <span>{t.colSub}</span>
          <span>{t.colBalance}</span>
          <span>{t.colTraffic}</span>
          <span>{t.colActivity}</span>
        </div>
        {(list.data?.items ?? []).map((u) => (
          <div
            key={u.id}
            className="tr click"
            style={{ gridTemplateColumns: cols }}
            onClick={() => {
              setSelId(u.id);
              setTab("overview");
            }}
          >
            <span className="row" style={{ gap: 10, minWidth: 0 }}>
              <span className="avatar-sq" style={{ flex: "0 0 auto" }}>
                {(u.name ?? u.username ?? "?").slice(0, 2).toUpperCase()}
              </span>
              <span style={{ minWidth: 0 }}>
                <div style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {u.username ? `@${u.username}` : `id${u.id}`}
                </div>
                <div className="dim" style={{ fontSize: 11.5 }}>
                  {u.name ?? "—"}
                </div>
              </span>
            </span>
            <span className="mono muted">{u.telegram_id ?? "—"}</span>
            <StatusCell status={u.status} t={t as unknown as Record<string, string>} />
            <span>
              {u.plan_name ?? "—"}
              {u.expire_at && (
                <div className="dim" style={{ fontSize: 11.5 }}>
                  {t.till} {dt(u.expire_at)}
                </div>
              )}
            </span>
            <span className="mono">{money(u.balance_minor)}</span>
            <span className="mono muted">
              {bytesFmt(u.traffic_used_bytes)} / {u.traffic_limit_bytes ? bytesFmt(u.traffic_limit_bytes) : "∞"}
            </span>
            <span className="dim" style={{ fontSize: 12 }}>
              {dtTime(u.last_seen_at)}
            </span>
          </div>
        ))}
        {list.data && list.data.items.length === 0 && (
          <div className="tr dim">{list.isFetching ? t.loading : "—"}</div>
        )}
      </div>

      {selId !== null && (
        <Drawer onClose={() => setSelId(null)}>
          <div style={{ padding: 20 }}>
            {d ? (
              <>
                <div className="row" style={{ marginBottom: 4 }}>
                  <div className="avatar-sq" style={{ width: 40, height: 40, fontSize: 14 }}>
                    {(d.name ?? d.username ?? "?").slice(0, 2).toUpperCase()}
                  </div>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div className="row" style={{ gap: 8 }}>
                      <b>{d.username ? `@${d.username}` : `id${d.id}`}</b>
                      <StatusCell status={d.status} t={t as unknown as Record<string, string>} />
                    </div>
                    <div className="dim" style={{ fontSize: 12 }}>
                      {d.name ?? "—"} · {d.telegram_id ?? "—"}
                    </div>
                  </div>
                  {d.username && (
                    <a
                      className="btn secondary sm"
                      href={`https://t.me/${d.username}`}
                      target="_blank"
                      rel="noreferrer"
                    >
                      ↗
                    </a>
                  )}
                  <button className="btn secondary sm" onClick={() => setSelId(null)}>
                    ✕
                  </button>
                </div>
                <hr className="sep" />
                <Seg
                  value={tab}
                  options={[
                    { id: "overview" as const, label: t.overviewTab },
                    { id: "finance" as const, label: t.financeTab },
                    { id: "tickets" as const, label: t.ticketsTab },
                    { id: "actions" as const, label: t.actionsTab },
                  ]}
                  onChange={setTab}
                />

                {tab === "overview" && (
                  <div className="grid" style={{ gap: 10, marginTop: 16, fontSize: 13 }}>
                    {(
                      [
                        [t.colSub, d.plan_name ?? "—"],
                        [t.till, d.expire_at ? dt(d.expire_at) : "—"],
                        [
                          t.colTraffic,
                          `${bytesFmt(d.traffic_used_bytes)} / ${d.traffic_limit_bytes ? bytesFmt(d.traffic_limit_bytes) : "∞"}`,
                        ],
                        [t.devices, d.subscription?.device_limit ?? "—"],
                        [t.regDate, dt(d.created_at)],
                        [t.balance, money(d.balance_minor)],
                      ] as [string, string | number][]
                    ).map(([k, v]) => (
                      <div key={k} className="row" style={{ justifyContent: "space-between" }}>
                        <span className="muted">{k}</span>
                        <span className="mono">{v}</span>
                      </div>
                    ))}
                    <div className="row" style={{ justifyContent: "space-between" }}>
                      <span className="muted">HWID</span>
                      <span className="row" style={{ gap: 8 }}>
                        <span className="mono">{d.subscription?.device_limit ?? "—"}</span>
                        <button
                          className="btn secondary sm"
                          onClick={() => act.mutate({ path: "/hwid", body: { delta: 1 } })}
                        >
                          +1
                        </button>
                      </span>
                    </div>
                    <hr className="sep" />
                    <div className="caps">{t.refBlock}</div>
                    <div className="row" style={{ justifyContent: "space-between" }}>
                      <span className="muted">{t.invited}</span>
                      <span className="mono">{d.referral_invited}</span>
                    </div>
                    <div className="row" style={{ justifyContent: "space-between" }}>
                      <span className="muted">{t.earned}</span>
                      <span className="mono">{money(d.referral_earned_minor)}</span>
                    </div>
                    <button
                      className="btn secondary sm"
                      onClick={() => {
                        void navigator.clipboard.writeText(`ref_${d.referral_code}`);
                        toast(t.copied);
                      }}
                    >
                      COPY LINK
                    </button>
                  </div>
                )}

                {tab === "finance" && (
                  <div className="grid" style={{ gap: 12, marginTop: 16 }}>
                    <div className="row" style={{ justifyContent: "space-between" }}>
                      <span className="muted">{t.balance}</span>
                      <b className="mono">{money(d.balance_minor)}</b>
                    </div>
                    <div className="row">
                      <button
                        className="btn secondary sm"
                        onClick={() => act.mutate({ path: "/balance", body: { amount_minor: 10000 } })}
                      >
                        +100 ₽
                      </button>
                      <button
                        className="btn secondary sm"
                        onClick={() => act.mutate({ path: "/balance", body: { amount_minor: 50000 } })}
                      >
                        +500 ₽
                      </button>
                    </div>
                    <div className="caps">{t.giveDays}</div>
                    <div className="row">
                      <button
                        className="btn secondary sm"
                        onClick={() => act.mutate({ path: "/extend", body: { days: 7 } })}
                      >
                        +7
                      </button>
                      <button
                        className="btn secondary sm"
                        onClick={() => act.mutate({ path: "/extend", body: { days: 30 } })}
                      >
                        +30
                      </button>
                    </div>
                    <div className="caps">{t.lastTx}</div>
                    <div className="grid" style={{ gap: 6, fontSize: 12.5 }}>
                      {d.transactions.map((tx) => (
                        <div key={tx.id} className="row" style={{ justifyContent: "space-between" }}>
                          <span className="muted">
                            {dtTime(tx.created_at)} · {tx.type}
                          </span>
                          <span className="mono">{money(tx.amount_minor)}</span>
                        </div>
                      ))}
                      {d.transactions.length === 0 && <span className="dim">—</span>}
                    </div>
                  </div>
                )}

                {tab === "tickets" && (
                  <div className="grid" style={{ marginTop: 16 }}>
                    <span className="dim">→ {t.tickets}</span>
                  </div>
                )}

                {tab === "actions" && (
                  <div className="grid" style={{ gap: 8, marginTop: 16 }}>
                    <button
                      className="btn secondary"
                      onClick={() => act.mutate({ path: "/extend", body: { days: 30 } })}
                    >
                      {t.extend30}
                    </button>
                    <button
                      className="btn secondary"
                      onClick={() => act.mutate({ path: "/reset-traffic" })}
                    >
                      {t.resetTraffic}
                    </button>
                    <button
                      className="btn secondary"
                      onClick={() => act.mutate({ path: "/hwid", body: { delta: 1 } })}
                    >
                      {t.extendHwid}
                    </button>
                    {d.status !== "blocked" ? (
                      <button
                        className="btn danger"
                        onClick={async () => {
                          if (await confirm(t.blockConfirm)) act.mutate({ path: "/block" });
                        }}
                      >
                        {t.block}
                      </button>
                    ) : (
                      <button className="btn secondary" onClick={() => act.mutate({ path: "/unblock" })}>
                        {t.unblock}
                      </button>
                    )}
                  </div>
                )}
              </>
            ) : (
              <span className="dim">{t.loading}</span>
            )}
          </div>
        </Drawer>
      )}
    </>
  );
}
