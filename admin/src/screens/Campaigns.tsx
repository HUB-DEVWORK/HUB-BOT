/* Screen 09 — Рекламные кампании: cards with UTM links + CPA/ROI metrics. */

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, dt, money } from "../api/client";
import { Field, Modal, Toggle } from "../components/ui";
import { useApp } from "../state/app";

type Campaign = {
  id: number;
  name: string;
  start_param: string;
  link: string | null;
  is_active: boolean;
  created_at: string | null;
  cost_minor: number;
  regs: number;
  trials: number;
  paid: number;
  revenue_minor: number;
  cr_pct: number;
  cpa_minor: number | null;
  roi_pct: number | null;
  avg_check_minor: number;
};

export default function Campaigns() {
  const { t, toast } = useApp();
  const qc = useQueryClient();
  const [modal, setModal] = useState(false);
  const [name, setName] = useState("");
  const [param, setParam] = useState("");
  const [cost, setCost] = useState(0);

  const list = useQuery({
    queryKey: ["campaigns"],
    queryFn: () => api.get<{ items: Campaign[] }>("/api/admin/campaigns"),
  });

  async function create() {
    try {
      await api.post("/api/admin/campaigns", {
        name,
        start_param: param,
        cost_minor: cost * 100,
      });
      setModal(false);
      setName("");
      setParam("");
      void qc.invalidateQueries({ queryKey: ["campaigns"] });
      toast("✓");
    } catch (e) {
      toast((e as Error).message);
    }
  }

  async function toggle(c: Campaign, on: boolean) {
    await api.patch(`/api/admin/campaigns/${c.id}`, { is_active: on });
    void qc.invalidateQueries({ queryKey: ["campaigns"] });
  }

  return (
    <>
      <div className="page-head">
        <h1 className="h1">{t.campaigns}</h1>
        <div className="actions">
          <button className="btn primary" onClick={() => setModal(true)}>
            + {t.create}
          </button>
        </div>
      </div>

      <div className="grid">
        {(list.data?.items ?? []).map((c) => (
          <div key={c.id} className="card">
            <div className="row" style={{ flexWrap: "wrap" }}>
              <b>{c.name}</b>
              <span className={`st ${c.is_active ? "on" : "off"}`}>
                {c.is_active ? "● ACTIVE" : "○ OFF"}
              </span>
              <span className="mono muted" style={{ fontSize: 12 }}>
                {c.link ?? `start=${c.start_param}`}
              </span>
              <button
                className="btn secondary sm"
                onClick={() => {
                  void navigator.clipboard.writeText(c.link ?? c.start_param);
                  toast(t.copied);
                }}
              >
                {t.copy}
              </button>
              <span style={{ marginLeft: "auto" }}>
                <Toggle on={c.is_active} onChange={(v) => void toggle(c, v)} />
              </span>
            </div>
            <div className="row" style={{ gap: 26, marginTop: 14, flexWrap: "wrap" }}>
              {(
                [
                  ["Переходы", c.regs],
                  ["Триалы", c.trials],
                  ["Оплаты", c.paid],
                  ["CR", `${c.cr_pct}%`],
                  ["Выручка", money(c.revenue_minor)],
                ] as [string, string | number][]
              ).map(([k, v]) => (
                <span key={k}>
                  <div className="caps">{k}</div>
                  <div className="mono" style={{ fontSize: 17 }}>
                    {v}
                  </div>
                </span>
              ))}
            </div>
            <div
              className="row"
              style={{
                gap: 26,
                marginTop: 12,
                paddingTop: 12,
                borderTop: "1px solid var(--border)",
                flexWrap: "wrap",
                fontSize: 12.5,
              }}
            >
              <span className="muted">
                Расход <b className="mono">{money(c.cost_minor)}</b>
              </span>
              <span className="muted">
                CPA <b className="mono">{c.cpa_minor !== null ? money(c.cpa_minor) : "—"}</b>
              </span>
              <span className="muted">
                ROI <b className="mono">{c.roi_pct !== null ? `${c.roi_pct}%` : "—"}</b>
              </span>
              <span className="muted">
                Ср. чек <b className="mono">{money(c.avg_check_minor)}</b>
              </span>
              <span className="dim" style={{ marginLeft: "auto" }}>
                {dt(c.created_at)}
              </span>
            </div>
          </div>
        ))}
        {list.data && list.data.items.length === 0 && (
          <div className="card dim">—</div>
        )}
      </div>

      {modal && (
        <Modal title={t.campaigns} onClose={() => setModal(false)}>
          <div className="grid" style={{ gap: 12 }}>
            <Field label="Название">
              <input className="input" value={name} onChange={(e) => setName(e.target.value)} />
            </Field>
            <Field label="start=<param>">
              <input
                className="input mono"
                value={param}
                placeholder="promo_tg_july"
                onChange={(e) => setParam(e.target.value)}
              />
            </Field>
            <Field label="Расход ₽">
              <input
                className="input num"
                type="number"
                value={cost}
                onChange={(e) => setCost(Number(e.target.value) || 0)}
              />
            </Field>
            <div className="row" style={{ justifyContent: "flex-end" }}>
              <button className="btn secondary" onClick={() => setModal(false)}>
                {t.cancel}
              </button>
              <button className="btn primary" disabled={!name || !param} onClick={create}>
                {t.create}
              </button>
            </div>
          </div>
        </Modal>
      )}
    </>
  );
}
