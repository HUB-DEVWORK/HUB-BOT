/* Screen 05 — Конструктор кнопок бота: tree + editor + live chat preview. */

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";

import { api, getToken } from "../api/client";
import { Field, Seg, Toggle } from "../components/ui";
import { useApp } from "../state/app";

/* Editable bot texts: the main-menu greeting and the «Личный кабинет» templates. Placeholder
   chips insert live-data tokens ({имя}, {баланс}, …) at the cursor of the last-focused field. */
type Texts = {
  main_menu: string;
  cabinet: string;
  cabinet_sub_active: string;
  cabinet_sub_inactive: string;
  placeholders: { cabinet: string[]; sub: string[] };
  defaults: { cabinet: string; cabinet_sub_active: string; cabinet_sub_inactive: string };
};
type TextField = "main_menu" | "cabinet" | "cabinet_sub_active" | "cabinet_sub_inactive";

function BotTextsCard() {
  const { t, toast } = useApp();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["bot-texts"], queryFn: () => api.get<Texts>("/api/admin/bot-menu/texts") });
  const [draft, setDraft] = useState<Partial<Record<TextField, string>>>({});
  const refs = useRef<Record<string, HTMLTextAreaElement | null>>({});
  const focused = useRef<TextField>("cabinet");

  const val = (f: TextField): string => draft[f] ?? (q.data ? (q.data[f] as string) : "");
  const set = (f: TextField, v: string) => setDraft((d) => ({ ...d, [f]: v }));

  function insert(token: string) {
    const f = focused.current;
    const el = refs.current[f];
    const cur = val(f);
    if (el && document.activeElement === el) {
      const s = el.selectionStart ?? cur.length;
      const e = el.selectionEnd ?? cur.length;
      set(f, cur.slice(0, s) + token + cur.slice(e));
      requestAnimationFrame(() => { el.focus(); el.setSelectionRange(s + token.length, s + token.length); });
    } else {
      set(f, cur + token);
    }
  }

  async function save() {
    try {
      await api.put("/api/admin/bot-menu/texts", {
        main_menu: val("main_menu"),
        cabinet: val("cabinet"),
        cabinet_sub_active: val("cabinet_sub_active"),
        cabinet_sub_inactive: val("cabinet_sub_inactive"),
      });
      setDraft({});
      void qc.invalidateQueries({ queryKey: ["bot-texts"] });
      toast(t.saved);
    } catch (e) {
      toast((e as Error).message);
    }
  }

  const chip = { border: "1px solid var(--border2)", borderRadius: 20, padding: "3px 9px", fontSize: 12, cursor: "pointer", background: "var(--panel)", color: "var(--text)" } as const;
  function Area({ field, label, rows = 4 }: { field: TextField; label: string; rows?: number }) {
    return (
      <div>
        <div className="caps" style={{ marginBottom: 4 }}>{label}</div>
        <textarea
          ref={(el) => { refs.current[field] = el; }}
          className="input"
          style={{ width: "100%", minHeight: rows * 22, fontFamily: "inherit", resize: "vertical" }}
          value={val(field)}
          onFocus={() => { focused.current = field; }}
          onChange={(e) => set(field, e.target.value)}
        />
      </div>
    );
  }

  if (!q.data) return null;
  return (
    <div className="card" style={{ marginTop: 16 }}>
      <div className="row" style={{ justifyContent: "space-between", marginBottom: 10 }}>
        <div className="caps">{t.botTextsTitle}</div>
        <button className="btn primary sm" onClick={save}>{t.saveApply}</button>
      </div>
      <div className="grid" style={{ gap: 16, maxWidth: 720 }}>
        <Area field="main_menu" label={t.mainMenuText} rows={4} />
        <Area field="cabinet" label={t.cabinetText} rows={5} />
        <div className="row" style={{ flexWrap: "wrap", gap: 6 }}>
          {q.data.placeholders.cabinet.map((p) => (
            <span key={p} style={chip} onMouseDown={(e) => { e.preventDefault(); insert(p); }}>{p}</span>
          ))}
          <button style={{ ...chip, borderStyle: "dashed" }} onClick={() => set("cabinet", q.data!.defaults.cabinet)}>↺ {t.reset}</button>
        </div>
        <Area field="cabinet_sub_active" label={t.cabinetSubActive} rows={4} />
        <Area field="cabinet_sub_inactive" label={t.cabinetSubInactive} rows={2} />
        <div className="row" style={{ flexWrap: "wrap", gap: 6 }}>
          {q.data.placeholders.sub.map((p) => (
            <span key={p} style={chip} onMouseDown={(e) => { e.preventDefault(); insert(p); }}>{p}</span>
          ))}
          <button style={{ ...chip, borderStyle: "dashed" }} onClick={() => { set("cabinet_sub_active", q.data!.defaults.cabinet_sub_active); set("cabinet_sub_inactive", q.data!.defaults.cabinet_sub_inactive); }}>↺ {t.reset}</button>
        </div>
        <div className="muted" style={{ fontSize: 12 }}>{t.botTextsHint}</div>
      </div>
    </div>
  );
}

/* Buttons of the built-in «Личный кабинет» screen — a separate, fixed catalogue (not the
   free-form tree). The owner toggles which show and reorders them; saved to CABINET_BUTTONS. */
type CabBtn = { key: string; label: string; enabled: boolean; gated: boolean };
function CabinetButtonsCard() {
  const { t, toast } = useApp();
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["cabinet-buttons"],
    queryFn: () => api.get<{ buttons: CabBtn[] }>("/api/admin/bot-menu/cabinet"),
  });
  const [items, setItems] = useState<CabBtn[] | null>(null);
  const list = items ?? q.data?.buttons ?? [];

  function update(next: CabBtn[]) {
    setItems(next);
  }
  function move(i: number, dir: -1 | 1) {
    const j = i + dir;
    if (j < 0 || j >= list.length) return;
    const next = [...list];
    [next[i], next[j]] = [next[j], next[i]];
    update(next);
  }
  async function save() {
    const order = list.filter((b) => b.enabled).map((b) => b.key);
    try {
      await api.put("/api/admin/bot-menu/cabinet", { order });
      setItems(null);
      void qc.invalidateQueries({ queryKey: ["cabinet-buttons"] });
      toast(t.saved);
    } catch (e) {
      toast((e as Error).message);
    }
  }

  return (
    <div className="card" style={{ marginTop: 16 }}>
      <div className="row" style={{ justifyContent: "space-between", marginBottom: 4 }}>
        <div className="caps">{t.cabinetButtonsTitle}</div>
        <button className="btn primary sm" onClick={save}>
          {t.saveApply}
        </button>
      </div>
      <div className="muted" style={{ fontSize: 12.5, marginBottom: 12 }}>
        {t.cabinetButtonsHint}
      </div>
      <div className="grid" style={{ gap: 6, maxWidth: 460 }}>
        {list.map((b, i) => (
          <div
            key={b.key}
            className="row"
            style={{
              justifyContent: "space-between",
              padding: "8px 10px",
              border: "1px solid var(--border2)",
              borderRadius: 8,
              opacity: b.enabled ? 1 : 0.5,
            }}
          >
            <span className="row" style={{ gap: 8 }}>
              <Toggle
                on={b.enabled}
                onChange={(v) => update(list.map((x) => (x.key === b.key ? { ...x, enabled: v } : x)))}
              />
              <b style={{ fontWeight: 500 }}>{b.label}</b>
              {b.gated && <span className="cap-pill dim">{t.cabinetButtonGated}</span>}
            </span>
            <span className="row" style={{ gap: 4 }}>
              <button className="btn secondary sm" onClick={() => move(i, -1)}>
                ↑
              </button>
              <button className="btn secondary sm" onClick={() => move(i, 1)}>
                ↓
              </button>
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

type Node = {
  id: string;
  parent: string | null;
  label: string;
  kind: "screen" | "action" | "link" | "miniapp" | "back";
  payload: string | null;
  custom_emoji_id: string | null;
  color: string | null;
  image_path: string | null;
  is_active: boolean;
  order_index?: number;
  row_index?: number;
};

const SWATCHES = ["", "#31A24C", "#2E63E7", "#E53935", "#F59E0B", "#7C5CFF", "#111111"];

let nextId = 1;
function genId(): string {
  return `n${Date.now().toString(36)}${nextId++}`;
}

export default function BotButtons() {
  const { t, toast, confirm } = useApp();
  const qc = useQueryClient();
  const [nodes, setNodes] = useState<Node[]>([]);
  const [selId, setSelId] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [uploading, setUploading] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  async function uploadImage(f: File) {
    setUploading(true);
    try {
      const form = new FormData();
      form.append("file", f);
      const res = await fetch("/api/admin/upload", {
        method: "POST",
        headers: { Authorization: `Bearer ${getToken()}` },
        body: form,
      });
      if (!res.ok) throw new Error(await res.text());
      const data = (await res.json()) as { path: string };
      patchSel({ image_path: data.path });
      toast("✓");
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setUploading(false);
    }
  }

  const data = useQuery({
    queryKey: ["bot-menu"],
    queryFn: () => api.get<{ nodes: Node[] }>("/api/admin/bot-menu"),
  });

  useEffect(() => {
    if (data.data && !loaded) {
      setNodes(data.data.nodes);
      setLoaded(true);
    }
  }, [data.data, loaded]);

  const sel = nodes.find((n) => n.id === selId) ?? null;
  const kids = (parent: string | null) =>
    nodes
      .filter((n) => n.parent === parent)
      .sort(
        (a, b) =>
          (a.row_index ?? 0) - (b.row_index ?? 0) || (a.order_index ?? 0) - (b.order_index ?? 0),
      );

  // Current buttons-per-row of a screen = its widest row. A flat menu (every button on the same
  // row_index) renders stacked one-per-row in the bot, so it reads as width 1 here too.
  function rowWidth(parent: string | null): number {
    const siblings = kids(parent);
    if (new Set(siblings.map((n) => n.row_index ?? 0)).size <= 1) return 1;
    const counts = new Map<number, number>();
    for (const n of siblings) {
      const r = n.row_index ?? 0;
      counts.set(r, (counts.get(r) ?? 0) + 1);
    }
    return Math.max(1, ...counts.values());
  }

  // Re-flow a screen's buttons into rows of `perRow`, preserving their order. This stamps the
  // row_index the bot renders by (inline honours up to 3/row; the reply bottom-bar up to 2).
  function layoutRows(parent: string | null, perRow: number) {
    const seq = kids(parent);
    const pos = new Map(seq.map((n, i) => [n.id, i]));
    setNodes((ns) =>
      ns.map((n) => {
        const i = pos.get(n.id);
        if (i === undefined) return n;
        return { ...n, row_index: Math.floor(i / perRow), order_index: i };
      }),
    );
  }

  function patchSel(patch: Partial<Node>) {
    if (!selId) return;
    setNodes((ns) => ns.map((n) => (n.id === selId ? { ...n, ...patch } : n)));
  }

  function addNode() {
    // Child of the selected screen (or of its parent when selected is not a screen).
    let parent: string | null = null;
    if (sel) parent = sel.kind === "screen" ? sel.id : sel.parent;
    const node: Node = {
      id: genId(),
      parent,
      label: "Новая кнопка",
      kind: "action",
      payload: null,
      custom_emoji_id: null,
      color: null,
      image_path: null,
      is_active: true,
      order_index: kids(parent).length,
    };
    setNodes((ns) => [...ns, node]);
    setSelId(node.id);
  }

  async function removeSel() {
    if (!sel) return;
    if (!(await confirm(t.deleteNodeConfirm))) return;
    // Collect the whole subtree.
    const doomed = new Set<string>([sel.id]);
    let grew = true;
    while (grew) {
      grew = false;
      for (const n of nodes) {
        if (n.parent && doomed.has(n.parent) && !doomed.has(n.id)) {
          doomed.add(n.id);
          grew = true;
        }
      }
    }
    setNodes((ns) => ns.filter((n) => !doomed.has(n.id)));
    setSelId(null);
  }

  function move(dir: -1 | 1) {
    if (!sel) return;
    const seq = kids(sel.parent);
    const idx = seq.findIndex((n) => n.id === sel.id);
    const j = idx + dir;
    if (j < 0 || j >= seq.length) return;
    const reordered = [...seq];
    [reordered[idx], reordered[j]] = [reordered[j], reordered[idx]];
    // Keep the screen's current row width so the grid re-flows instead of scrambling rows.
    const w = rowWidth(sel.parent);
    const patch = new Map(
      reordered.map((n, i) => [n.id, { order_index: i, row_index: Math.floor(i / w) }]),
    );
    setNodes((ns) => ns.map((n) => (patch.has(n.id) ? { ...n, ...patch.get(n.id)! } : n)));
  }

  async function save() {
    try {
      const payload = nodes.map((n, i) => ({
        id: n.id,
        parent: n.parent,
        label: n.label,
        kind: n.kind,
        payload: n.payload,
        custom_emoji_id: n.custom_emoji_id,
        color: n.color,
        image_path: n.image_path,
        is_active: n.is_active,
        // Persist BOTH layout axes so reordering sticks: order_index (position among
        // siblings, set by move()) and row_index (which buttons share a row). Without
        // order_index the server fell back to array/creation order and reorders snapped back.
        row_index: n.row_index ?? 0,
        order_index: n.order_index ?? i,
      }));
      const res = await api.put<{ nodes: Node[] }>("/api/admin/bot-menu", { nodes: payload });
      setNodes(res.nodes);
      setSelId(null);
      void qc.invalidateQueries({ queryKey: ["bot-menu"] });
      toast(t.saved);
    } catch (e) {
      toast((e as Error).message);
    }
  }

  // Preview: the screen owning the selected node (or root).
  const previewScreenId = useMemo(() => {
    if (!sel) return null;
    return sel.kind === "screen" ? sel.id : sel.parent;
  }, [sel]);
  const previewScreen = nodes.find((n) => n.id === previewScreenId) ?? null;
  const previewButtons = kids(previewScreenId);
  // Show the exact grid the bot renders. With no deliberate layout (every button on the same
  // row_index) the bot stacks one per row, so the preview does too; once «В ряд 2/3» assigns
  // distinct row_index values, group buttons by that so what you see is what the bot shows.
  const previewRows = useMemo(() => {
    const deliberate = new Set(previewButtons.map((b) => b.row_index ?? 0)).size > 1;
    if (!deliberate) return previewButtons.map((b) => [b]); // stacked, one per row
    const out: Node[][] = [];
    let cur: number | null = null;
    for (const b of previewButtons) {
      const r = b.row_index ?? 0;
      if (out.length === 0 || r !== cur) {
        out.push([]);
        cur = r;
      }
      out[out.length - 1].push(b);
    }
    return out;
  }, [previewButtons]);

  function TreeRow({ node, depth }: { node: Node; depth: number }) {
    const children = kids(node.id);
    return (
      <div style={{ position: "relative" }}>
        <div
          className="row click"
          style={{
            padding: "8px 10px",
            cursor: "pointer",
            background: node.id === selId ? "var(--pill)" : "var(--panel2)",
            border:
              node.id === selId ? "1px solid var(--muted)" : "1px solid var(--border)",
            borderRadius: 6,
            marginBottom: 6,
          }}
          onClick={() => setSelId(node.id)}
        >
          <span
            style={{
              width: 10,
              height: 10,
              borderRadius: node.kind === "screen" ? 2 : "50%",
              background: node.color || "var(--border2)",
              flex: "0 0 auto",
            }}
          />
          <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {node.label}
          </span>
          {node.image_path && <span title="картинка/GIF экрана">🖼</span>}
          <span className="cap-pill" style={{ marginLeft: "auto" }}>
            {kindLabel(node.kind)}
          </span>
        </div>
        {children.length > 0 && (
          <div
            style={{
              marginLeft: 14,
              paddingLeft: 16,
              borderLeft: "2px solid var(--border2)",
            }}
          >
            {children.map((c) => (
              <div key={c.id} style={{ position: "relative" }}>
                <span
                  style={{
                    position: "absolute",
                    left: -16,
                    top: 17,
                    width: 12,
                    height: 2,
                    background: "var(--border2)",
                  }}
                />
                <TreeRow node={c} depth={depth + 1} />
              </div>
            ))}
          </div>
        )}
      </div>
    );
  }

  function kindLabel(kind: Node["kind"]): string {
    return {
      screen: t.kindScreen,
      action: t.kindAction,
      link: t.kindLink,
      miniapp: t.kindMiniapp,
      back: t.kindBack,
    }[kind];
  }

  return (
    <>
      <div className="page-head">
        <h1 className="h1">{t.botButtons}</h1>
        <div className="actions">
          <button className="btn primary" onClick={save}>
            {t.saveMenu}
          </button>
        </div>
      </div>

      <div className="cols">
        {/* tree */}
        <div className="card" style={{ flex: "1 1 280px" }}>
          <div className="caps" style={{ marginBottom: 10 }}>
            {t.menuTree}
          </div>
          <div className="grid" style={{ gap: 2 }}>
            {kids(null).map((n) => (
              <TreeRow key={n.id} node={n} depth={0} />
            ))}
          </div>
          <button className="btn secondary" style={{ marginTop: 12, width: "100%" }} onClick={addNode}>
            {t.addButton}
          </button>
        </div>

        {/* editor */}
        <div className="card" style={{ flex: "1 1 300px" }}>
          {sel ? (
            <div className="grid" style={{ gap: 14 }}>
              <div className="row">
                <button className="btn secondary sm" onClick={() => move(-1)}>
                  ↑
                </button>
                <button className="btn secondary sm" onClick={() => move(1)}>
                  ↓
                </button>
                <button className="btn danger sm" style={{ marginLeft: "auto" }} onClick={removeSel}>
                  ✕ {t.delete}
                </button>
              </div>
              <Field label={t.buttonText}>
                <input
                  className="input"
                  value={sel.label}
                  onChange={(e) => patchSel({ label: e.target.value })}
                />
              </Field>
              <Field label={t.buttonType}>
                <Seg
                  value={sel.kind}
                  options={(["screen", "action", "link", "miniapp", "back"] as const).map((k) => ({
                    id: k,
                    label: kindLabel(k),
                  }))}
                  onChange={(kind) => patchSel({ kind })}
                />
              </Field>
              {sel.kind === "screen" && (
                <Field label={t.screenText}>
                  <textarea
                    className="input"
                    rows={4}
                    value={sel.payload ?? ""}
                    onChange={(e) => patchSel({ payload: e.target.value })}
                  />
                </Field>
              )}
              {sel.kind === "link" && (
                <Field label="URL">
                  <input
                    className="input mono"
                    value={sel.payload ?? ""}
                    placeholder="https://…"
                    onChange={(e) => patchSel({ payload: e.target.value })}
                  />
                </Field>
              )}
              {sel.kind === "action" && (
                <Field label="ACTION CODE">
                  <input
                    className="input mono"
                    value={sel.payload ?? ""}
                    placeholder="buy / balance / referral / support / trial"
                    onChange={(e) => patchSel({ payload: e.target.value })}
                  />
                </Field>
              )}
              <Field label={t.buttonColor}>
                <div className="row" style={{ flexWrap: "wrap" }}>
                  {SWATCHES.map((c) => (
                    <button
                      key={c || "none"}
                      title={c || "—"}
                      onClick={() => patchSel({ color: c || null })}
                      style={{
                        width: 24,
                        height: 24,
                        borderRadius: 3,
                        cursor: "pointer",
                        background: c || "transparent",
                        border:
                          (sel.color ?? "") === c
                            ? "2px solid var(--text)"
                            : "1px solid var(--border2)",
                      }}
                    />
                  ))}
                  <input
                    className="input mono"
                    style={{ width: 100 }}
                    placeholder="#HEX"
                    value={sel.color ?? ""}
                    onChange={(e) => patchSel({ color: e.target.value || null })}
                  />
                </div>
              </Field>
              {sel.kind === "screen" && (
                <Field label={t.screenImage}>
                  <div className="row" style={{ flexWrap: "wrap" }}>
                    <button
                      className="btn secondary sm"
                      disabled={uploading}
                      onClick={() => fileRef.current?.click()}
                    >
                      {uploading ? "…" : "🖼 " + t.uploadImage}
                    </button>
                    {sel.image_path && (
                      <>
                        <img
                          src={"/" + sel.image_path}
                          alt=""
                          style={{ height: 40, borderRadius: 4, border: "1px solid var(--border2)" }}
                        />
                        <button
                          className="btn danger sm"
                          onClick={() => patchSel({ image_path: null })}
                        >
                          ✕
                        </button>
                      </>
                    )}
                    <input
                      ref={fileRef}
                      type="file"
                      accept=".jpg,.jpeg,.png,.webp,.gif,.mp4"
                      style={{ display: "none" }}
                      onChange={(e) => {
                        const f = e.target.files?.[0];
                        if (f) void uploadImage(f);
                        e.target.value = "";
                      }}
                    />
                  </div>
                </Field>
              )}
            </div>
          ) : (
            <span className="dim">← {t.menuTree}</span>
          )}
        </div>

        {/* live preview */}
        <div className="card" style={{ flex: "1 1 300px" }}>
          <div className="caps" style={{ marginBottom: 10 }}>
            {t.livePreview}
          </div>
          {previewButtons.length > 1 && (
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 10,
                flexWrap: "wrap",
                padding: "10px 12px",
                marginBottom: 12,
                background: "var(--panel2)",
                border: "1px solid var(--border)",
                borderRadius: 8,
              }}
            >
              <span style={{ fontSize: 13, fontWeight: 600 }}>{t.perRow}:</span>
              <div className="row" style={{ gap: 6 }}>
                {[1, 2, 3].map((w) => (
                  <button
                    key={w}
                    className={`btn ${rowWidth(previewScreenId) === w ? "primary" : "secondary"}`}
                    style={{ minWidth: 42, padding: "6px 12px", fontWeight: 700 }}
                    onClick={() => layoutRows(previewScreenId, w)}
                  >
                    {w}
                  </button>
                ))}
              </div>
              <span className="dim" style={{ fontSize: 12, flexBasis: "100%" }}>
                {t.perRowHint}
              </span>
            </div>
          )}
          <div
            style={{
              background: "var(--panel2)",
              border: "1px solid var(--border)",
              borderRadius: 8,
              padding: 14,
            }}
          >
            <div
              style={{
                background: "var(--pill)",
                borderRadius: 8,
                overflow: "hidden",
                fontSize: 13,
                marginBottom: 10,
              }}
            >
              {previewScreen?.image_path && (
                <img
                  src={"/" + previewScreen.image_path}
                  alt=""
                  style={{ width: "100%", maxHeight: 140, objectFit: "cover", display: "block" }}
                />
              )}
              <div style={{ padding: "10px 12px", whiteSpace: "pre-wrap" }}>
                {previewScreen?.payload || "Привет! Это VPN-бот — выбери действие в меню."}
              </div>
            </div>
            <div className="grid" style={{ gap: 6 }}>
              {previewRows.map((row, ri) => (
                <div key={ri} className="row" style={{ gap: 6 }}>
                  {row.map((b) => (
                    <button
                      key={b.id}
                      onClick={() => setSelId(b.id)}
                      title={b.label}
                      style={{
                        flex: "1 1 0",
                        minWidth: 0,
                        borderRadius: 6,
                        border:
                          b.id === selId ? "1px solid var(--text)" : "1px solid var(--border2)",
                        background: b.color || "var(--panel)",
                        color: b.color ? "#fff" : "var(--text)",
                        padding: "9px 12px",
                        fontSize: 13,
                        cursor: "pointer",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {b.label}
                    </button>
                  ))}
                </div>
              ))}
              {previewButtons.length === 0 && <span className="dim">—</span>}
            </div>
          </div>
        </div>
      </div>
      <BotTextsCard />
      <CabinetButtonsCard />
    </>
  );
}
