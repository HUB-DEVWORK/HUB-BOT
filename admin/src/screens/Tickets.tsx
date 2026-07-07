/* Screen 11 — Тикеты: support channels config + ticket list + chat. */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, dtTime } from "../api/client";
import { Toggle } from "../components/ui";
import { useApp } from "../state/app";

type TicketRow = {
  id: number;
  username: string | null;
  subject: string;
  status: "open" | "waiting" | "closed";
  messages: number;
  updated_at: string | null;
};
type TicketDetail = {
  id: number;
  subject: string;
  status: string;
  user: { username: string | null };
  messages: { id: number; author: "user" | "admin"; text: string; at: string | null }[];
};
type Channels = { mode: string; redirect_username: string };

const ST: Record<string, [string, string]> = {
  open: ["●", "on"],
  waiting: ["◐", "mid"],
  closed: ["○", "off"],
};

export default function Tickets() {
  const { t, toast } = useApp();
  const qc = useQueryClient();
  const [selId, setSelId] = useState<number | null>(null);
  const [reply, setReply] = useState("");
  const [redirect, setRedirect] = useState<string | null>(null);

  const channels = useQuery({
    queryKey: ["support-channels"],
    queryFn: () => api.get<Channels>("/api/admin/support-channels"),
  });
  const tickets = useQuery({
    queryKey: ["tickets"],
    queryFn: () => api.get<{ items: TicketRow[]; open_count: number }>("/api/admin/tickets"),
    refetchInterval: 30_000,
  });
  const detail = useQuery({
    queryKey: ["ticket", selId],
    queryFn: () => api.get<TicketDetail>(`/api/admin/tickets/${selId}`),
    enabled: selId !== null,
  });

  const sendReply = useMutation({
    mutationFn: () => api.post(`/api/admin/tickets/${selId}/reply`, { text: reply }),
    onSuccess: () => {
      setReply("");
      void qc.invalidateQueries({ queryKey: ["ticket", selId] });
      void qc.invalidateQueries({ queryKey: ["tickets"] });
      toast("✓");
    },
    onError: (e) => toast(e.message),
  });

  async function setStatus(status: string) {
    await api.patch(`/api/admin/tickets/${selId}/status`, { status });
    void qc.invalidateQueries({ queryKey: ["ticket", selId] });
    void qc.invalidateQueries({ queryKey: ["tickets"] });
  }

  const ch = channels.data;
  const mode = ch?.mode ?? "tickets";

  async function saveChannels() {
    try {
      await api.patch("/api/admin/support-channels", {
        mode,
        redirect_username: redirect ?? ch?.redirect_username ?? "",
      });
      void qc.invalidateQueries({ queryKey: ["support-channels"] });
      toast(t.saved);
    } catch (e) {
      toast((e as Error).message);
    }
  }

  async function setMode(m: string) {
    await api.patch("/api/admin/support-channels", { mode: m });
    void qc.invalidateQueries({ queryKey: ["support-channels"] });
    toast(t.saved);
  }

  const d = detail.data;

  return (
    <>
      <div className="page-head">
        <h1 className="h1">{t.tickets}</h1>
        <div className="actions">
          <button className="btn secondary" onClick={saveChannels}>
            {t.saveChannels}
          </button>
        </div>
      </div>

      {/* support channels */}
      <div className="kpis" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))" }}>
        <div className="card">
          <div className="row" style={{ justifyContent: "space-between" }}>
            <span className="caps">01 · {t.redirectAcc}</span>
            <Toggle on={mode === "redirect"} onChange={() => void setMode(mode === "redirect" ? "tickets" : "redirect")} />
          </div>
          <input
            className="input"
            style={{ width: "100%", marginTop: 10 }}
            placeholder="@support_account"
            value={redirect ?? ch?.redirect_username ?? ""}
            onChange={(e) => setRedirect(e.target.value)}
          />
        </div>
        <div className="card">
          <div className="row" style={{ justifyContent: "space-between" }}>
            <span className="caps">02 · {t.miniappChat}</span>
            <Toggle on={mode === "miniapp"} onChange={() => void setMode(mode === "miniapp" ? "tickets" : "miniapp")} />
          </div>
          <div className="dim" style={{ fontSize: 12, marginTop: 12 }}>
            Тикеты в мини-аппе + этот раздел
          </div>
        </div>
      </div>

      <div className="cols">
        {/* ticket list */}
        <div className="card" style={{ flex: "1 1 300px", padding: 0, overflow: "hidden" }}>
          {(tickets.data?.items ?? []).map((tk) => {
            const [g, cls] = ST[tk.status];
            return (
              <div
                key={tk.id}
                className="tr click"
                style={{ gridTemplateColumns: "auto 1fr auto" }}
                onClick={() => setSelId(tk.id)}
              >
                <span className={`st ${cls}`}>{g}</span>
                <span style={{ minWidth: 0 }}>
                  <div style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    <span className="mono dim">#{tk.id}</span>{" "}
                    {tk.username ? `@${tk.username}` : "—"} · {tk.subject}
                  </div>
                  <div className="dim" style={{ fontSize: 11.5 }}>
                    {tk.messages} · {dtTime(tk.updated_at)}
                  </div>
                </span>
              </div>
            );
          })}
          {tickets.data && tickets.data.items.length === 0 && (
            <div className="tr dim">—</div>
          )}
        </div>

        {/* chat */}
        <div className="card" style={{ flex: "2 1 400px", display: "flex", flexDirection: "column", minHeight: 420 }}>
          {d ? (
            <>
              <div className="row" style={{ justifyContent: "space-between", marginBottom: 12 }}>
                <span>
                  <b className="mono">#{d.id}</b> · {d.subject}{" "}
                  <span className={`st ${ST[d.status]?.[1] ?? "off"}`}>
                    {d.status === "open" ? t.openSt : d.status === "waiting" ? t.waitSt : t.closedSt}
                  </span>
                </span>
                {d.status !== "closed" ? (
                  <button className="btn secondary sm" onClick={() => void setStatus("closed")}>
                    {t.closeTicket}
                  </button>
                ) : (
                  <button className="btn secondary sm" onClick={() => void setStatus("open")}>
                    {t.reopenTicket}
                  </button>
                )}
              </div>
              <div className="grid" style={{ gap: 8, flex: 1, overflowY: "auto", marginBottom: 12 }}>
                {d.messages.map((m) => (
                  <div
                    key={m.id}
                    style={{
                      maxWidth: "78%",
                      alignSelf: m.author === "admin" ? "flex-end" : "flex-start",
                      background: m.author === "admin" ? "var(--text)" : "var(--panel2)",
                      color: m.author === "admin" ? "var(--inv)" : "var(--text)",
                      border: m.author === "admin" ? "0" : "1px solid var(--border)",
                      borderRadius: 6,
                      padding: "8px 12px",
                      fontSize: 13,
                    }}
                  >
                    <div style={{ whiteSpace: "pre-wrap" }}>{m.text}</div>
                    <div
                      className="mono"
                      style={{ fontSize: 9.5, opacity: 0.6, marginTop: 4, textAlign: "right" }}
                    >
                      {dtTime(m.at)}
                    </div>
                  </div>
                ))}
              </div>
              <div className="row">
                <input
                  className="input"
                  style={{ flex: 1 }}
                  placeholder={t.reply + "…"}
                  value={reply}
                  onChange={(e) => setReply(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && reply.trim()) sendReply.mutate();
                  }}
                />
                <button
                  className="btn primary"
                  disabled={!reply.trim() || sendReply.isPending}
                  onClick={() => sendReply.mutate()}
                >
                  {t.reply}
                </button>
              </div>
            </>
          ) : (
            <span className="dim">← {t.tickets}</span>
          )}
        </div>
      </div>
    </>
  );
}
