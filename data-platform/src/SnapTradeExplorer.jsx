/**
 * SnapTradeExplorer.jsx — schema-agnostic data explorer
 * ─────────────────────────────────────────────────────
 * Renders EVERY value in the SnapTrade export JSON as tables, with no
 * hardcoded columns. Structure is discovered at runtime, so accounts with
 * different shapes (extra sections, nested objects, {data:[...]} wrappers,
 * varying keys) all render fully — columns are the union of every field found.
 *
 * Data source: GET /api/explorer/data  ->  { success, data: <full export> }
 * Refresh:     POST /api/explorer/refresh
 * (Both gated by HTTP Basic Auth on the backend.)
 *
 * Requires: axios, Tailwind CSS (CDN), lucide-react
 */

import React, { useState, useEffect, useMemo, useCallback } from "react";
import axios from "axios";
import {
  Database, RefreshCw, Search, ChevronUp, ChevronDown,
  Download, AlertCircle, X, Users, Columns, List, Maximize2, Copy, Check,
} from "lucide-react";

const API_URL = "/api";

// ── value helpers ────────────────────────────────────────────────────────────
const isObj = (v) => v !== null && typeof v === "object" && !Array.isArray(v);
const isArr = Array.isArray;
const isContainer = (v) => isObj(v) || isArr(v);

/** Deep-flatten any value to { "dot.path": scalar }. Empty objects/arrays are
 *  kept as visible leaves so no detail is silently dropped. */
function flatten(value, prefix = "", out = {}) {
  if (isObj(value)) {
    const keys = Object.keys(value);
    if (keys.length === 0) out[prefix] = "{}";
    else for (const k of keys) flatten(value[k], prefix ? `${prefix}.${k}` : k, out);
  } else if (isArr(value)) {
    if (value.length === 0) out[prefix] = "[]";
    else value.forEach((item, i) => flatten(item, `${prefix}.${i}`, out));
  } else {
    out[prefix] = value;
  }
  return out;
}

/** Shallow row: top-level keys only (objects/arrays kept raw for expansion). */
function shallowRow(r) {
  return isObj(r) ? { ...r } : { value: r };
}

/** Normalize a section value into { rows, wrapper, kind }. Handles list,
 *  {data:[...]}-style single-list wrappers, single objects, and scalars. */
function sectionToRows(value) {
  if (isArr(value)) return { rows: value, wrapper: null, kind: "list" };
  if (isObj(value)) {
    const keys = Object.keys(value);
    const listKeys = keys.filter((k) => isArr(value[k]));
    if (keys.length === 1 && listKeys.length === 1)
      return { rows: value[keys[0]], wrapper: keys[0], kind: "list" };
    return { rows: [value], wrapper: null, kind: "object" };
  }
  return { rows: [{ value }], wrapper: null, kind: "scalar" };
}

/** Union of column keys across rows, preserving first-seen order. */
function columnsOf(rows, mode) {
  const seen = [];
  const set = new Set();
  for (const r of rows) {
    const flat = mode === "flat" ? flatten(isContainer(r) ? r : { value: r }) : shallowRow(r);
    for (const k of Object.keys(flat)) if (!set.has(k)) { set.add(k); seen.push(k); }
  }
  return seen;
}

const fmtScalar = (v) => {
  if (v === null) return "null";
  if (v === undefined) return undefined; // means "missing"
  if (typeof v === "boolean") return String(v);
  return v;
};

// ── tiny UI atoms ──────────────────────────────────────────────────────────────
function Badge({ children, color = "gray" }) {
  const map = {
    gray: "bg-gray-100 text-gray-600",
    blue: "bg-blue-100 text-blue-700",
    green: "bg-green-100 text-green-700",
    amber: "bg-amber-100 text-amber-700",
  };
  return <span className={`px-1.5 py-0.5 rounded text-[11px] font-medium ${map[color]}`}>{children}</span>;
}

/** A single table cell value (scalar formatted, container -> expand button). */
function ValueCell({ value, onExpand }) {
  if (value === undefined) return <span className="text-gray-300">—</span>;
  if (value === null) return <span className="text-gray-400 italic">null</span>;
  if (isObj(value)) {
    const n = Object.keys(value).length;
    return (
      <button onClick={() => onExpand(value)} className="inline-flex items-center gap-1 text-blue-600 hover:underline">
        <Maximize2 className="w-3 h-3" /> {`{ } ${n} ${n === 1 ? "key" : "keys"}`}
      </button>
    );
  }
  if (isArr(value)) {
    return (
      <button onClick={() => onExpand(value)} className="inline-flex items-center gap-1 text-blue-600 hover:underline">
        <Maximize2 className="w-3 h-3" /> {`[ ] ${value.length} ${value.length === 1 ? "item" : "items"}`}
      </button>
    );
  }
  if (typeof value === "boolean")
    return <Badge color={value ? "green" : "gray"}>{String(value)}</Badge>;
  if (typeof value === "string" && /^https?:\/\//.test(value))
    return <a href={value} target="_blank" rel="noreferrer" className="text-blue-600 hover:underline break-all">{value}</a>;
  if (typeof value === "number")
    return <span className="tabular-nums">{value}</span>;
  return <span className="break-words">{String(value)}</span>;
}

// ── recursive JSON viewer (used in the expand modal) ────────────────────────────
function JsonViewer({ value, depth = 0 }) {
  if (!isContainer(value)) {
    const f = fmtScalar(value);
    return <span className="text-gray-800 break-all">{f === undefined ? "—" : String(f)}</span>;
  }
  const entries = isArr(value) ? value.map((v, i) => [i, v]) : Object.entries(value);
  if (entries.length === 0)
    return <span className="text-gray-400 italic">{isArr(value) ? "[] (empty)" : "{} (empty)"}</span>;
  return (
    <table className="w-full text-sm border-collapse">
      <tbody>
        {entries.map(([k, v]) => (
          <tr key={k} className="border-b border-gray-100 align-top">
            <td className="py-1 pr-3 font-mono text-xs text-gray-500 whitespace-nowrap w-1 align-top">{String(k)}</td>
            <td className="py-1">
              {isContainer(v) ? <JsonViewer value={v} depth={depth + 1} /> : <ValueCellInline value={v} />}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
function ValueCellInline({ value }) {
  if (value === null) return <span className="text-gray-400 italic">null</span>;
  if (typeof value === "boolean") return <Badge color={value ? "green" : "gray"}>{String(value)}</Badge>;
  if (typeof value === "string" && /^https?:\/\//.test(value))
    return <a href={value} target="_blank" rel="noreferrer" className="text-blue-600 hover:underline break-all">{value}</a>;
  return <span className="text-gray-800 break-all">{String(value)}</span>;
}

// ── expand modal ────────────────────────────────────────────────────────────────
function ExpandModal({ value, onClose }) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    navigator.clipboard?.writeText(JSON.stringify(value, null, 2));
    setCopied(true); setTimeout(() => setCopied(false), 1200);
  };
  return (
    <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4" onClick={onClose}>
      <div className="bg-white rounded-xl shadow-xl max-w-3xl w-full max-h-[80vh] flex flex-col" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between px-4 py-3 border-b">
          <h3 className="font-semibold text-gray-800">Nested value</h3>
          <div className="flex items-center gap-2">
            <button onClick={copy} className="inline-flex items-center gap-1 text-sm text-gray-600 hover:text-gray-900 px-2 py-1 rounded hover:bg-gray-100">
              {copied ? <Check className="w-4 h-4 text-green-600" /> : <Copy className="w-4 h-4" />} {copied ? "Copied" : "Copy JSON"}
            </button>
            <button onClick={onClose} className="p-1 rounded hover:bg-gray-100"><X className="w-5 h-5" /></button>
          </div>
        </div>
        <div className="p-4 overflow-auto">
          <JsonViewer value={value} />
        </div>
      </div>
    </div>
  );
}

// ── the generic data table ──────────────────────────────────────────────────────
const PAGE_SIZES = [25, 50, 100, "All"];

function DataTable({ rows, title }) {
  const [mode, setMode] = useState("flat"); // 'flat' | 'compact'
  const [query, setQuery] = useState("");
  const [sortCol, setSortCol] = useState(null);
  const [sortDir, setSortDir] = useState("asc");
  const [hidden, setHidden] = useState(() => new Set());
  const [showCols, setShowCols] = useState(false);
  const [pageSize, setPageSize] = useState(50);
  const [page, setPage] = useState(0);
  const [expand, setExpand] = useState(null);

  // reset transient state when the underlying rows/mode change
  useEffect(() => { setPage(0); setSortCol(null); setHidden(new Set()); }, [rows, mode]);

  const allColumns = useMemo(() => columnsOf(rows, mode), [rows, mode]);
  const columns = useMemo(() => allColumns.filter((c) => !hidden.has(c)), [allColumns, hidden]);

  // precompute per-row value maps for the active mode
  const flatRows = useMemo(
    () => rows.map((r) => (mode === "flat" ? flatten(isContainer(r) ? r : { value: r }) : shallowRow(r))),
    [rows, mode]
  );

  const filtered = useMemo(() => {
    if (!query.trim()) return flatRows.map((fr, i) => ({ fr, i }));
    const q = query.toLowerCase();
    return flatRows
      .map((fr, i) => ({ fr, i }))
      .filter(({ fr }) => columns.some((c) => {
        const v = fr[c];
        return v != null && String(isContainer(v) ? JSON.stringify(v) : v).toLowerCase().includes(q);
      }));
  }, [flatRows, columns, query]);

  const sorted = useMemo(() => {
    if (!sortCol) return filtered;
    const dir = sortDir === "asc" ? 1 : -1;
    return [...filtered].sort((a, b) => {
      const av = a.fr[sortCol], bv = b.fr[sortCol];
      if (av == null && bv == null) return 0;
      if (av == null) return 1; if (bv == null) return -1;
      const an = Number(av), bn = Number(bv);
      if (!Number.isNaN(an) && !Number.isNaN(bn)) return (an - bn) * dir;
      return String(av).localeCompare(String(bv)) * dir;
    });
  }, [filtered, sortCol, sortDir]);

  const size = pageSize === "All" ? sorted.length || 1 : pageSize;
  const pageCount = Math.max(1, Math.ceil(sorted.length / size));
  const pageRows = useMemo(() => sorted.slice(page * size, page * size + size), [sorted, page, size]);

  const toggleSort = (c) => {
    if (sortCol === c) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else { setSortCol(c); setSortDir("asc"); }
  };

  const exportCsv = useCallback(() => {
    const esc = (v) => {
      if (v == null) return "";
      const s = isContainer(v) ? JSON.stringify(v) : String(v);
      return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
    };
    const header = columns.join(",");
    const body = sorted.map(({ fr }) => columns.map((c) => esc(fr[c])).join(",")).join("\n");
    const blob = new Blob([header + "\n" + body], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = `${(title || "table").replace(/\W+/g, "_")}.csv`; a.click();
    URL.revokeObjectURL(url);
  }, [columns, sorted, title]);

  if (!rows.length)
    return <div className="text-sm text-gray-400 italic py-8 text-center">No rows in this section.</div>;

  return (
    <div>
      {/* toolbar */}
      <div className="flex flex-wrap items-center gap-2 mb-3">
        <div className="relative">
          <Search className="w-4 h-4 absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-400" />
          <input
            value={query} onChange={(e) => { setQuery(e.target.value); setPage(0); }}
            placeholder="Search all fields…"
            className="pl-8 pr-3 py-1.5 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 w-56"
          />
        </div>
        <div className="inline-flex rounded-lg border border-gray-200 overflow-hidden">
          <button onClick={() => setMode("flat")}
            className={`px-2.5 py-1.5 text-xs inline-flex items-center gap-1 ${mode === "flat" ? "bg-blue-600 text-white" : "bg-white text-gray-600 hover:bg-gray-50"}`}>
            <Columns className="w-3.5 h-3.5" /> Flat
          </button>
          <button onClick={() => setMode("compact")}
            className={`px-2.5 py-1.5 text-xs inline-flex items-center gap-1 ${mode === "compact" ? "bg-blue-600 text-white" : "bg-white text-gray-600 hover:bg-gray-50"}`}>
            <List className="w-3.5 h-3.5" /> Compact
          </button>
        </div>
        <div className="relative">
          <button onClick={() => setShowCols((s) => !s)}
            className="px-2.5 py-1.5 text-xs border border-gray-200 rounded-lg inline-flex items-center gap-1 text-gray-600 hover:bg-gray-50">
            <Columns className="w-3.5 h-3.5" /> Columns ({columns.length}/{allColumns.length})
          </button>
          {showCols && (
            <div className="absolute z-20 mt-1 w-72 max-h-80 overflow-auto bg-white border border-gray-200 rounded-lg shadow-lg p-2">
              <div className="flex justify-between px-1 pb-1 mb-1 border-b text-xs">
                <button className="text-blue-600 hover:underline" onClick={() => setHidden(new Set())}>Show all</button>
                <button className="text-blue-600 hover:underline" onClick={() => setHidden(new Set(allColumns))}>Hide all</button>
              </div>
              {allColumns.map((c) => (
                <label key={c} className="flex items-center gap-2 px-1 py-0.5 text-xs hover:bg-gray-50 rounded cursor-pointer">
                  <input type="checkbox" checked={!hidden.has(c)} onChange={() => {
                    setHidden((h) => { const n = new Set(h); n.has(c) ? n.delete(c) : n.add(c); return n; });
                  }} />
                  <span className="font-mono break-all">{c}</span>
                </label>
              ))}
            </div>
          )}
        </div>
        <button onClick={exportCsv}
          className="px-2.5 py-1.5 text-xs border border-gray-200 rounded-lg inline-flex items-center gap-1 text-gray-600 hover:bg-gray-50">
          <Download className="w-3.5 h-3.5" /> CSV
        </button>
        <span className="text-xs text-gray-400 ml-auto">{sorted.length} of {rows.length} rows</span>
      </div>

      {/* table */}
      <div className="overflow-auto border border-gray-200 rounded-lg max-h-[65vh]">
        <table className="text-sm border-collapse min-w-full">
          <thead className="sticky top-0 bg-gray-50 z-10">
            <tr>
              <th className="px-2 py-2 text-left text-[11px] font-semibold text-gray-400 border-b border-gray-200 sticky left-0 bg-gray-50">#</th>
              {columns.map((c) => (
                <th key={c}
                  onClick={() => toggleSort(c)}
                  className="px-3 py-2 text-left text-[11px] font-semibold text-gray-500 border-b border-gray-200 whitespace-nowrap cursor-pointer hover:bg-gray-100 select-none">
                  <span className="inline-flex items-center gap-1 font-mono">
                    {c}
                    {sortCol === c && (sortDir === "asc" ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />)}
                  </span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {pageRows.map(({ fr, i }) => (
              <tr key={i} className="hover:bg-blue-50/40 border-b border-gray-100">
                <td className="px-2 py-1.5 text-[11px] text-gray-400 sticky left-0 bg-white">{i}</td>
                {columns.map((c) => (
                  <td key={c} className="px-3 py-1.5 whitespace-nowrap max-w-xs truncate" title={!isContainer(fr[c]) && fr[c] != null ? String(fr[c]) : undefined}>
                    <ValueCell value={fr[c]} onExpand={setExpand} />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* pagination */}
      <div className="flex items-center gap-3 mt-3 text-xs text-gray-600">
        <span>Rows per page:</span>
        <select value={pageSize} onChange={(e) => { setPageSize(e.target.value === "All" ? "All" : Number(e.target.value)); setPage(0); }}
          className="border border-gray-200 rounded px-1.5 py-1">
          {PAGE_SIZES.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <span className="ml-auto">Page {page + 1} / {pageCount}</span>
        <button disabled={page === 0} onClick={() => setPage((p) => p - 1)}
          className="px-2 py-1 border border-gray-200 rounded disabled:opacity-40">Prev</button>
        <button disabled={page >= pageCount - 1} onClick={() => setPage((p) => p + 1)}
          className="px-2 py-1 border border-gray-200 rounded disabled:opacity-40">Next</button>
      </div>

      {expand !== null && <ExpandModal value={expand} onClose={() => setExpand(null)} />}
    </div>
  );
}

// ── account label helpers ────────────────────────────────────────────────────────
function accLabel(acc) {
  const a = acc.account || {};
  return a.name || a.institution_name || a.number || a.id || "Account";
}
function accInst(acc) {
  return (acc.account || {}).institution_name || "—";
}

// ── main explorer ────────────────────────────────────────────────────────────────
export default function SnapTradeExplorer() {
  const [credentials, setCredentials] = useState(null);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [refreshing, setRefreshing] = useState(false);
  const [selUnit, setSelUnit] = useState(null);
  const [selSection, setSelSection] = useState(null);

  const load = useCallback(async () => {
    if (!credentials) return;
    setLoading(true); setError(null);
    try {
      const res = await axios.get(`${API_URL}/explorer/data`, { withCredentials: true, auth: credentials });
      if (res.data?.success) setData(res.data.data);
      else setError(res.data?.error || "Failed to load export.");
    } catch (e) {
      setError(e.response?.status === 401 ? "Authentication required (HTTP Basic Auth)." : (e.response?.data?.error || e.message));
    } finally { setLoading(false); }
  }, [credentials]);

  useEffect(() => { load(); }, [load]);

  const refresh = async () => {
    setRefreshing(true);
    setError(null);
    try {
      await axios.post(`${API_URL}/explorer/refresh`, {}, { withCredentials: true, auth: credentials });
      for (let attempt = 0; attempt < 75; attempt += 1) {
        await new Promise(resolve => setTimeout(resolve, 2000));
        const { data: state } = await axios.get(`${API_URL}/explorer/refresh`, { withCredentials: true, auth: credentials });
        if (state.status === 'failed') throw new Error(state.error);
        if (state.status === 'complete') {
          await load();
          return;
        }
      }
      throw new Error('Refresh is taking longer than expected. Reload to check the export.');
    } catch (e) {
      setError(e.response?.data?.error || e.message);
    } finally {
      setRefreshing(false);
    }
  };

  // build the flat list of account "units" from users[].accounts[] + top-level accounts[]
  const units = useMemo(() => {
    if (!data) return [];
    const out = [];
    (data.users || []).forEach((u, ui) =>
      (u.accounts || []).forEach((acc, ai) =>
        out.push({ id: `u${ui}-${ai}`, user: u.username, source: "User", acc })
      )
    );
    (data.accounts || []).forEach((acc, ai) =>
      out.push({ id: `top-${ai}`, user: null, source: "Top-level", acc })
    );
    return out;
  }, [data]);

  // default selection
  useEffect(() => {
    if (units.length && !units.find((u) => u.id === selUnit)) setSelUnit(units[0].id);
  }, [units, selUnit]);

  const currentUnit = units.find((u) => u.id === selUnit) || null;
  const sections = useMemo(() => currentUnit ? Object.keys(currentUnit.acc) : [], [currentUnit]);

  useEffect(() => {
    if (sections.length && selSection !== "__raw__" && !sections.includes(selSection))
      setSelSection(sections[0]);
  }, [sections, selSection]);

  const sectionInfo = useMemo(() => {
    if (!currentUnit || !selSection || selSection === "__raw__") return null;
    return sectionToRows(currentUnit.acc[selSection]);
  }, [currentUnit, selSection]);

  // Credentials stay in memory and are sent only to the configured API.
  if (!credentials) return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50">
      <form className="bg-white rounded-xl border p-6 space-y-4 w-80" onSubmit={event => {
        event.preventDefault();
        const fields = new FormData(event.currentTarget);
        setCredentials({ username: fields.get('username'), password: fields.get('password') });
      }}>
        <h1 className="text-lg font-semibold">Internal data explorer</h1>
        <label className="block">Username<input name="username" autoComplete="username" required className="block border rounded p-2 w-full" /></label>
        <label className="block">Password<input name="password" type="password" autoComplete="current-password" required className="block border rounded p-2 w-full" /></label>
        <button className="bg-blue-600 text-white rounded px-4 py-2" type="submit">Sign in</button>
      </form>
    </div>
  );

  // ── render states ──
  if (loading)
    return <div className="min-h-screen flex items-center justify-center text-gray-500"><RefreshCw className="w-5 h-5 animate-spin mr-2" /> Loading export…</div>;

  if (error)
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="bg-white border border-red-200 rounded-xl p-6 max-w-md text-center">
          <AlertCircle className="w-8 h-8 text-red-500 mx-auto mb-2" />
          <p className="text-gray-800 font-medium mb-1">Could not load data</p>
          <p className="text-sm text-gray-500 mb-4">{error}</p>
          <button onClick={load} className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700">Retry</button>
          <button onClick={() => { setCredentials(null); setData(null); setError(null); }} className="ml-3 text-sm text-blue-600">Change credentials</button>
        </div>
      </div>
    );

  return (
    <div className="min-h-screen bg-gray-50">
      {/* header */}
      <header className="bg-white border-b border-gray-200 sticky top-0 z-30">
        <div className="max-w-[1600px] mx-auto px-6 py-3 flex items-center gap-3">
          <Database className="w-6 h-6 text-blue-600" />
          <div>
            <h1 className="font-bold text-gray-900 leading-tight">SnapTrade Data Platform</h1>
            <p className="text-xs text-gray-400">
              Exported {data?.exported_at ? new Date(data.exported_at).toLocaleString() : "—"} ·{" "}
              {(data?.users || []).length} users · {units.length} accounts
            </p>
          </div>
          <button onClick={refresh} disabled={refreshing}
            className="ml-auto inline-flex items-center gap-2 px-3 py-1.5 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50">
            <RefreshCw className={`w-4 h-4 ${refreshing ? "animate-spin" : ""}`} /> {refreshing ? "Refreshing…" : "Refresh export"}
          </button>
        </div>
      </header>

      <div className="max-w-[1600px] mx-auto px-6 py-5 flex gap-5">
        {/* sidebar: accounts */}
        <aside className="w-64 shrink-0">
          <div className="bg-white border border-gray-200 rounded-xl p-2 sticky top-20">
            <div className="px-2 py-1.5 text-xs font-semibold text-gray-400 flex items-center gap-1">
              <Users className="w-3.5 h-3.5" /> ACCOUNTS
            </div>
            <div className="space-y-0.5 max-h-[75vh] overflow-auto">
              {units.map((u) => (
                <button key={u.id} onClick={() => setSelUnit(u.id)}
                  className={`w-full text-left px-2.5 py-2 rounded-lg text-sm ${selUnit === u.id ? "bg-blue-50 ring-1 ring-blue-200" : "hover:bg-gray-50"}`}>
                  <div className="font-medium text-gray-800 truncate">{accLabel(u.acc)}</div>
                  <div className="text-[11px] text-gray-400 flex items-center gap-1">
                    <Badge color={u.source === "User" ? "blue" : "amber"}>{u.source}</Badge>
                    {u.user && <span className="truncate">{u.user}</span>}
                  </div>
                </button>
              ))}
            </div>
          </div>
        </aside>

        {/* main */}
        <main className="flex-1 min-w-0">
          {currentUnit && (
            <>
              <div className="mb-3">
                <h2 className="text-lg font-bold text-gray-900">{accLabel(currentUnit.acc)}</h2>
                <p className="text-sm text-gray-500">
                  {accInst(currentUnit.acc)} {currentUnit.user ? `· ${currentUnit.user}` : ""} · {currentUnit.source}
                </p>
              </div>

              {/* section tabs (dynamic) */}
              <div className="flex flex-wrap gap-1 mb-4 border-b border-gray-200">
                {sections.map((s) => {
                  const info = sectionToRows(currentUnit.acc[s]);
                  const count = info.kind === "list" ? info.rows.length : null;
                  return (
                    <button key={s} onClick={() => setSelSection(s)}
                      className={`px-3 py-2 text-sm font-medium border-b-2 -mb-px capitalize ${selSection === s ? "border-blue-600 text-blue-700" : "border-transparent text-gray-500 hover:text-gray-700"}`}>
                      {s}{count != null && <span className="ml-1.5 text-xs text-gray-400">({count})</span>}
                    </button>
                  );
                })}
                <button onClick={() => setSelSection("__raw__")}
                  className={`px-3 py-2 text-sm font-medium border-b-2 -mb-px ml-auto ${selSection === "__raw__" ? "border-blue-600 text-blue-700" : "border-transparent text-gray-400 hover:text-gray-600"}`}>
                  Raw JSON
                </button>
              </div>

              {/* section body */}
              <div className="bg-white border border-gray-200 rounded-xl p-4">
                {selSection === "__raw__" ? (
                  <pre className="text-xs overflow-auto max-h-[70vh] bg-gray-50 rounded-lg p-3">{JSON.stringify(currentUnit.acc, null, 2)}</pre>
                ) : sectionInfo ? (
                  <>
                    {sectionInfo.wrapper && (
                      <p className="text-xs text-gray-400 mb-2">unwrapped from <span className="font-mono">{`{ ${sectionInfo.wrapper}: [...] }`}</span></p>
                    )}
                    <DataTable rows={sectionInfo.rows} title={`${accLabel(currentUnit.acc)}_${selSection}`} />
                  </>
                ) : null}
              </div>
            </>
          )}
        </main>
      </div>
    </div>
  );
}
