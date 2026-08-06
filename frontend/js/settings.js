(() => {
const { api, toast, escapeHtml, withBusy } = HelpOAI;

const SETTINGS_FIELDS = [
  "CPA_ENABLED", "CPA_AUTH_DIR", "CPA_REMOTE_URL",
  "GROK2API_BASE_URL", "GROK2API_POOL",
  "DOMAIN_MAIL_ROOT", "DOMAIN_MAIL_PREFER_SUBDOMAIN", "MAIL_PREFER_INVENTORY",
  "MAIL_SOURCE_GROUP_NAME", "MAIL_PENDING_GROUP_NAME", "MAIL_SUCCESS_GROUP_NAME", "MAIL_BAD_GROUP_NAME",
  "GROK_MAIL_PENDING_GROUP_NAME", "GROK_MAIL_SUCCESS_GROUP_NAME", "SUB2API_API_URL", "SUB2API_IMPORT_GROUP_NAMES",
	"SIGNUP_PROXY_MODE", "SIGNUP_PROXY_REGION", "SIGNUP_PROXY_CUSTOM_URL", "CLIPROXY_PROXY_URL",
	"SUB2API_PROXY_REGION", "SUB2API_IMPORT_USE_SIGNUP_PROXY",
	"OAI_FINGERPRINT_CLOUD_ENABLED", "OAI_FINGERPRINT_CLOUD_API_BASE_URL",
	"OAI_FINGERPRINT_CLOUD_HEADERS_FILE", "OAI_FINGERPRINT_CLOUD_INCLUDE_MAC",
	"ROXY_OPENAPI_ENABLED", "ROXY_OPENAPI_URL", "ROXY_OPENAPI_KEY_FILE", "ROXY_OPENAPI_TIMEOUT_SECONDS",
];
const SECRET_FIELDS = new Set(["CPA_MANAGEMENT_KEY", "CPA_API_KEY", "GROK2API_ADMIN_KEY"]);
const BOOLEAN_FIELDS = new Set([
	"OAI_FINGERPRINT_CLOUD_ENABLED", "OAI_FINGERPRINT_CLOUD_INCLUDE_MAC", "ROXY_OPENAPI_ENABLED",
	"SUB2API_IMPORT_USE_SIGNUP_PROXY",
]);
const REGION_OPTIONS = ["US", "HK", "JP", "SG", "TW", "UK", "KR", "MY", "NL", "DE"];
const SELECT_OPTIONS = {
	SIGNUP_PROXY_MODE: [
		["custom", "必须填写自定义代理（默认，无代理不启动）"],
		["cliproxy", "Cliproxy 家宽动态代理"],
	],
	SIGNUP_PROXY_REGION: REGION_OPTIONS.map((region) => [region, region]),
	SUB2API_PROXY_REGION: REGION_OPTIONS.map((region) => [region, region]),
};
const FIELD_LABELS = {
	SIGNUP_PROXY_MODE: "注册代理模式",
	SIGNUP_PROXY_REGION: "本地注册代理地区",
	SIGNUP_PROXY_CUSTOM_URL: "自定义注册代理完整链接",
	CLIPROXY_PROXY_URL: "Cliproxy 完整动态代理链接",
	SUB2API_PROXY_REGION: "Sub2API 默认代理地区",
	SUB2API_IMPORT_USE_SIGNUP_PROXY: "导入 Sub2API 时跟随本次注册代理（批量开关）",
	OAI_FINGERPRINT_CLOUD_ENABLED: "使用云端基础指纹 API",
	OAI_FINGERPRINT_CLOUD_API_BASE_URL: "云端基础指纹 API 前缀",
	OAI_FINGERPRINT_CLOUD_HEADERS_FILE: "云端授权 Headers 文件（0600）",
	OAI_FINGERPRINT_CLOUD_INCLUDE_MAC: "云端同时申请 MAC 记录",
	ROXY_OPENAPI_ENABLED: "启用 Roxy 官方本地 OpenAPI",
	ROXY_OPENAPI_URL: "Roxy OpenAPI 地址",
	ROXY_OPENAPI_KEY_FILE: "Roxy OpenAPI Key 文件（0600）",
	ROXY_OPENAPI_TIMEOUT_SECONDS: "Roxy OpenAPI 超时秒数",
};
const fields = document.getElementById("fields");
const output = document.getElementById("out");
const saveButton = document.getElementById("saveBtn");
const trafficToggle = document.getElementById("trafficMeterEnabled");
const trafficHistory = document.getElementById("trafficHistory");
const tmStatus = document.getElementById("tmStatus");
const tmCount = document.getElementById("tmCount");
const tmLast = document.getElementById("tmLast");
const proxyCheckOut = document.getElementById("proxyCheckOut");
window.__helpOaiSettingsState = "loaded";

function parseEnabled(value) {
  const text = String(value ?? "").trim().toLowerCase();
  return text === "1" || text === "true" || text === "yes" || text === "on";
}

function render(settings, secretsConfigured = {}) {
  const regular = SETTINGS_FIELDS.map((key) => {
	const label = FIELD_LABELS[key] || key;
	if (SELECT_OPTIONS[key]) {
	  const current = String(settings[key] ?? "");
	  const options = SELECT_OPTIONS[key].map(([value, text]) => (
		`<option value="${escapeHtml(value)}"${current === value ? " selected" : ""}>${escapeHtml(text)}</option>`
	  )).join("");
	  return `<label>${escapeHtml(label)}<select data-k="${escapeHtml(key)}">${options}</select></label>`;
	}
	if (BOOLEAN_FIELDS.has(key)) {
	  const enabled = parseEnabled(settings[key]);
	  return `<label>${escapeHtml(label)}<select data-k="${escapeHtml(key)}">` +
		`<option value="false"${enabled ? "" : " selected"}>关闭</option>` +
		`<option value="true"${enabled ? " selected" : ""}>开启</option></select></label>`;
	}
	return `<label>${escapeHtml(label)}<input data-k="${escapeHtml(key)}" value="${escapeHtml(settings[key] ?? "")}"/></label>`;
  });
  const secret = [...SECRET_FIELDS].map((key) => (
    `<label>${escapeHtml(key)}（${secretsConfigured[key] ? "已配置；留空保持不变" : "未配置"}）` +
    `<input data-k="${escapeHtml(key)}" type="password" autocomplete="new-password" placeholder="输入新值以替换"/></label>`
  ));
  fields.innerHTML = [...regular, ...secret].join("");
  if (trafficToggle) {
    trafficToggle.checked = parseEnabled(settings.TRAFFIC_METER_ENABLED);
  }
}

function fieldValue(key) {
  const input = fields.querySelector(`[data-k="${key}"]`);
  return input ? String(input.value || "").trim() : "";
}

function regionalProxy(region) {
  const regions = ["US", "HK", "JP", "SG", "TW", "UK", "KR", "MY", "NL", "DE"];
  const index = regions.indexOf(String(region || "JP").toUpperCase());
  return `http://172.19.0.1:${index >= 0 ? 7901 + index : 7903}`;
}

function selectedSignupProxy() {
  const mode = fieldValue("SIGNUP_PROXY_MODE") || "custom";
  if (mode === "cliproxy") return fieldValue("CLIPROXY_PROXY_URL");
  // 无默认 Mihomo。没填就返回空，检测/启动侧会拒绝。
  return fieldValue("SIGNUP_PROXY_CUSTOM_URL") || fieldValue("UC_SIGNUP_PROXY") || fieldValue("BROWSER_PROXY") || "";
}

async function checkProxy(proxyUrl) {
  if (!proxyUrl) throw new Error("请先填写代理链接");
  const response = await api("POST", "/proxy/check", { proxyUrl });
  const result = response.result || {};
  proxyCheckOut.textContent = [
    `代理：${result.proxyUrl || proxyUrl}`,
    `出口：${result.country || "-"} ${result.countryCode || ""} / ${result.region || "-"} / ${result.city || "-"}`,
    `IP：${result.ip || "-"}`,
    `网络：${result.isp || result.org || "-"}`,
    `认证：${result.authenticated ? "有用户名/密码" : "无"}`,
  ].join("\n");
}

function fmtItem(item) {
  if (!item || typeof item !== "object") return "";
  const parts = [
    item.started_at || item.id || "—",
    item.service || "?",
    item.status || "",
    item.bytes_total_h || (item.bytes_total != null ? `${item.bytes_total} B` : "0 B"),
    item.upstream ? `via ${item.upstream}` : "",
  ];
  return parts.filter(Boolean).join(" · ");
}

async function loadTraffic() {
  if (!trafficHistory) return;
  try {
    const data = await api("GET", "/traffic?tail=40");
    const enabled = !!data.enabled;
    if (trafficToggle && document.activeElement !== trafficToggle) {
      trafficToggle.checked = enabled;
    }
    if (tmStatus) tmStatus.textContent = enabled ? "已启用（默认开）" : "未启用（默认关）";
    const items = Array.isArray(data.items) ? data.items : [];
    if (tmCount) tmCount.textContent = String(items.length);
    const last = items[0] || data.current || null;
    if (tmLast) {
      tmLast.textContent = last
        ? (last.bytes_total_h || `${last.bytes_total || 0} B`)
        : "暂无";
    }
    if (!items.length && !data.current) {
      trafficHistory.textContent = "暂无记录。在设置页开启「启用流量统计」后，去 OpenAI3 / Grok TTK / Grok2 注册机跑任务会写入这里。";
      return;
    }
    const lines = [];
    if (data.current) lines.push("[当前] " + fmtItem(data.current));
    items.forEach((item, idx) => lines.push(`${idx + 1}. ${fmtItem(item)}`));
    trafficHistory.textContent = lines.join("\n");
  } catch (error) {
    if (tmStatus) tmStatus.textContent = "读取失败";
    trafficHistory.textContent = `读取 /api/traffic 失败：${error.message}`;
  }
}

async function loadSettings() {
  window.__helpOaiSettingsState = "loading";
  try {
    const response = await api("GET", "/settings");
    render(response.settings || {}, response.secretsConfigured || {});
    output.textContent = "配置已加载";
    window.__helpOaiSettingsState = "ready";
  } catch (error) {
    fields.innerHTML = SETTINGS_FIELDS.map((key) => (
      `<label>${escapeHtml(key)}<input data-k="${escapeHtml(key)}" value=""/></label>`
    )).join("");
    output.textContent = "配置读取失败；请检查本机服务后重试。";
    window.__helpOaiSettingsState = `error: ${error.message}`;
    toast(`读取 /settings 失败：${error.message}`, "error");
  }
  await loadTraffic();
}

async function saveTrafficOnly() {
  const enabled = !!(trafficToggle && trafficToggle.checked);
  await api("POST", "/settings", { TRAFFIC_METER_ENABLED: enabled ? "true" : "false" });
  toast(enabled ? "流量统计已开启（默认）" : "流量统计已关闭（默认）", "ok");
  await loadTraffic();
}

document.getElementById("reloadBtn").onclick = loadSettings;
saveButton.onclick = async () => {
  const payload = {};
	fields.querySelectorAll("input[data-k], select[data-k]").forEach((input) => {
    if (!SECRET_FIELDS.has(input.dataset.k) || input.value) payload[input.dataset.k] = input.value;
  });
  if (trafficToggle) {
    payload.TRAFFIC_METER_ENABLED = trafficToggle.checked ? "true" : "false";
  }
  try {
    await withBusy(saveButton, async () => {
      const response = await api("POST", "/settings", payload);
      render(response.settings || {}, response.secretsConfigured || {});
      output.textContent = "配置已保存";
      toast("已保存", "ok");
      await loadTraffic();
    });
  } catch (error) {
    toast(error.message, "error");
  }
};

const trafficSaveBtn = document.getElementById("trafficSaveBtn");
const trafficRefreshBtn = document.getElementById("trafficRefreshBtn");
if (trafficSaveBtn) {
  trafficSaveBtn.onclick = async () => {
    try {
      await withBusy(trafficSaveBtn, saveTrafficOnly);
    } catch (error) {
      toast(error.message, "error");
    }
  };
}
if (trafficRefreshBtn) {
  trafficRefreshBtn.onclick = () => loadTraffic();
}

const checkProxyBtn = document.getElementById("checkProxyBtn");
const checkCliproxyBtn = document.getElementById("checkCliproxyBtn");
if (checkProxyBtn) {
  checkProxyBtn.onclick = async () => {
    try { await withBusy(checkProxyBtn, () => checkProxy(selectedSignupProxy())); }
    catch (error) { proxyCheckOut.textContent = error.message; toast(error.message, "error"); }
  };
}
if (checkCliproxyBtn) {
  checkCliproxyBtn.onclick = async () => {
    try { await withBusy(checkCliproxyBtn, () => checkProxy(fieldValue("CLIPROXY_PROXY_URL"))); }
    catch (error) { proxyCheckOut.textContent = error.message; toast(error.message, "error"); }
  };
}

loadSettings();
})();
