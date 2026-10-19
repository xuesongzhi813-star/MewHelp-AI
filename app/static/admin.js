/* ch03 后台共享外壳:注入复古琥珀+青样式、顶部导航与 fetch/作业轮询工具。
 * 所有样式选择器都限定在 .admin-* 类下,不影响现有客服页 index.html。 */
(() => {
  "use strict";

  const currentScript = document.currentScript;
  const page = (currentScript && currentScript.dataset && currentScript.dataset.page) || "";

  const NAV = [
    { key: "admin", label: "总览", href: "/admin" },
    { key: "kb", label: "知识库", href: "/kb" },
    { key: "chat", label: "客服对话", href: "/" },
  ];

  const STYLE = `
    .admin-body {
      --bg: #0d0a06;
      --bg-panel: rgba(30, 24, 14, 0.78);
      --glass: rgba(255, 200, 100, 0.045);
      --glass-border: rgba(255, 190, 90, 0.16);
      --amber: #fbbf24;
      --teal: #2dd4bf;
      --text: #f3ead7;
      --text-dim: #a08c6a;
      --danger: #f87171;
      --good: #34d399;
      margin: 0;
      min-height: 100vh;
      background: var(--bg);
      color: var(--text);
      font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei", system-ui, sans-serif;
      font-size: 14px;
      line-height: 1.55;
    }
    .admin-body, .admin-body * { box-sizing: border-box; }
    .admin-body::before {
      content: "";
      position: fixed; inset: 0;
      background:
        radial-gradient(ellipse 60% 40% at 72% -10%, rgba(251, 191, 36, 0.16), transparent 60%),
        radial-gradient(ellipse 50% 35% at 12% 110%, rgba(45, 212, 191, 0.12), transparent 60%),
        linear-gradient(rgba(255, 200, 100, 0.04) 1px, transparent 1px),
        linear-gradient(90deg, rgba(255, 200, 100, 0.04) 1px, transparent 1px);
      background-size: 100% 100%, 100% 100%, 44px 44px, 44px 44px;
      pointer-events: none;
      z-index: 0;
    }
    .admin-nav {
      position: sticky; top: 0; z-index: 20;
      display: flex; align-items: center; gap: 8px;
      padding: 12px 22px;
      background: rgba(13, 10, 6, 0.9);
      border-bottom: 1px solid var(--glass-border);
      backdrop-filter: blur(14px);
    }
    .admin-brand {
      display: flex; align-items: center; gap: 10px;
      margin-right: 14px;
      color: var(--text); text-decoration: none;
      font-size: 13px; font-weight: 600; letter-spacing: 1.5px;
    }
    .admin-hex {
      width: 30px; height: 30px;
      display: grid; place-items: center;
      background: linear-gradient(135deg, var(--amber), var(--teal));
      clip-path: polygon(50% 0, 100% 25%, 100% 75%, 50% 100%, 0 75%, 0 25%);
      color: #1a1206; font-size: 11px; font-weight: 800;
      box-shadow: 0 0 16px rgba(251, 191, 36, 0.45);
    }
    .admin-nav-link {
      color: var(--text-dim); text-decoration: none;
      font-size: 13px; letter-spacing: 1px;
      padding: 6px 14px; border-radius: 999px;
      border: 1px solid transparent;
      transition: color 0.2s, border-color 0.2s, background 0.2s;
    }
    .admin-nav-link:hover { color: var(--amber); }
    .admin-nav-link.admin-active {
      color: var(--amber);
      border-color: rgba(251, 191, 36, 0.4);
      background: rgba(251, 191, 36, 0.1);
      box-shadow: 0 0 14px rgba(251, 191, 36, 0.18);
    }
    .admin-nav-spacer { flex: 1; }
    .admin-nav-tag {
      color: var(--text-dim); font-size: 11px;
      letter-spacing: 2px;
    }
    .admin-wrap {
      position: relative; z-index: 1;
      max-width: 1180px; margin: 0 auto;
      padding: 26px 20px 80px;
    }
    .admin-hero { margin: 6px 2px 22px; }
    .admin-hero h1 {
      margin: 0; font-size: 22px; font-weight: 600; letter-spacing: 1px;
    }
    .admin-hero h1 span {
      background: linear-gradient(135deg, var(--amber), var(--teal));
      -webkit-background-clip: text; background-clip: text; color: transparent;
    }
    .admin-hero p { margin: 7px 0 0; color: var(--text-dim); font-size: 13px; }
    .admin-grid {
      display: grid; gap: 16px;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      align-items: start;
    }
    .admin-span-2 { grid-column: span 2; }
    .admin-card {
      position: relative;
      background: var(--bg-panel);
      border: 1px solid var(--glass-border);
      border-radius: 14px;
      padding: 18px 20px;
      backdrop-filter: blur(10px);
      box-shadow: 0 8px 26px rgba(0, 0, 0, 0.25);
    }
    .admin-card-head {
      display: flex; align-items: center; flex-wrap: wrap; gap: 10px;
      margin-bottom: 14px;
    }
    .admin-card-head h2 {
      margin: 0; font-size: 15px; font-weight: 600;
      letter-spacing: 1.5px; color: var(--amber);
    }
    .admin-card-head .admin-actions { margin-left: auto; }
    .admin-hint { color: var(--text-dim); font-size: 12px; letter-spacing: 0.3px; }
    .admin-note {
      margin: 12px 0 0; padding: 9px 12px;
      color: var(--text-dim); font-size: 12.5px;
      background: var(--glass);
      border: 1px solid var(--glass-border);
      border-radius: 8px;
    }
    .admin-note.admin-note-good { color: var(--teal); border-color: rgba(45, 212, 191, 0.3); }
    .admin-note.admin-note-warn { color: var(--amber); border-color: rgba(251, 191, 36, 0.3); }
    .admin-note.admin-note-bad { color: var(--danger); border-color: rgba(248, 113, 113, 0.35); }
    .admin-empty {
      margin: 4px 0; color: var(--text-dim); font-size: 12.5px;
    }
    .admin-error { color: var(--danger); }
    .admin-stat-row {
      display: flex; flex-wrap: wrap; gap: 10px;
    }
    .admin-stat {
      min-width: 108px; flex: 1 1 108px;
      padding: 10px 12px;
      background: var(--glass);
      border: 1px solid var(--glass-border);
      border-radius: 10px;
    }
    .admin-stat-label {
      display: block; color: var(--text-dim);
      font-size: 11px; letter-spacing: 1.2px; margin-bottom: 3px;
    }
    .admin-stat-value {
      display: block; color: var(--text);
      font-size: 19px; font-weight: 600; letter-spacing: 0.5px;
      font-variant-numeric: tabular-nums;
    }
    .admin-stat-value.admin-tone-good { color: var(--teal); }
    .admin-stat-value.admin-tone-warn { color: var(--amber); }
    .admin-stat-value.admin-tone-bad { color: var(--danger); }
    .admin-badge {
      display: inline-flex; align-items: center; gap: 6px;
      padding: 3px 10px; border-radius: 999px;
      border: 1px solid var(--glass-border);
      color: var(--text-dim); font-size: 11px; letter-spacing: 1px;
    }
    .admin-badge.admin-tone-good { color: var(--teal); border-color: rgba(45, 212, 191, 0.4); background: rgba(45, 212, 191, 0.08); }
    .admin-badge.admin-tone-warn { color: var(--amber); border-color: rgba(251, 191, 36, 0.4); background: rgba(251, 191, 36, 0.08); }
    .admin-badge.admin-tone-bad { color: var(--danger); border-color: rgba(248, 113, 113, 0.4); background: rgba(248, 113, 113, 0.08); }
    .admin-actions {
      display: flex; align-items: center; flex-wrap: wrap; gap: 8px;
    }
    .admin-btn {
      font: inherit; font-size: 12.5px; letter-spacing: 0.5px;
      color: var(--amber); cursor: pointer;
      background: rgba(251, 191, 36, 0.07);
      border: 1px solid rgba(251, 191, 36, 0.38);
      border-radius: 9px; padding: 7px 14px;
      transition: background 0.2s, box-shadow 0.2s, transform 0.2s;
    }
    .admin-btn:hover:not(:disabled) {
      background: rgba(251, 191, 36, 0.14);
      box-shadow: 0 0 14px rgba(251, 191, 36, 0.22);
    }
    .admin-btn.admin-btn-teal {
      color: var(--teal);
      border-color: rgba(45, 212, 191, 0.38);
      background: rgba(45, 212, 191, 0.07);
    }
    .admin-btn.admin-btn-teal:hover:not(:disabled) {
      background: rgba(45, 212, 191, 0.14);
      box-shadow: 0 0 14px rgba(45, 212, 191, 0.22);
    }
    .admin-btn.admin-btn-danger {
      color: var(--danger);
      border-color: rgba(248, 113, 113, 0.42);
      background: rgba(248, 113, 113, 0.07);
    }
    .admin-btn.admin-btn-danger:hover:not(:disabled) {
      background: rgba(248, 113, 113, 0.15);
      box-shadow: 0 0 14px rgba(248, 113, 113, 0.24);
    }
    .admin-btn.admin-btn-ghost {
      color: var(--text-dim);
      border-color: var(--glass-border);
      background: transparent;
    }
    .admin-btn:disabled { opacity: 0.4; cursor: not-allowed; transform: none; }
    .admin-form-row {
      display: flex; flex-wrap: wrap; align-items: flex-end; gap: 10px;
      margin-bottom: 12px;
    }
    .admin-field { display: flex; flex-direction: column; gap: 5px; }
    .admin-field > span { color: var(--text-dim); font-size: 11px; letter-spacing: 1.2px; }
    .admin-grow { flex: 1 1 240px; }
    .admin-topk { width: 90px; }
    .admin-input, .admin-select, .admin-textarea {
      font: inherit; font-size: 13px;
      color: var(--text);
      background: rgba(255, 255, 255, 0.035);
      border: 1px solid var(--glass-border);
      border-radius: 8px;
      padding: 8px 10px;
      outline: none;
      transition: border-color 0.2s, box-shadow 0.2s;
    }
    .admin-input:focus, .admin-select:focus, .admin-textarea:focus {
      border-color: rgba(45, 212, 191, 0.55);
      box-shadow: 0 0 14px rgba(45, 212, 191, 0.14);
    }
    .admin-select option { background: #171208; color: var(--text); }
    .admin-textarea {
      width: 100%; min-height: 190px; resize: vertical;
      line-height: 1.7;
      font-family: "Cascadia Code", Consolas, "Courier New", monospace;
      font-size: 12.5px;
    }
    .admin-check {
      display: inline-flex; align-items: center; gap: 7px;
      color: var(--text-dim); font-size: 12.5px; cursor: pointer;
      user-select: none;
    }
    .admin-check input { accent-color: var(--teal); }
    .admin-list { display: flex; flex-direction: column; gap: 8px; margin-top: 10px; }
    .admin-list-item {
      display: flex; align-items: flex-start; gap: 12px;
      padding: 10px 12px;
      background: var(--glass);
      border: 1px solid var(--glass-border);
      border-radius: 9px;
    }
    .admin-row-main { flex: 1; min-width: 0; }
    .admin-row-title {
      display: flex; align-items: center; flex-wrap: wrap; gap: 8px;
      color: var(--text); font-size: 13px; font-weight: 600;
    }
    .admin-row-sub {
      margin-top: 3px;
      color: var(--text-dim); font-size: 12px;
      word-break: break-word;
    }
    .admin-row-meta {
      margin-top: 5px; display: flex; flex-wrap: wrap; gap: 6px;
    }
    .admin-result {
      margin-top: 12px; padding: 12px;
      background: rgba(0, 0, 0, 0.22);
      border: 1px solid var(--glass-border);
      border-radius: 10px;
    }
    .admin-result-title { color: var(--teal); font-size: 12.5px; margin-bottom: 8px; }
    .admin-chunk {
      margin-top: 6px; padding: 8px 10px;
      background: var(--glass);
      border: 1px solid var(--glass-border);
      border-radius: 8px;
    }
    .admin-chunk summary {
      cursor: pointer; color: var(--text); font-size: 12.5px;
      word-break: break-word;
    }
    .admin-chunk pre {
      margin: 8px 0 0; color: var(--text-dim);
      font: 12px/1.65 "Cascadia Code", Consolas, "Courier New", monospace;
      white-space: pre-wrap; word-break: break-word;
    }
    .admin-log {
      margin: 10px 0 0; padding: 12px;
      max-height: 230px; overflow: auto;
      background: #080604;
      border: 1px solid var(--glass-border);
      border-radius: 9px;
      color: #c9b98f; font: 12px/1.6 "Cascadia Code", Consolas, "Courier New", monospace;
      white-space: pre-wrap; word-break: break-word;
    }
    .admin-row-toggle {
      display: flex; align-items: center; gap: 10px; width: 100%;
      font: inherit; text-align: left; cursor: pointer;
      color: var(--text); background: transparent; border: 0; padding: 0;
    }
    .admin-row-toggle .admin-row-main { flex: 1; min-width: 0; }
    .admin-scroll { max-height: 420px; overflow: auto; }
    .admin-link {
      display: inline-block; margin-top: 12px;
      color: var(--teal); text-decoration: none;
      font-size: 12.5px; letter-spacing: 0.5px;
    }
    .admin-link:hover { text-decoration: underline; text-shadow: 0 0 12px rgba(45, 212, 191, 0.4); }
    .admin-card-muted { opacity: 0.82; }
    .admin-placeholder {
      margin: 6px 0 0; padding: 18px 14px;
      border: 1px dashed var(--glass-border);
      border-radius: 10px; text-align: center;
      color: var(--text-dim); font-size: 12.5px; letter-spacing: 1px;
    }
    @media (max-width: 860px) {
      .admin-grid { grid-template-columns: minmax(0, 1fr); }
      .admin-span-2 { grid-column: span 1; }
      .admin-nav { flex-wrap: wrap; }
    }
  `;

  // ── 样式注入 ────────────────────────────────────────────────
  const style = document.createElement("style");
  style.dataset.adminStyle = "ch03";
  style.textContent = STYLE;
  document.head.appendChild(style);

  // ── 顶部导航 ────────────────────────────────────────────────
  function mountNav() {
    if (!document.body || document.querySelector(".admin-nav")) return;
    const nav = document.createElement("nav");
    nav.className = "admin-nav";

    const brand = document.createElement("a");
    brand.className = "admin-brand";
    brand.href = "/admin";
    const hex = document.createElement("span");
    hex.className = "admin-hex";
    hex.textContent = "EL";
    const brandText = document.createElement("span");
    brandText.textContent = "ETHAN_LEO · 后台";
    brand.append(hex, brandText);

    nav.appendChild(brand);
    NAV.forEach((item) => {
      const link = document.createElement("a");
      link.className = "admin-nav-link";
      if (item.key === page) link.classList.add("admin-active");
      link.href = item.href;
      link.textContent = item.label;
      nav.appendChild(link);
    });

    const spacer = document.createElement("span");
    spacer.className = "admin-nav-spacer";
    const tag = document.createElement("span");
    tag.className = "admin-nav-tag";
    tag.textContent = "KNOWLEDGE OPS";
    nav.append(spacer, tag);

    document.body.insertBefore(nav, document.body.firstChild);
  }

  if (document.body) {
    mountNav();
  } else {
    document.addEventListener("DOMContentLoaded", mountNav, { once: true });
  }

  // ── fetch 工具:非 2xx 时把后端 JSON 的 detail 抛成 Error ────────
  async function api(path, options = {}) {
    const init = { ...options };
    if (
      init.body !== undefined &&
      init.body !== null &&
      typeof init.body !== "string"
    ) {
      init.body = JSON.stringify(init.body);
      init.headers = { "Content-Type": "application/json", ...(init.headers || {}) };
    }

    const response = await fetch(path, init);
    const raw = await response.text();
    let body = null;
    if (raw) {
      try {
        body = JSON.parse(raw);
      } catch {
        body = raw;
      }
    }

    if (!response.ok) {
      let detail = body && typeof body === "object" ? body.detail : null;
      if (Array.isArray(detail)) {
        detail = detail
          .map((item) => (item && (item.msg || item.message)) || JSON.stringify(item))
          .join("; ");
      }
      if (!detail) detail = `HTTP ${response.status}`;
      const error = new Error(String(detail));
      error.status = response.status;
      error.body = body;
      throw error;
    }
    return body;
  }

  // ── 作业轮询:每 3 秒拉一次状态,回调拿到 log_tail ─────────────
  function pollJob(name, onUpdate) {
    const endpoint = `/api/jobs/${encodeURIComponent(name)}`;
    return new Promise((resolve) => {
      const tick = async () => {
        try {
          const state = await api(endpoint);
          if (typeof onUpdate === "function") onUpdate(state);
          if (state && state.status === "running") {
            setTimeout(tick, 3000);
            return;
          }
          resolve(state);
        } catch (error) {
          const state = {
            name,
            status: "failed",
            started_at: null,
            finished_at: null,
            exit_code: null,
            log_tail: "",
            error: error.message,
          };
          if (typeof onUpdate === "function") onUpdate(state);
          resolve(state);
        }
      };
      tick();
    });
  }

  // ── 数字格式化:null/读不到 用「—」,绝不伪装成 0 ───────────────
  function fmt(value) {
    if (value === null || value === undefined || value === "" || typeof value === "boolean") {
      return "—";
    }
    const number = Number(value);
    if (!Number.isFinite(number)) return String(value);
    return number.toLocaleString("zh-CN");
  }

  window.Admin = { api, pollJob, fmt };
})();
