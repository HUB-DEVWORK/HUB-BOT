/* Screen 14 — Обслуживание: action cards, bot migration, report topics. */

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, getToken } from "../api/client";
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

type MigSource = "shopbot" | "bedolaga" | "remnashop" | "threexui" | "minishop" | "jolymmiels";
type Squad = { uuid: string; name: string };

const MIG_SOURCES: { key: MigSource; label: string }[] = [
  { key: "shopbot", label: "remnawave-shopbot" },
  { key: "bedolaga", label: "Bedolaga" },
  { key: "remnashop", label: "RemnaShop" },
  { key: "threexui", label: "3x-ui" },
  { key: "minishop", label: "remnawave-minishop" },
  { key: "jolymmiels", label: "remnawave-telegram-shop" },
];

const MIG_ACCEPT: Record<MigSource, string> = {
  shopbot: ".db,.sqlite,.sqlite3",
  bedolaga: ".db,.sqlite,.sqlite3,.sql,.json,.gz,.tgz,.tar.gz",
  remnashop: ".sql,.json",
  threexui: ".db,.sqlite,.sqlite3",
  minishop: ".sql,.json",
  jolymmiels: ".sql,.json",
};

// Human labels for the run-summary counters (all importers share this key set).
const MIG_RESULT_LABELS: Record<string, string> = {
  users_created: "юзеры +",
  users_updated: "обновлено",
  referrals_linked: "рефералы",
  subscriptions: "подписки",
  transactions: "платежи",
  promocodes: "промокоды",
  panel_users_created: "создано на панели",
  without_telegram: "без Telegram",
};

function summaryLine(r: Record<string, unknown>): string {
  const parts = Object.entries(MIG_RESULT_LABELS)
    .filter(([k]) => typeof r[k] === "number")
    .map(([k, label]) => `${label} ${r[k]}`);
  const skipped = (r.skipped as string[] | undefined) ?? [];
  return (
    parts.join(", ") +
    (skipped.length ? ` · пропущено ${skipped.length}: ${skipped.slice(0, 3).join("; ")}…` : "")
  );
}

export default function Maintenance() {
  const { t, toast, confirm } = useApp();
  const qc = useQueryClient();
  const [migSrc, setMigSrc] = useState<MigSource>("shopbot");
  const [dsn, setDsn] = useState("");
  const [sbFileId, setSbFileId] = useState<string | null>(null);
  const [sbFileName, setSbFileName] = useState<string | null>(null);
  const [sbResult, setSbResult] = useState<string | null>(null);
  const [sbBusy, setSbBusy] = useState(false);
  const [probed, setProbed] = useState(false);
  const [squads, setSquads] = useState<Squad[]>([]);
  const [squad, setSquad] = useState("");
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

  function resetMigration(src: MigSource) {
    setMigSrc(src);
    setSbFileId(null);
    setSbFileName(null);
    setSbResult(null);
    setDsn("");
    setProbed(false);
    setSquads([]);
    setSquad("");
  }

  function applyProbe(probe: {
    ok: boolean;
    counts?: Record<string, number>;
    squads?: Squad[];
    detail?: string;
  }): boolean {
    if (probe.ok && probe.counts) {
      setSbResult(
        `${t.sbFound}: ` +
          Object.entries(probe.counts)
            .map(([k, v]) => `${k} ${v}`)
            .join(" · ") +
          (migSrc === "threexui" && !(probe.squads ?? []).length ? ` · ${t.migNoSquads}` : ""),
      );
      setSquads(probe.squads ?? []);
      setSquad(probe.squads?.[0]?.uuid ?? "");
      setProbed(true);
      return true;
    }
    setSbResult(`✕ ${probe.detail ?? "error"}`);
    setProbed(false);
    return false;
  }

  async function sbUpload(file: File) {
    setSbResult(null);
    setProbed(false);
    setSbBusy(true);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const uploadUrl =
        migSrc === "shopbot"
          ? "/api/admin/migration/shopbot/upload"
          : "/api/admin/migration/upload";
      const res = await fetch(uploadUrl, {
        method: "POST",
        headers: { Authorization: `Bearer ${getToken() ?? ""}` },
        body: fd,
      });
      const j = (await res.json()) as { file_id?: string; detail?: string };
      if (!res.ok || !j.file_id) throw new Error(j.detail ?? `HTTP ${res.status}`);
      setSbFileId(j.file_id);
      setSbFileName(file.name);
      setDsn("");
      const probeUrl =
        migSrc === "shopbot"
          ? "/api/admin/migration/shopbot/probe"
          : `/api/admin/migration/${migSrc}/probe`;
      const probe = await api.post<{
        ok: boolean;
        counts?: Record<string, number>;
        squads?: Squad[];
        detail?: string;
      }>(probeUrl, { file_id: j.file_id });
      if (!applyProbe(probe)) setSbFileId(null);
    } catch (e) {
      setSbResult(`✕ ${(e as Error).message}`);
      setSbFileId(null);
    } finally {
      setSbBusy(false);
    }
  }

  async function probeDsn() {
    if (!dsn) return;
    setSbResult(null);
    setProbed(false);
    setSbFileId(null);
    setSbFileName(null);
    setSbBusy(true);
    try {
      const probe = await api.post<{
        ok: boolean;
        counts?: Record<string, number>;
        detail?: string;
      }>(`/api/admin/migration/${migSrc}/probe`, { dsn });
      applyProbe(probe);
    } catch (e) {
      setSbResult(`✕ ${(e as Error).message}`);
    } finally {
      setSbBusy(false);
    }
  }

  async function sbRun() {
    if (!probed) return;
    if (!(await confirm(t.migConfirm))) return;
    setSbBusy(true);
    setSbResult(t.sbRunning);
    try {
      const runUrl =
        migSrc === "shopbot"
          ? "/api/admin/migration/shopbot/run"
          : `/api/admin/migration/${migSrc}/run`;
      const body: Record<string, unknown> = sbFileId ? { file_id: sbFileId } : { dsn };
      if (migSrc === "threexui" && squad) body.squad_uuid = squad;
      const r = await api.post<Record<string, unknown>>(runUrl, body);
      setSbResult(`✓ ${t.sbDone}: ${summaryLine(r)}`);
      setSbFileId(null);
      setSbFileName(null);
      setProbed(false);
    } catch (e) {
      setSbResult(`✕ ${(e as Error).message}`);
    } finally {
      setSbBusy(false);
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
            <div className="row" style={{ flexWrap: "wrap", gap: 6 }}>
              <span className="dim" style={{ fontSize: 12 }}>{t.migSource}:</span>
              {MIG_SOURCES.map((s) => (
                <button
                  key={s.key}
                  className={`btn sm ${migSrc === s.key ? "primary" : "secondary"}`}
                  onClick={() => resetMigration(s.key)}
                >
                  {s.label}
                </button>
              ))}
            </div>
            <div className="dim" style={{ fontSize: 12 }}>
              {migSrc === "shopbot" && t.sbHint}
              {migSrc === "bedolaga" && t.migHintBedolaga}
              {migSrc === "remnashop" && t.migHintRemnashop}
              {migSrc === "threexui" && t.migHintThreexui}
            </div>
            <div className="row" style={{ flexWrap: "wrap", gap: 8 }}>
              <label className="btn secondary" style={{ cursor: "pointer" }}>
                {sbFileName ?? t.migPickFile}
                <input
                  type="file"
                  accept={MIG_ACCEPT[migSrc]}
                  style={{ display: "none" }}
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    if (f) void sbUpload(f);
                    e.target.value = "";
                  }}
                />
              </label>
              <button
                className="btn primary"
                disabled={!probed || sbBusy || (migSrc === "threexui" && !squad)}
                onClick={() => void sbRun()}
              >
                {sbBusy ? "…" : t.startMigration}
              </button>
            </div>
            {migSrc === "threexui" && probed && squads.length > 0 && (
              <div className="row" style={{ flexWrap: "wrap", gap: 8 }}>
                <span className="dim" style={{ fontSize: 12 }}>{t.migSquad}</span>
                <select
                  className="input"
                  style={{ flex: "1 1 180px" }}
                  value={squad}
                  onChange={(e) => setSquad(e.target.value)}
                >
                  {squads.map((s) => (
                    <option key={s.uuid} value={s.uuid}>
                      {s.name}
                    </option>
                  ))}
                </select>
              </div>
            )}
            {(migSrc === "bedolaga" || migSrc === "remnashop") && (
              <>
                <div className="dim" style={{ fontSize: 12, marginTop: 4 }}>{t.migDsnLabel}</div>
                <div className="row" style={{ flexWrap: "wrap", gap: 8 }}>
                  <input
                    className="input mono"
                    style={{ flex: "1 1 260px" }}
                    placeholder="postgresql://user:pass@host:5432/oldbot"
                    value={dsn}
                    onChange={(e) => {
                      setDsn(e.target.value);
                      setProbed(false); // an edited DSN must be re-probed before run
                    }}
                  />
                  <button className="btn secondary" disabled={!dsn || sbBusy} onClick={() => void probeDsn()}>
                    {t.checkConn}
                  </button>
                </div>
              </>
            )}
            {sbResult && (
              <div className="mono muted" style={{ fontSize: 12, whiteSpace: "pre-wrap" }}>
                {sbResult}
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
