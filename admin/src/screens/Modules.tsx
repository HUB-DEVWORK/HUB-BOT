/* Screen — Модули: list built-in + uploaded modules, toggle on/off,
   upload a module as .zip or as a folder, delete an uploaded one, and
   restart the bot to apply changes (the enable gate resolves at startup). */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { api, getToken } from "../api/client";
import { Toggle } from "../components/ui";
import { useApp } from "../state/app";

type Module = {
  name: string;
  title_ru: string;
  title_en: string;
  version: string;
  enabled: boolean;
  external: boolean;
  requires: string[];
  has_config: boolean;
  enable_key: string;
  config_keys: string[];
};
type ListResp = { modules: Module[]; ext_dir: string };

export default function Modules() {
  const { t, lang, toast, confirm } = useApp();
  const qc = useQueryClient();
  const nav = useNavigate();
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);

  const list = useQuery({
    queryKey: ["modules"],
    queryFn: () => api.get<ListResp>("/api/admin/modules"),
  });

  const toggle = useMutation({
    mutationFn: (v: { name: string; enabled: boolean }) =>
      api.post(`/api/admin/modules/${v.name}/toggle`, { enabled: v.enabled }),
    onSuccess: () => {
      setDirty(true);
      void qc.invalidateQueries({ queryKey: ["modules"] });
    },
    onError: (e) => toast((e as Error).message),
  });

  async function uploadFiles(url: string, form: FormData) {
    setBusy(true);
    try {
      const res = await fetch(url, {
        method: "POST",
        headers: { Authorization: `Bearer ${getToken() ?? ""}` },
        body: form,
      });
      const j = (await res.json().catch(() => ({}))) as { detail?: string; name?: string };
      if (!res.ok) throw new Error(j.detail ?? `HTTP ${res.status}`);
      toast(`${t.modulesUploadedOk}: ${j.name ?? ""}`);
      setDirty(true);
      void qc.invalidateQueries({ queryKey: ["modules"] });
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function onZip(file: File) {
    const fd = new FormData();
    fd.append("file", file);
    void uploadFiles("/api/admin/modules/upload-zip", fd);
  }

  function onFolder(files: FileList) {
    const fd = new FormData();
    for (const f of Array.from(files)) {
      // webkitRelativePath carries the folder tree (e.g. "mymod/manifest.py").
      const rel = (f as File & { webkitRelativePath?: string }).webkitRelativePath || f.name;
      fd.append("files", f, rel);
    }
    void uploadFiles("/api/admin/modules/upload-folder", fd);
  }

  async function onDelete(m: Module) {
    if (!(await confirm(`${t.modulesDeleteAsk} · ${m.name}?`))) return;
    try {
      await api.del(`/api/admin/modules/${m.name}`);
      toast(t.modulesDeleted);
      setDirty(true);
      void qc.invalidateQueries({ queryKey: ["modules"] });
    } catch (e) {
      toast((e as Error).message);
    }
  }

  async function restartBot() {
    if (!(await confirm(t.modulesRestartAsk))) return;
    try {
      const r = await api.post<{ status?: string; hint?: string }>(
        "/api/admin/modules/restart-bot",
      );
      if (r.status === "started") {
        toast(t.modulesRestartStarted);
        setDirty(false);
      } else {
        toast(r.hint || r.status || "…");
      }
    } catch (e) {
      toast((e as Error).message);
    }
  }

  function openConfig(m: Module) {
    // Hand off this module's exact param keys to the settings screen, which
    // filters to them and shows a module header (see Settings.tsx).
    sessionStorage.setItem("settings_keys", JSON.stringify(m.config_keys));
    sessionStorage.setItem("settings_title", lang === "ru" ? m.title_ru : m.title_en);
    nav("/settings");
  }

  const mods = list.data?.modules ?? [];
  const builtin = mods.filter((m) => !m.external);
  const uploaded = mods.filter((m) => m.external);

  function Row({ m }: { m: Module }) {
    const title = lang === "ru" ? m.title_ru : m.title_en;
    return (
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "44px minmax(0,1fr) auto",
          gap: 10,
          alignItems: "center",
          padding: "10px 0",
          borderTop: "1px solid var(--border)",
        }}
      >
        <Toggle
          on={m.enabled}
          onChange={(v) => toggle.mutate({ name: m.name, enabled: v })}
        />
        <div style={{ minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <span style={{ fontWeight: 600 }}>{title}</span>
            <span className="dim mono" style={{ fontSize: 11 }}>
              {m.name} · v{m.version}
            </span>
          </div>
          <div className="dim" style={{ fontSize: 11, marginTop: 2 }}>
            {m.requires.length > 0 && `${t.modulesRequires}: ${m.requires.join(", ")} · `}
            {m.has_config ? "" : t.modulesNoConfig}
          </div>
        </div>
        <div className="row" style={{ gap: 6 }}>
          {m.has_config && (
            <button
              className="btn secondary sm"
              onClick={() => openConfig(m)}
              title={t.modulesConfigure}
            >
              ⚙️
            </button>
          )}
          {m.external && (
            <button className="btn danger sm" onClick={() => void onDelete(m)} title="delete">
              🗑
            </button>
          )}
        </div>
      </div>
    );
  }

  return (
    <>
      <div className="page-head">
        <h1 className="h1">🧩 {t.modules}</h1>
      </div>

      {dirty && (
        <div
          className="card"
          style={{
            borderColor: "var(--accent, #F7971D)",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: 12,
            marginBottom: 14,
          }}
        >
          <span>❕ {t.modulesRestartRequired}</span>
          <button className="btn primary sm" onClick={() => void restartBot()}>
            ⟳ {t.modulesRestartBot}
          </button>
        </div>
      )}

      <div className="cols">
        <div className="card main-col">
          {builtin.length > 0 && (
            <>
              <div className="caps" style={{ marginBottom: 4 }}>
                {t.modulesBuiltin}
              </div>
              {builtin.map((m) => (
                <Row key={m.name} m={m} />
              ))}
            </>
          )}

          <div className="caps" style={{ margin: "20px 0 4px" }}>
            {t.modulesUploaded}
          </div>
          {uploaded.length === 0 ? (
            <div className="dim" style={{ fontSize: 12, padding: "10px 0" }}>
              {t.modulesEmpty}
            </div>
          ) : (
            uploaded.map((m) => <Row key={m.name} m={m} />)
          )}
        </div>

        <div className="card side-col">
          <div className="caps" style={{ marginBottom: 12 }}>
            {t.modulesUploaded}
          </div>
          <div className="grid" style={{ gap: 10 }}>
            <label className="btn secondary" style={{ cursor: "pointer", textAlign: "center" }}>
              {busy ? "…" : `📦 ${t.modulesUploadZip}`}
              <input
                type="file"
                accept=".zip"
                style={{ display: "none" }}
                disabled={busy}
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) onZip(f);
                  e.target.value = "";
                }}
              />
            </label>
            <label className="btn secondary" style={{ cursor: "pointer", textAlign: "center" }}>
              {busy ? "…" : `📁 ${t.modulesUploadFolder}`}
              <input
                type="file"
                ref={(el) => {
                  // webkitdirectory/directory aren't in the React types; set them on the node.
                  if (el) {
                    el.setAttribute("webkitdirectory", "");
                    el.setAttribute("directory", "");
                  }
                }}
                multiple
                style={{ display: "none" }}
                disabled={busy}
                onChange={(e) => {
                  const fs = e.target.files;
                  if (fs && fs.length) onFolder(fs);
                  e.target.value = "";
                }}
              />
            </label>
            <div className="dim" style={{ fontSize: 11, lineHeight: 1.5 }}>
              {lang === "ru"
                ? "Одна корневая папка с manifest.py внутри. Модуль появится в списке — включи тумблером и перезагрузи бота."
                : "One top folder containing manifest.py. It shows up in the list — flip it on and restart the bot."}
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
