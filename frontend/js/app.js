async function api(method, path, body) {
  if (!window.AutoMyAIAPI) throw new Error("AutoMyAIAPI 未加载");
  return AutoMyAIAPI.main.request(method, path, body);
}

function $(sel, root = document) { return root.querySelector(sel); }
function $all(sel, root = document) { return Array.from(root.querySelectorAll(sel)); }

function setActiveNav() {
  const page = document.body.dataset.page || "";
  $all(".nav a").forEach((a) => {
    const active = a.dataset.page === page;
    a.classList.toggle("active", active);
    if (active) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
}

function toast(msg, type = "info") {
  let el = $("#toast");
  if (!el) {
    el = document.createElement("div");
    el.id = "toast";
    el.style.cssText = "position:fixed;right:18px;bottom:18px;z-index:99;padding:12px 14px;border-radius:12px;background:#2f353c;color:#fff;box-shadow:0 10px 24px rgba(0,0,0,.2);max-width:360px;transition:opacity .2s";
    document.body.appendChild(el);
  }
  el.textContent = msg;
  el.style.background = type === "error" ? "#9b3b32" : type === "ok" ? "#2f6b3b" : "#2f353c";
  el.style.opacity = "1";
  clearTimeout(el._t);
  el._t = setTimeout(() => { el.style.opacity = "0"; }, 3200);
}

async function withBusy(button, work) {
  const original = button.textContent;
  button.disabled = true;
  button.classList.add("loading");
  try { return await work(); }
  finally { button.disabled = false; button.classList.remove("loading"); button.textContent = original; }
}

function safeJson(value) {
  return JSON.stringify(value, null, 2);
}

const THEME_STORAGE_KEY = "automyai-theme";

function applyTheme(theme) {
  const resolved = theme === "dark" ? "dark" : "light";
  document.documentElement.dataset.theme = resolved;
  const toggle = document.querySelector("[data-theme-toggle]");
  if (toggle) {
    toggle.setAttribute("aria-pressed", String(resolved === "dark"));
    toggle.textContent = resolved === "dark" ? "☀ 浅色" : "◐ 暗黑";
  }
}

function installThemeToggle() {
  const saved = localStorage.getItem(THEME_STORAGE_KEY);
  applyTheme(saved || "light");
  const brand = document.querySelector(".brand");
  if (!brand || brand.querySelector("[data-theme-toggle]")) return;
  const toggle = document.createElement("button");
  toggle.type = "button";
  toggle.className = "theme-toggle";
  toggle.dataset.themeToggle = "true";
  toggle.setAttribute("aria-label", "切换暗黑模式");
  toggle.onclick = () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    localStorage.setItem(THEME_STORAGE_KEY, next);
    applyTheme(next);
  };
  brand.appendChild(toggle);
  applyTheme(document.documentElement.dataset.theme);
}

function installNavigation() {
  const sidebar = document.querySelector(".sidebar");
  const brand = sidebar?.querySelector(".brand");
  const nav = sidebar?.querySelector(".nav");
  if (!sidebar || !brand || !nav) return;

  document.querySelectorAll('a[href^="/ui"], iframe[src^="/ui"]').forEach((element) => {
    const value = element.tagName === "IFRAME" ? element.getAttribute("src") : element.getAttribute("href");
    const relative = String(value || "").replace(/^\/ui/, "") || "/";
    if (element.tagName === "IFRAME") element.setAttribute("src", AutoMyAIAPI.uiURL(relative));
    else element.setAttribute("href", AutoMyAIAPI.uiURL(relative));
  });
  document.querySelectorAll('a[href^="/grok2"], iframe[src^="/grok2"]').forEach((element) => {
    const attr = element.tagName === "IFRAME" ? "src" : "href";
    const relative = String(element.getAttribute(attr) || "").replace(/^\/grok2/, "");
    element.setAttribute(attr, `${AutoMyAIAPI.uiBases.grok2}${relative}` || "/");
  });
  document.querySelectorAll('a[href^="/openai2"], iframe[src^="/openai2"]').forEach((element) => {
    const attr = element.tagName === "IFRAME" ? "src" : "href";
    const relative = String(element.getAttribute(attr) || "").replace(/^\/openai2/, "");
    element.setAttribute(attr, `${AutoMyAIAPI.uiBases.openai2}${relative}` || "/");
  });
  document.querySelectorAll('[data-service-link], [data-service-frame]').forEach((element) => {
    const service = element.dataset.serviceLink || element.dataset.serviceFrame;
    const base = AutoMyAIAPI.uiBases[service];
    if (base === undefined) return;
    const value = `${base}${element.dataset.servicePath || ''}` || '/';
    element.setAttribute(element.dataset.serviceFrame ? 'src' : 'href', value);
  });

  // Keep the shared navigation complete even when an older page carries a
  // stale inline copy. The overview page historically omitted Grok 2, which
  // made the entry appear only after navigating to another page.
  if (!nav.querySelector('a[href$="/grok2/accounts"]')) {
    const grok = nav.querySelector('a[data-page="grok"]');
    if (grok) {
      const grok2 = document.createElement("a");
      grok2.href = `${AutoMyAIAPI.uiBases.grok2}/accounts`;
      grok2.target = "_blank";
      grok2.rel = "noopener";
      grok2.className = "nav-external";
      grok2.innerHTML = '<span class="dot"></span><span>Grok 2</span><span class="nav-link-mark" aria-hidden="true">↗</span>';
      grok.insertAdjacentElement("afterend", grok2);
    }
  }

  nav.querySelectorAll('a[target="_blank"]').forEach((anchor) => {
    anchor.classList.add("nav-external");
    anchor.rel = "noopener";
    if (!anchor.querySelector(".nav-link-mark")) {
      const mark = document.createElement("span");
      mark.className = "nav-link-mark";
      mark.setAttribute("aria-hidden", "true");
      mark.textContent = "↗";
      anchor.appendChild(mark);
    }
  });

  const sections = [
    { label: "工作台", match: (a) => a.dataset.page === "overview" },
    { label: "注册与账号", match: (a) => a.dataset.page === "openai" },
    { label: "数据工具", match: (a) => a.dataset.page === "convert" },
    { label: "系统", match: (a) => a.dataset.page === "settings" },
  ];
  sections.forEach(({ label, match }) => {
    const anchor = Array.from(nav.querySelectorAll("a")).find(match);
    if (!anchor || anchor.previousElementSibling?.classList.contains("nav-label")) return;
    const heading = document.createElement("div");
    heading.className = "nav-label";
    heading.textContent = label;
    nav.insertBefore(heading, anchor);
  });

  if (!brand.querySelector("[data-nav-toggle]")) {
    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "nav-toggle";
    toggle.dataset.navToggle = "true";
    toggle.setAttribute("aria-label", "打开导航");
    toggle.setAttribute("aria-expanded", "false");
    toggle.innerHTML = "<span></span><span></span><span></span>";
    toggle.addEventListener("click", () => {
      const open = sidebar.classList.toggle("nav-open");
      toggle.setAttribute("aria-expanded", String(open));
      toggle.setAttribute("aria-label", open ? "关闭导航" : "打开导航");
    });
    brand.appendChild(toggle);
  }

  nav.querySelectorAll("a").forEach((anchor) => {
    anchor.addEventListener("click", () => sidebar.classList.remove("nav-open"));
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") sidebar.classList.remove("nav-open");
  });
}

function installRememberedDetails() {
  document.querySelectorAll("details[data-remember]").forEach((details) => {
    const key = `automyai-details:${details.dataset.remember}`;
    const saved = localStorage.getItem(key);
    if (saved !== null) details.open = saved === "open";
    details.addEventListener("toggle", () => {
      localStorage.setItem(key, details.open ? "open" : "closed");
    });
  });
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  }[char]));
}

function fmt(v) {
  if (v === null || v === undefined || v === "") return "—";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

async function loadExtensionStatus() {
  try {
    return await api("GET", "/extensions/status");
  } catch (e) {
    return null;
  }
}

window.HelpOAI = { api, $, $all, setActiveNav, toast, fmt, loadExtensionStatus, withBusy, safeJson, escapeHtml };
document.addEventListener("DOMContentLoaded", () => {
  setActiveNav();
  installThemeToggle();
  installNavigation();
  setActiveNav();
  installRememberedDetails();
});
