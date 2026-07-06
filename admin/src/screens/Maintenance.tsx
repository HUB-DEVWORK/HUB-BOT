/* Screen 14 — Обслуживание: action cards, bedolaga migration, report topics. */

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../api/client";
import { Field, Toggle } from "../components/ui";
import { useApp } from "../state/app";

type Topic = {
  id: number;
  code: string;
  topic_id: number | null;
  schedule: string | null;
  enabled: boolean;
};
type TopicsResp = { group_id: string; items: Topic[] };

const TOPIC_NAMES: Record<string, string> = {
  daily_report: "Отчёты · ежедневно",
  backups: "Бэкапы",
  payments: "Платежи · мгновенно",
  tickets: "Тикеты",
  alerts: "Алерты",
  weekly_report: "Недельный отчёт",
  registrations: "Регистрации",
};

export default function Maintenance() {
  const { t, toast, confirm } = useApp();
  const qc = useQueryClient();
  const [dsn, setDsn] = useState("");
  const [migResult, setMigResult] = useState<string | null>(null);
  const [groupId, setGroupId] = useState<string | null>(null);

  const topics = useQuery({
    queryKey: ["report-topics"],
    queryFn: () => api.get<TopicsResp>("/api/admin/report-topics"),
  });

  async function action(name: string, label: string) {
    if (!(await confirm(`${t.confirmAction} · ${label}`))) return;
    try {
      await api.post(`/api/admin/maintenance/${name}`);
      toast(`${label} ✓`);
    } catch (e) {
      toast((e as Error).message);
    }
  }

  async function backup() {
    try {
      await api.post("/api/admin/maintenance/backup");
      toast(`${t.quickBackup} ✓`);
    } catch (e) {
      toast((e as Error).message);
    }
  }

  async function testMigration() {
    setMigResult(null);
    try {
      const r = await api.post<{ ok: boolean; counts?: Record<string, number | null>; detail?: string }>(
        "/api/admin/migration/test",
        { dsn },
      );
      if (r.ok && r.counts) {
        setMigResult(
          Object.entries(r.counts)
            .map(([k, v]) => `${k}: ${v ?? "—"}`)
            .join(" · "),
        );
      } else {
        setMigResult(`✕ ${r.detail ?? "error"}`);
      }
    } catch (e) {
      setMigResult(`✕ ${(e as Error).message}`);
    }
  }

  async function patchTopic(tp: Topic, p: Partial<Topic>) {
    await api.patch(`/api/admin/report-topics/${tp.id}`, p);
    void qc.invalidateQueries({ queryKey: ["report-topics"] });
  }

  async function saveGroup() {
    if (groupId === null) return;
    await api.post("/api/admin/report-topics/group", { group_id: groupId });
    void qc.invalidateQueries({ queryKey: ["report-topics"] });
    toast(t.saved);
  }

  const tp = topics.data;

  return (
    <>
      <div className="page-head">
        <h1 className="h1">{t.maintenance}</h1>
      </div>

      <div className="kpis" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))" }}>
        <div className="card">
          <div className="caps">{t.update}</div>
          <div className="mono" style={{ margin: "8px 0" }}>
            v0.1.0
          </div>
          <button className="btn secondary sm" onClick={() => void action("update", t.updateBot)}>
            {t.updateBot}
          </button>
        </div>
        <div className="card">
          <div className="caps">{t.quickBackup}</div>
          <div className="mono" style={{ margin: "8px 0" }}>
            pg_dump + zip
          </div>
          <button className="btn secondary sm" onClick={backup}>
            {t.quickBackup}
          </button>
        </div>
        <div className="card">
          <div className="caps">{t.restartPanel}</div>
          <div className="mono" style={{ margin: "8px 0" }}>
            web
          </div>
          <button
            className="btn secondary sm"
            onClick={() => void action("restart-panel", t.restartPanel)}
          >
            ⟳
          </button>
        </div>
        <div className="card">
          <div className="caps">{t.restartBot}</div>
          <div className="mono" style={{ margin: "8px 0" }}>
            bot
          </div>
          <button
            className="btn secondary sm"
            onClick={() => void action("restart-bot", t.restartBot)}
          >
            ⟳
          </button>
        </div>
        <div className="card" style={{ borderColor: "var(--muted)" }}>
          <div className="caps">
            {t.rebootServer} · {t.dangerous}
          </div>
          <div className="mono" style={{ margin: "8px 0" }}>
            host
          </div>
          <button
            className="btn danger sm"
            onClick={() => void action("reboot-server", t.rebootServer)}
          >
            ⚠ ⟳
          </button>
        </div>
      </div>

      <div className="cols">
        <div className="card main-col">
          <div className="caps" style={{ marginBottom: 12 }}>
            {t.migration}
          </div>
          <div className="grid" style={{ gap: 10 }}>
            <input
              className="input mono"
              placeholder="postgresql://user:pass@host:5432/bedolaga"
              value={dsn}
              onChange={(e) => setDsn(e.target.value)}
            />
            <div className="row">
              <button className="btn secondary" disabled={!dsn} onClick={testMigration}>
                {t.checkConn}
              </button>
              <button className="btn primary" disabled title="после проверки">
                {t.startMigration}
              </button>
            </div>
            {migResult && (
              <div className="mono muted" style={{ fontSize: 12 }}>
                {migResult}
              </div>
            )}
          </div>
        </div>

        <div className="card side-col">
          <div className="caps" style={{ marginBottom: 12 }}>
            {t.reportsGroup}
          </div>
          <div className="grid" style={{ gap: 10 }}>
            <Field label="GROUP ID">
              <div className="row" style={{ flexWrap: "wrap" }}>
                <input
                  className="input mono"
                  style={{ flex: "1 1 140px" }}
                  placeholder="-100…"
                  value={groupId ?? tp?.group_id ?? ""}
                  onChange={(e) => setGroupId(e.target.value)}
                />
                <button className="btn secondary sm" onClick={saveGroup}>
                  {t.checkGroup}
                </button>
              </div>
            </Field>
            <div className="grid" style={{ gap: 8 }}>
              {(tp?.items ?? []).map((topic) => (
                <div
                  key={topic.id}
                  style={{
                    display: "grid",
                    gridTemplateColumns: "minmax(0,1fr) 58px auto",
                    gap: 8,
                    alignItems: "center",
                    fontSize: 12.5,
                  }}
                >
                  <span style={{ minWidth: 0 }}>
                    {TOPIC_NAMES[topic.code] ?? topic.code}
                    <div className="dim mono" style={{ fontSize: 10 }}>
                      {topic.schedule ?? "—"}
                    </div>
                  </span>
                  <input
                    className="input num"
                    style={{ width: 58 }}
                    placeholder="ID"
                    defaultValue={topic.topic_id ?? ""}
                    onBlur={(e) => {
                      const v = Number(e.target.value) || null;
                      if (v !== topic.topic_id) void patchTopic(topic, { topic_id: v });
                    }}
                  />
                  <Toggle
                    on={topic.enabled}
                    onChange={(v) => void patchTopic(topic, { enabled: v })}
                  />
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
