/* Screen 03 — Тарифы: sales-mode toggle, ready plans grid, constructor tab. */

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api, bytesFmt, money } from "../api/client";
import { Field, Modal, Seg, Toggle } from "../components/ui";
import { useApp } from "../state/app";

type Duration = { id?: number; days: number; prices: Record<string, number> };
type Plan = {
  id: number;
  name: string;
  description: string | null;
  traffic_limit_bytes: number | null;
  device_limit: number | null;
  is_active: boolean;
  internal_squads: string[];
  durations: Duration[];
  sales: number;
};
type PlansResp = { mode: string; items: Plan[] };
type Constructor = {
  periods: { id: number; days: number; price_minor: number; is_active: boolean }[];
  traffic_packs: { id: number; gb: number; price_minor: number; is_active: boolean }[];
  extra_device_price_minor: number;
  max_devices: number;
  trial_enabled: boolean;
};

type PlanDraft = {
  id?: number;
  name: string;
  description: string;
  traffic_limit_gb: number;
  device_limit: number;
  durations: { days: number; price_minor: number }[];
  internal_squads: string[];
};
type Squad = { id: number; name: string; uuid: string };

export default function Tariffs() {
  const { t, toast, confirm } = useApp();
  const qc = useQueryClient();
  const [tab, setTab] = useState<"plans" | "constructor">("plans");
  const [draft, setDraft] = useState<PlanDraft | null>(null);
  const [ctor, setCtor] = useState<Constructor | null>(null);
  const [ctorDirty, setCtorDirty] = useState(false);

  const plans = useQuery({
    queryKey: ["plans"],
    queryFn: () => api.get<PlansResp>("/api/admin/plans"),
  });
  const constructor = useQuery({
    queryKey: ["constructor"],
    queryFn: () => api.get<Constructor>("/api/admin/constructor"),
  });
  const servers = useQuery({
    queryKey: ["servers"],
    queryFn: () => api.get<{ squads: Squad[] }>("/api/admin/servers"),
  });
  const squads = servers.data?.squads ?? [];

  useEffect(() => {
    if (constructor.data && !ctorDirty) setCtor(constructor.data);
  }, [constructor.data, ctorDirty]);

  const mode = plans.data?.mode ?? "plans";

  async function setMode(m: string) {
    await api.patch("/api/admin/settings", { changes: { SALES_MODE: m } });
    void qc.invalidateQueries({ queryKey: ["plans"] });
    toast(t.applied);
  }

  async function savePlan() {
    if (!draft) return;
    try {
      if (draft.id) {
        await api.patch(`/api/admin/plans/${draft.id}`, {
          name: draft.name,
          description: draft.description,
          traffic_limit_gb: draft.traffic_limit_gb,
          device_limit: draft.device_limit,
          durations: draft.durations,
          internal_squads: draft.internal_squads,
        });
      } else {
        await api.post("/api/admin/plans", {
          name: draft.name,
          description: draft.description,
          traffic_limit_gb: draft.traffic_limit_gb,
          device_limit: draft.device_limit,
          durations: draft.durations,
          internal_squads: draft.internal_squads,
        });
      }
      setDraft(null);
      void qc.invalidateQueries({ queryKey: ["plans"] });
      toast(t.saved);
    } catch (e) {
      toast((e as Error).message);
    }
  }

  async function togglePlan(p: Plan, on: boolean) {
    await api.patch(`/api/admin/plans/${p.id}`, { is_active: on });
    void qc.invalidateQueries({ queryKey: ["plans"] });
    toast(on ? t.on : t.off);
  }

  async function removePlan(p: Plan) {
    if (!(await confirm(t.deleteTariffConfirm))) return;
    try {
      await api.del(`/api/admin/plans/${p.id}`);
      void qc.invalidateQueries({ queryKey: ["plans"] });
      toast("✕ " + p.name);
    } catch (e) {
      // 409 "plan has subscriptions" surfaces here as a human-readable message.
      toast((e as Error).message);
    }
  }

  async function saveCtor() {
    if (!ctor) return;
    try {
      await api.put("/api/admin/constructor", {
        periods: ctor.periods.map((p) => ({
          days: p.days,
          price_minor: p.price_minor,
          is_active: p.is_active,
        })),
        traffic_packs: ctor.traffic_packs.map((p) => ({
          gb: p.gb,
          price_minor: p.price_minor,
          is_active: p.is_active,
        })),
      });
      setCtorDirty(false);
      void qc.invalidateQueries({ queryKey: ["constructor"] });
      toast(t.applied);
    } catch (e) {
      toast((e as Error).message);
    }
  }

  function patchCtor(update: (c: Constructor) => Constructor) {
    setCtor((c) => (c ? update(c) : c));
    setCtorDirty(true);
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1 className="h1">{t.tariffs}</h1>
          <div className="row sub">
            <span className="caps">{t.botSells}:</span>
            <Seg
              value={mode}
              options={[
                { id: "plans", label: t.plansMode },
                { id: "constructor", label: t.constructorMode },
              ]}
              onChange={(m) => void setMode(m)}
            />
          </div>
        </div>
        <div className="actions">
          <button
            className="btn primary"
            onClick={() =>
              setDraft({
                name: "",
                description: "",
                traffic_limit_gb: 100,
                device_limit: 3,
                durations: [{ days: 30, price_minor: 19900 }],
                internal_squads: [],
              })
            }
          >
            {t.newPlan}
          </button>
        </div>
      </div>

      <div style={{ marginBottom: 16 }}>
        <Seg
          value={tab}
          options={[
            { id: "plans" as const, label: t.readyPlans },
            { id: "constructor" as const, label: t.constructorMode },
          ]}
          onChange={setTab}
        />
      </div>

      {tab === "plans" && (
        <div className="kpis" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))" }}>
          {(plans.data?.items ?? []).map((p) => (
            <div key={p.id} className="card">
              <div className="row" style={{ justifyContent: "space-between" }}>
                <b>{p.name}</b>
                <Toggle on={p.is_active} onChange={(v) => void togglePlan(p, v)} />
              </div>
              <div className="mono" style={{ fontSize: 22, margin: "10px 0 6px" }}>
                {p.durations[0] ? money(p.durations[0].prices.RUB ?? 0) : "—"}
                <span className="dim" style={{ fontSize: 12 }}>
                  {" "}
                  / {p.durations[0]?.days ?? "—"} {t.days}
                </span>
              </div>
              <div className="muted" style={{ fontSize: 12.5, marginBottom: 10 }}>
                {p.traffic_limit_bytes ? bytesFmt(p.traffic_limit_bytes) : t.unlimited} ·{" "}
                {p.device_limit ?? "∞"} dev · {p.sales} {t.sales}
              </div>
              <div className="row" style={{ marginTop: 4 }}>
                <button
                  className="btn secondary sm"
                  onClick={() =>
                    setDraft({
                      id: p.id,
                      name: p.name,
                      description: p.description ?? "",
                      traffic_limit_gb: p.traffic_limit_bytes
                        ? Math.round(p.traffic_limit_bytes / 1024 ** 3)
                        : 0,
                      device_limit: p.device_limit ?? 3,
                      durations: p.durations.map((d) => ({
                        days: d.days,
                        price_minor: d.prices.RUB ?? 0,
                      })),
                      internal_squads: p.internal_squads ?? [],
                    })
                  }
                >
                  {t.edit}
                </button>
                <button
                  className="btn danger sm"
                  title={t.delete}
                  onClick={() => void removePlan(p)}
                >
                  🗑
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {tab === "constructor" && ctor && (
        <div className="cols">
          <div className="card" style={{ flex: "1 1 320px" }}>
            <div className="caps" style={{ marginBottom: 12 }}>
              {t.periods}
            </div>
            <div className="grid" style={{ gap: 8 }}>
              {ctor.periods.map((p, i) => (
                <div key={i} className="row">
                  <span className="mono" style={{ width: 70 }}>
                    {p.days} {t.days}
                  </span>
                  <input
                    className="input num"
                    style={{ width: 110 }}
                    value={p.price_minor / 100}
                    type="number"
                    onChange={(e) =>
                      patchCtor((c) => ({
                        ...c,
                        periods: c.periods.map((x, j) =>
                          j === i ? { ...x, price_minor: Math.round(Number(e.target.value) * 100) } : x,
                        ),
                      }))
                    }
                  />
                  <span className="dim">₽</span>
                  <span style={{ marginLeft: "auto" }}>
                    <Toggle
                      on={p.is_active}
                      onChange={(v) =>
                        patchCtor((c) => ({
                          ...c,
                          periods: c.periods.map((x, j) => (j === i ? { ...x, is_active: v } : x)),
                        }))
                      }
                    />
                  </span>
                </div>
              ))}
            </div>
          </div>
          <div className="card" style={{ flex: "1 1 320px" }}>
            <div className="caps" style={{ marginBottom: 12 }}>
              {t.trafficPacks}
            </div>
            <div className="grid" style={{ gap: 8 }}>
              {ctor.traffic_packs.map((p, i) => (
                <div key={i} className="row">
                  <span className="mono" style={{ width: 70 }}>
                    {p.gb === 0 ? "∞" : `${p.gb} ГБ`}
                  </span>
                  <input
                    className="input num"
                    style={{ width: 110 }}
                    value={p.price_minor / 100}
                    type="number"
                    onChange={(e) =>
                      patchCtor((c) => ({
                        ...c,
                        traffic_packs: c.traffic_packs.map((x, j) =>
                          j === i ? { ...x, price_minor: Math.round(Number(e.target.value) * 100) } : x,
                        ),
                      }))
                    }
                  />
                  <span className="dim">₽</span>
                  <span style={{ marginLeft: "auto" }}>
                    <Toggle
                      on={p.is_active}
                      onChange={(v) =>
                        patchCtor((c) => ({
                          ...c,
                          traffic_packs: c.traffic_packs.map((x, j) =>
                            j === i ? { ...x, is_active: v } : x,
                          ),
                        }))
                      }
                    />
                  </span>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {tab === "constructor" && ctorDirty && (
        <div className="row" style={{ marginTop: 14, justifyContent: "space-between" }}>
          <span className="cap-pill">{t.unsavedChanges}</span>
          <button className="btn primary" onClick={saveCtor}>
            {t.save}
          </button>
        </div>
      )}

      {draft && (
        <Modal title={draft.id ? t.edit : t.newPlan} onClose={() => setDraft(null)}>
          <div className="grid" style={{ gap: 12 }}>
            <Field label={t.planName}>
              <input
                className="input"
                value={draft.name}
                onChange={(e) => setDraft({ ...draft, name: e.target.value })}
              />
            </Field>
            <Field label="Описание">
              <input
                className="input"
                value={draft.description}
                onChange={(e) => setDraft({ ...draft, description: e.target.value })}
              />
            </Field>
            <div className="row">
              <Field label="Трафик ГБ (0=∞)">
                <input
                  className="input num"
                  type="number"
                  value={draft.traffic_limit_gb}
                  onChange={(e) =>
                    setDraft({ ...draft, traffic_limit_gb: Number(e.target.value) || 0 })
                  }
                />
              </Field>
              <Field label={t.devices}>
                <input
                  className="input num"
                  type="number"
                  value={draft.device_limit}
                  onChange={(e) => setDraft({ ...draft, device_limit: Number(e.target.value) || 1 })}
                />
              </Field>
            </div>
            <div>
              <div className="caps" style={{ marginBottom: 6 }}>{t.planSquads}</div>
              {squads.length === 0 ? (
                <div className="dim" style={{ fontSize: 12 }}>{t.planSquadsEmpty}</div>
              ) : (
                <div className="grid" style={{ gap: 6 }}>
                  {squads.map((sq) => {
                    const on = draft.internal_squads.includes(sq.uuid);
                    return (
                      <label key={sq.uuid} className="row" style={{ cursor: "pointer", fontSize: 13 }}>
                        <input
                          type="checkbox"
                          checked={on}
                          onChange={() =>
                            setDraft({
                              ...draft,
                              internal_squads: on
                                ? draft.internal_squads.filter((u) => u !== sq.uuid)
                                : [...draft.internal_squads, sq.uuid],
                            })
                          }
                        />
                        <span>{sq.name}</span>
                        <span className="mono dim" style={{ fontSize: 10 }}>{sq.uuid.slice(0, 8)}</span>
                      </label>
                    );
                  })}
                </div>
              )}
            </div>
            <span className="caps">{t.periods}</span>
            {draft.durations.map((d, i) => (
              <div key={i} className="row">
                <input
                  className="input num"
                  style={{ width: 90 }}
                  type="number"
                  value={d.days}
                  onChange={(e) => {
                    const days = Number(e.target.value) || 1;
                    setDraft({
                      ...draft,
                      durations: draft.durations.map((x, j) => (j === i ? { ...x, days } : x)),
                    });
                  }}
                />
                <span className="dim">{t.days}</span>
                <input
                  className="input num"
                  style={{ width: 110 }}
                  type="number"
                  value={d.price_minor / 100}
                  onChange={(e) => {
                    const price_minor = Math.round(Number(e.target.value) * 100);
                    setDraft({
                      ...draft,
                      durations: draft.durations.map((x, j) =>
                        j === i ? { ...x, price_minor } : x,
                      ),
                    });
                  }}
                />
                <span className="dim">₽</span>
                <button
                  className="btn danger sm"
                  onClick={() =>
                    setDraft({ ...draft, durations: draft.durations.filter((_, j) => j !== i) })
                  }
                >
                  ✕
                </button>
              </div>
            ))}
            <button
              className="btn secondary sm"
              onClick={() =>
                setDraft({
                  ...draft,
                  durations: [...draft.durations, { days: 90, price_minor: 49900 }],
                })
              }
            >
              + {t.periods}
            </button>
            <div className="row" style={{ justifyContent: "flex-end" }}>
              <button className="btn secondary" onClick={() => setDraft(null)}>
                {t.cancel}
              </button>
              <button className="btn primary" disabled={!draft.name} onClick={savePlan}>
                {t.save}
              </button>
            </div>
          </div>
        </Modal>
      )}
    </>
  );
}
