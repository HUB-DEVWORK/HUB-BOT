/* Screen 12 — Серверы Remnawave: panel summary + node table + for-sale toggles. */

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, bytesFmt, dtTime } from "../api/client";
import { Toggle } from "../components/ui";
import { useApp } from "../state/app";

type Node = {
  id: number;
  name: string;
  country_code: string | null;
  address: string | null;
  status: "online" | "maintenance" | "offline";
  users_online: number;
  traffic_day_bytes: number;
  load_pct: number;
  ping_ms: number | null;
  uptime_pct: number | null;
  is_for_sale: boolean;
  last_sync_at: string | null;
};
type Resp = { panel_url: string; items: Node[]; squads: { id: number; name: string }[] };

export default function Servers() {
  const { t, toast } = useApp();
  const qc = useQueryClient();
  const [syncing, setSyncing] = useState(false);

  const data = useQuery({
    queryKey: ["servers"],
    queryFn: () => api.get<Resp>("/api/admin/servers"),
  });

  async function sync() {
    setSyncing(true);
    try {
      const r = await api.post<{ synced: number }>("/api/admin/servers/sync");
      void qc.invalidateQueries({ queryKey: ["servers"] });
      toast(`✓ ${r.synced} ${t.nodes}`);
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setSyncing(false);
    }
  }

  async function toggleSale(n: Node, on: boolean) {
    await api.patch(`/api/admin/servers/${n.id}`, { is_for_sale: on });
    void qc.invalidateQueries({ queryKey: ["servers"] });
    toast(`${n.name}: ${on ? t.on : t.off}`);
  }

  const d = data.data;
  const totalUsers = d?.items.reduce((a, n) => a + n.users_online, 0) ?? 0;
  const totalTraffic = d?.items.reduce((a, n) => a + n.traffic_day_bytes, 0) ?? 0;
  const lastSync = d?.items.map((n) => n.last_sync_at).filter(Boolean).sort().at(-1);
  const cols = "1.6fr 1.2fr 0.8fr 1fr 0.7fr 0.7fr 1.1fr auto";

  return (
    <>
      <div className="page-head">
        <h1 className="h1">{t.servers}</h1>
        <div className="actions">
          <button className="btn primary" onClick={sync} disabled={syncing}>
            {syncing ? <span className="spin">⟳</span> : "⟳"} {t.syncBtn}
          </button>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 14 }}>
        <div className="row" style={{ flexWrap: "wrap", gap: 24 }}>
          <span className="mono muted">{d?.panel_url ?? "…"}</span>
          <span className="caps">
            {d?.items.length ?? 0} {t.nodes}
          </span>
          <span className="caps">
            {totalUsers} {t.online.toLowerCase()}
          </span>
          <span className="caps">{bytesFmt(totalTraffic)}/сут</span>
          <span className="caps" style={{ marginLeft: "auto" }}>
            {t.lastSync}: {lastSync ? dtTime(lastSync) : "—"}
          </span>
        </div>
      </div>

      <div className="tbl">
        <div className="tr head" style={{ gridTemplateColumns: cols }}>
          <span>NODE</span>
          <span>{t.load}</span>
          <span>{t.online}</span>
          <span>{t.colTraffic}</span>
          <span>PING</span>
          <span>UPTIME</span>
          <span>{t.colStatus}</span>
          <span>{t.forSale}</span>
        </div>
        {(d?.items ?? []).map((n) => (
          <div key={n.id} className="tr" style={{ gridTemplateColumns: cols }}>
            <span>
              <b style={{ fontWeight: 500 }}>{n.name}</b>
              <div className="dim" style={{ fontSize: 11.5 }}>
                {n.country_code ?? "—"} · {n.address ?? "—"}
              </div>
            </span>
            <span>
              <div className="mono" style={{ fontSize: 11, marginBottom: 3 }}>
                {n.load_pct}%
              </div>
              <div className="prog">
                <i
                  style={{
                    width: `${n.load_pct}%`,
                    background: n.load_pct > 70 ? "var(--text)" : "var(--muted)",
                  }}
                />
              </div>
            </span>
            <span className="mono">{n.users_online}</span>
            <span className="mono muted">{bytesFmt(n.traffic_day_bytes)}</span>
            <span className="mono muted">{n.ping_ms !== null ? `${n.ping_ms}ms` : "—"}</span>
            <span className="mono muted">
              {n.uptime_pct !== null ? `${n.uptime_pct}%` : "—"}
            </span>
            <span
              className={`st ${
                n.status === "online" ? "on" : n.status === "maintenance" ? "mid" : "off"
              }`}
            >
              {n.status === "online" && <span className="status-dot" />}
              {n.status === "online" ? t.onlineSt : n.status === "maintenance" ? t.maintSt : t.offlineSt}
            </span>
            <Toggle on={n.is_for_sale} onChange={(v) => void toggleSale(n, v)} />
          </div>
        ))}
        {d && d.items.length === 0 && (
          <div className="tr dim">— · {t.syncBtn} →</div>
        )}
      </div>
    </>
  );
}
