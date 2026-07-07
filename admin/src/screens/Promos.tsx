/* Screen 04 — Акции и промокоды: table + create modal + groups + referral. */

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, dt, money } from "../api/client";
import { Field, Modal, Prog, Seg, Toggle } from "../components/ui";
import { useApp } from "../state/app";

type Promo = {
  id: number;
  code: string;
  reward_type: string;
  reward_value: number;
  used: number;
  max_activations: number | null;
  expires_at: string | null;
  is_active: boolean;
};
type Group = {
  id: number;
  name: string;
  server_discount_pct: number;
  auto_assign_total_spent_minor: number | null;
  members: number;
};
type Referral = {
  enabled: boolean;
  bonus_minor: number;
  percent: number;
  invited_total: number;
  paid_out_minor: number;
};

function genCode(): string {
  const abc = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
  return Array.from({ length: 8 }, () => abc[Math.floor(Math.random() * abc.length)]).join("");
}

export default function Promos() {
  const { t, toast, confirm } = useApp();
  const qc = useQueryClient();
  const [modal, setModal] = useState(false);
  const [rewardType, setRewardType] = useState<"balance" | "days" | "trial" | "group">("balance");
  const [code, setCode] = useState("");
  const [value, setValue] = useState(100);
  const [limit, setLimit] = useState(0);

  const promos = useQuery({
    queryKey: ["promocodes"],
    queryFn: () =>
      api.get<{ items: Promo[]; total_activations: number }>("/api/admin/promocodes"),
  });
  const groups = useQuery({
    queryKey: ["promogroups"],
    queryFn: () => api.get<{ items: Group[] }>("/api/admin/promogroups"),
  });
  const referral = useQuery({
    queryKey: ["referral"],
    queryFn: () => api.get<Referral>("/api/admin/referral"),
  });

  async function bulkGifts() {
    const countRaw = window.prompt(t.bulkCountQ, "50");
    if (!countRaw) return;
    const daysRaw = window.prompt(t.bulkDaysQ, "7");
    if (!daysRaw) return;
    const count = Math.max(1, Math.min(1000, parseInt(countRaw, 10) || 0));
    const days = Math.max(1, parseInt(daysRaw, 10) || 0);
    try {
      const r = await api.post<{ count: number; items: { code: string; gift_link: string | null }[] }>(
        "/api/admin/promocodes/bulk",
        { count, reward_type: "days", reward_value: days, prefix: "GIFT" },
      );
      const lines = ["code,gift_link"].concat(r.items.map((i) => `${i.code},${i.gift_link ?? ""}`));
      const blob = new Blob(["\ufeff" + lines.join("\n")], { type: "text/csv;charset=utf-8" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `gift-codes-${new Date().toISOString().slice(0, 10)}.csv`;
      a.click();
      URL.revokeObjectURL(a.href);
      void qc.invalidateQueries({ queryKey: ["promos"] });
      toast(`${t.bulkDone}: ${r.count}`);
    } catch (e) {
      toast((e as Error).message);
    }
  }

  async function create() {
    try {
      await api.post("/api/admin/promocodes", {
        code,
        reward_type: rewardType,
        reward_value: rewardType === "balance" ? value * 100 : value,
        max_activations: limit || null,
      });
      setModal(false);
      setCode("");
      void qc.invalidateQueries({ queryKey: ["promocodes"] });
      toast("✓");
    } catch (e) {
      toast((e as Error).message);
    }
  }

  async function togglePromo(p: Promo, on: boolean) {
    await api.patch(`/api/admin/promocodes/${p.id}`, { is_active: on });
    void qc.invalidateQueries({ queryKey: ["promocodes"] });
  }

  async function removePromo(p: Promo) {
    if (!(await confirm(t.deletePromoConfirm))) return;
    await api.del(`/api/admin/promocodes/${p.id}`);
    void qc.invalidateQueries({ queryKey: ["promocodes"] });
    toast("✕ " + p.code);
  }

  const rewardLabel: Record<string, string> = {
    balance: t.rewardBalance,
    days: t.rewardDays,
    trial: t.rewardTrial,
    group: t.rewardGroup,
  };
  const r = referral.data;

  return (
    <>
      <div className="page-head">
        <div>
          <h1 className="h1">{t.promos}</h1>
          <div className="caps sub">
            {t.activations}: {promos.data?.total_activations ?? "…"}
          </div>
        </div>
        <div className="actions">
          <button className="btn secondary" onClick={() => void bulkGifts()}>
            {t.bulkGifts}
          </button>
          <button className="btn primary" onClick={() => setModal(true)}>
            {t.createPromo}
          </button>
        </div>
      </div>

      <div className="tbl" style={{ marginBottom: 16 }}>
        <div className="tr head" style={{ gridTemplateColumns: "1.2fr 0.9fr 0.9fr 1.4fr 1fr auto auto" }}>
          <span>{t.code}</span>
          <span>{t.reward}</span>
          <span>{t.nominal}</span>
          <span>{t.activations}</span>
          <span>{t.validTill}</span>
          <span />
          <span />
        </div>
        {(promos.data?.items ?? []).map((p) => (
          <div key={p.id} className="tr" style={{ gridTemplateColumns: "1.2fr 0.9fr 0.9fr 1.4fr 1fr auto auto" }}>
            <span className="mono" style={{ fontWeight: 700 }}>
              {p.code}
            </span>
            <span>
              <span className="cap-pill">{rewardLabel[p.reward_type] ?? p.reward_type}</span>
            </span>
            <span className="mono">
              {p.reward_type === "balance" ? money(p.reward_value) : p.reward_value}
            </span>
            <span>
              <div className="mono" style={{ fontSize: 11, marginBottom: 3 }}>
                {p.used} / {p.max_activations ?? "∞"}
              </div>
              <Prog pct={p.max_activations ? (p.used / p.max_activations) * 100 : 0} />
            </span>
            <span className="muted">{p.expires_at ? dt(p.expires_at) : "—"}</span>
            <Toggle on={p.is_active} onChange={(v) => void togglePromo(p, v)} />
            <button className="btn danger sm" onClick={() => void removePromo(p)}>
              ✕
            </button>
          </div>
        ))}
        {promos.data && promos.data.items.length === 0 && <div className="tr dim">—</div>}
      </div>

      <div className="cols">
        <div className="card main-col">
          <div className="caps" style={{ marginBottom: 12 }}>
            {t.promoGroups}
          </div>
          <div className="grid" style={{ gap: 10 }}>
            {(groups.data?.items ?? []).map((g) => (
              <div key={g.id} className="row" style={{ fontSize: 13 }}>
                <span className="mono" style={{ fontWeight: 700, width: 90 }}>
                  {g.name}
                </span>
                <span className="muted">−{g.server_discount_pct}%</span>
                <span className="dim">
                  {g.auto_assign_total_spent_minor
                    ? `авто от ${money(g.auto_assign_total_spent_minor)}`
                    : "вручную"}
                </span>
                <span className="mono" style={{ marginLeft: "auto" }}>
                  {g.members}
                </span>
              </div>
            ))}
            {groups.data && groups.data.items.length === 0 && <span className="dim">—</span>}
          </div>
        </div>

        <div className="card side-col">
          <div className="caps" style={{ marginBottom: 12 }}>
            {t.referralProg}
          </div>
          <div className="grid" style={{ gap: 9, fontSize: 13 }}>
            <div className="row" style={{ justifyContent: "space-between" }}>
              <span className="muted">Статус</span>
              <span className={`st ${r?.enabled ? "on" : "off"}`}>{r?.enabled ? t.on : t.off}</span>
            </div>
            <div className="row" style={{ justifyContent: "space-between" }}>
              <span className="muted">Бонус</span>
              <span className="mono">{r ? money(r.bonus_minor) : "…"}</span>
            </div>
            <div className="row" style={{ justifyContent: "space-between" }}>
              <span className="muted">%</span>
              <span className="mono">{r?.percent ?? "…"}%</span>
            </div>
            <div className="row" style={{ justifyContent: "space-between" }}>
              <span className="muted">Приглашено</span>
              <span className="mono">{r?.invited_total ?? "…"}</span>
            </div>
            <div className="row" style={{ justifyContent: "space-between" }}>
              <span className="muted">Выплачено</span>
              <span className="mono">{r ? money(r.paid_out_minor) : "…"}</span>
            </div>
          </div>
        </div>
      </div>

      {modal && (
        <Modal title={t.createPromo} onClose={() => setModal(false)}>
          <div className="grid" style={{ gap: 12 }}>
            <Field label={t.reward}>
              <Seg
                value={rewardType}
                options={[
                  { id: "balance" as const, label: t.rewardBalance },
                  { id: "days" as const, label: t.rewardDays },
                  { id: "trial" as const, label: t.rewardTrial },
                  { id: "group" as const, label: t.rewardGroup },
                ]}
                onChange={setRewardType}
              />
            </Field>
            <Field label={t.code}>
              <div className="row">
                <input
                  className="input mono"
                  style={{ flex: 1, textTransform: "uppercase" }}
                  value={code}
                  placeholder="AUTO"
                  onChange={(e) => setCode(e.target.value.toUpperCase())}
                />
                <button className="btn secondary sm" onClick={() => setCode(genCode())}>
                  GEN
                </button>
              </div>
            </Field>
            <div className="row">
              <Field label={rewardType === "balance" ? `${t.nominal} ₽` : t.nominal}>
                <input
                  className="input num"
                  type="number"
                  value={value}
                  onChange={(e) => setValue(Number(e.target.value) || 0)}
                />
              </Field>
              <Field label={t.limit0}>
                <input
                  className="input num"
                  type="number"
                  value={limit}
                  onChange={(e) => setLimit(Number(e.target.value) || 0)}
                />
              </Field>
            </div>
            <div className="row" style={{ justifyContent: "flex-end" }}>
              <button className="btn secondary" onClick={() => setModal(false)}>
                {t.cancel}
              </button>
              <button className="btn primary" onClick={create}>
                {t.create}
              </button>
            </div>
          </div>
        </Modal>
      )}
    </>
  );
}
