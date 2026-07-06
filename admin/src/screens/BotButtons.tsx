/* Screen 05 — Конструктор кнопок бота: tree + editor + live chat preview. */

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";

import { api, getToken } from "../api/client";
import { Field, Seg } from "../components/ui";
import { useApp } from "../state/app";

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
      .sort((a, b) => (a.order_index ?? 0) - (b.order_index ?? 0));

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
    const siblings = kids(sel.parent);
    const idx = siblings.findIndex((n) => n.id === sel.id);
    const j = idx + dir;
    if (j < 0 || j >= siblings.length) return;
    const a = siblings[idx];
    const b = siblings[j];
    setNodes((ns) =>
      ns.map((n) =>
        n.id === a.id
          ? { ...n, order_index: b.order_index ?? j }
          : n.id === b.id
            ? { ...n, order_index: a.order_index ?? idx }
            : n,
      ),
    );
  }

  async function save() {
    try {
      const payload = nodes.map((n) => ({
        id: n.id,
        parent: n.parent,
        label: n.label,
        kind: n.kind,
        payload: n.payload,
        custom_emoji_id: n.custom_emoji_id,
        color: n.color,
        image_path: n.image_path,
        is_active: n.is_active,
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
          {node.image_path && <span title="картинка экрана">🖼</span>}
          {node.custom_emoji_id && <span className="dim">◈</span>}
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
              <Field label={t.customEmoji}>
                <input
                  className="input mono"
                  value={sel.custom_emoji_id ?? ""}
                  placeholder="5368324170671202286"
                  onChange={(e) => patchSel({ custom_emoji_id: e.target.value || null })}
                />
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
                      accept=".jpg,.jpeg,.png,.webp"
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
              {previewButtons.map((b) => (
                <button
                  key={b.id}
                  onClick={() => setSelId(b.id)}
                  style={{
                    borderRadius: 6,
                    border:
                      b.id === selId ? "1px solid var(--text)" : "1px solid var(--border2)",
                    background: b.color || "var(--panel)",
                    color: b.color ? "#fff" : "var(--text)",
                    padding: "9px 12px",
                    fontSize: 13,
                    cursor: "pointer",
                  }}
                >
                  {b.label}
                </button>
              ))}
              {previewButtons.length === 0 && <span className="dim">—</span>}
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
