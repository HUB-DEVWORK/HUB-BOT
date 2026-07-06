/* Screen 08 — Умные рассылки: renewal reminder + RF holiday promo calendar. */

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api } from "../api/client";
import { Field, Seg, Toggle } from "../components/ui";
import { useApp } from "../state/app";

type Reminder = {
  enabled: boolean;
  days_before: string;
  send_time: string;
  text: string;
  button_enabled: boolean;
};
type Holiday = {
  id: number;
  date: string;
  name: string;
  enabled: boolean;
  reward_type: "discount" | "days" | "balance";
  value: number;
  send_time: string;
  results: Record<string, { sent?: number; conv?: number }>;
};

export default function Smart() {
  const { t, toast } = useApp();
  const qc = useQueryClient();
  const [rem, setRem] = useState<Reminder | null>(null);
  const [dirty, setDirty] = useState(false);

  const reminder = useQuery({
    queryKey: ["smart-reminder"],
    queryFn: () => api.get<Reminder>("/api/admin/smart-reminder"),
  });
  const holidays = useQuery({
    queryKey: ["holidays"],
    queryFn: () => api.get<{ items: Holiday[] }>("/api/admin/holidays"),
  });

  useEffect(() => {
    if (reminder.data && !dirty) setRem(reminder.data);
  }, [reminder.data, dirty]);

  function patch(p: Partial<Reminder>) {
    setRem((r) => (r ? { ...r, ...p } : r));
    setDirty(true);
  }

  async function saveReminder() {
    if (!rem) return;
    try {
      await api.patch("/api/admin/smart-reminder", rem);
      setDirty(false);
      void qc.invalidateQueries({ queryKey: ["smart-reminder"] });
      toast(t.applied);
    } catch (e) {
      toast((e as Error).message);
    }
  }

  async function patchHoliday(h: Holiday, p: Partial<Holiday>) {
    try {
      await api.patch(`/api/admin/holidays/${h.id}`, p);
      void qc.invalidateQueries({ queryKey: ["holidays"] });
    } catch (e) {
      toast((e as Error).message);
    }
  }

  // Nearest upcoming enabled holiday.
  const now = new Date();
  const nearest = (holidays.data?.items ?? [])
    .filter((h) => h.enabled)
    .map((h) => {
      const [d, m] = h.date.split(".").map(Number);
      const dt = new Date(now.getFullYear(), m - 1, d);
      if (dt < now) dt.setFullYear(dt.getFullYear() + 1);
      return { h, dt };
    })
    .sort((a, b) => a.dt.getTime() - b.dt.getTime())[0];

  const previewText = rem?.text.replace("{days}", "3") ?? "";

  return (
    <>
      <div className="page-head">
        <h1 className="h1">{t.smart}</h1>
      </div>

      <div className="cols" style={{ marginBottom: 14 }}>
        <div className="card main-col">
          <div className="row" style={{ justifyContent: "space-between", marginBottom: 14 }}>
            <span className="caps">{t.renewalReminder}</span>
            <div className="row">
              <span className={`st ${rem?.enabled ? "on" : "off"}`}>
                {rem?.enabled ? t.on : t.off}
              </span>
              <Toggle on={rem?.enabled ?? false} onChange={(v) => patch({ enabled: v })} />
            </div>
          </div>
          {rem && (
            <div className="grid" style={{ gap: 12 }}>
              <div className="row">
                <Field label={t.daysBefore}>
                  <input
                    className="input mono"
                    style={{ width: 110 }}
                    value={rem.days_before}
                    onChange={(e) => patch({ days_before: e.target.value })}
                  />
                </Field>
                <Field label={t.timeMsk}>
                  <input
                    className="input mono"
                    style={{ width: 90 }}
                    value={rem.send_time}
                    onChange={(e) => patch({ send_time: e.target.value })}
                  />
                </Field>
              </div>
              <Field label={`${t.msgText} · {days}`}>
                <textarea
                  className="input"
                  rows={3}
                  value={rem.text}
                  onChange={(e) => patch({ text: e.target.value })}
                />
              </Field>
              <label className="row" style={{ cursor: "pointer" }}>
                <Toggle on={rem.button_enabled} onChange={(v) => patch({ button_enabled: v })} />
                <span style={{ fontSize: 13 }}>{t.miniappBtn}</span>
              </label>
              {dirty && (
                <button className="btn primary" onClick={saveReminder}>
                  {t.saveApply}
                </button>
              )}
            </div>
          )}
        </div>

        <div className="card side-col" style={{ maxWidth: 420 }}>
          <div className="caps" style={{ marginBottom: 10 }}>
            {t.preview}
          </div>
          <div
            style={{
              background: "var(--panel2)",
              border: "1px solid var(--border)",
              borderRadius: 8,
              padding: 12,
              fontSize: 13.5,
              whiteSpace: "pre-wrap",
            }}
          >
            {previewText || <span className="dim">…</span>}
            {rem?.button_enabled && (
              <div
                style={{
                  marginTop: 10,
                  border: "1px solid var(--border2)",
                  borderRadius: 6,
                  textAlign: "center",
                  padding: "8px 0",
                }}
              >
                Продлить
              </div>
            )}
          </div>
        </div>
      </div>

      <div className="card">
        <div className="row" style={{ justifyContent: "space-between", marginBottom: 12 }}>
          <span className="caps">{t.holidayCalendar}</span>
          {nearest && (
            <span className="caps">
              {t.nearest}: {nearest.h.date} · {nearest.h.name}
            </span>
          )}
        </div>
        <div className="grid" style={{ gap: 10 }}>
          {(holidays.data?.items ?? []).map((h) => {
            const past = Object.keys(h.results).length > 0;
            const lastYear = past ? Object.keys(h.results).sort().at(-1) : null;
            const res = lastYear ? h.results[lastYear] : null;
            return (
              <div key={h.id} className="row" style={{ flexWrap: "wrap", fontSize: 13 }}>
                <span className="mono" style={{ width: 52 }}>
                  {h.date}
                </span>
                <span style={{ width: 190, overflow: "hidden", textOverflow: "ellipsis" }}>
                  {h.name}
                </span>
                <Seg
                  value={h.reward_type}
                  options={[
                    { id: "discount" as const, label: t.discount },
                    { id: "days" as const, label: t.rewardDays },
                    { id: "balance" as const, label: "₽" },
                  ]}
                  onChange={(reward_type) => void patchHoliday(h, { reward_type })}
                />
                <input
                  className="input num"
                  style={{ width: 74 }}
                  type="number"
                  defaultValue={h.value}
                  onBlur={(e) => {
                    const v = Number(e.target.value) || 0;
                    if (v !== h.value) void patchHoliday(h, { value: v });
                  }}
                />
                <span className="dim" style={{ flex: 1, minWidth: 120, fontSize: 12 }}>
                  {res
                    ? `${res.sent ?? 0} · +${res.conv ?? 0} продлений`
                    : `${t.planned} · ${h.send_time}`}
                </span>
                <Toggle on={h.enabled} onChange={(v) => void patchHoliday(h, { enabled: v })} />
              </div>
            );
          })}
        </div>
      </div>
    </>
  );
}
