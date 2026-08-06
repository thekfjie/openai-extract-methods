import { PRESETS } from "./catalog.mjs";
import { generateProfiles } from "./generator.mjs";

export const ROXY_CLOUD_ENDPOINTS = Object.freeze({
  uaWebgl: "/user_get_ua_webgl_v2",
  deviceName: "/user_get_device_name_v2",
  macAddress: "/user_get_mac_addr_v2"
});

function nonEmptyString(value) {
  return typeof value === "string" && value.trim() !== "" ? value.trim() : undefined;
}

function optionalNumber(value) {
  if (value === undefined || value === null || value === "") return undefined;
  const number = Number(value);
  return Number.isFinite(number) ? number : undefined;
}

function browserVersionFromUa(userAgent, engine) {
  const patterns = engine === "Firefox" ? [/Firefox\/([\d.]+)/, /FxiOS\/([\d.]+)/] : [/Chrome\/([\d.]+)/, /CriOS\/([\d.]+)/];
  for (const pattern of patterns) {
    const match = userAgent.match(pattern);
    if (match) return match[1];
  }
  return undefined;
}

function normalizeMac(value) {
  const compact = nonEmptyString(value)?.replace(/-/g, ":").toUpperCase();
  return compact && /^([0-9A-F]{2}:){5}[0-9A-F]{2}$/.test(compact) ? compact : undefined;
}

function normalizeRecord(raw, engine) {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) throw new Error("Cloud base record must be an object");
  const userAgent = nonEmptyString(raw.userAgentNew ?? raw.userAgent);
  const browserVersion = nonEmptyString(raw.userAgentVersion ?? raw.browserVersion ?? raw.chromeVersion) ?? (userAgent ? browserVersionFromUa(userAgent, engine) : undefined);
  const record = {
    userAgent,
    browserVersion,
    webglVendor: nonEmptyString(raw.webGLManufacturer ?? raw.webglVendor ?? raw.webGLVendor),
    webglRenderer: nonEmptyString(raw.webGLRender ?? raw.webglRenderer ?? raw.webGLRenderer),
    computerName: nonEmptyString(raw.computerName ?? raw.deviceName),
    macAddress: normalizeMac(raw.macAddress ?? raw.macAddr),
    navigatorPlatform: nonEmptyString(raw.navigatorPlatform),
    platform: nonEmptyString(raw.platform),
    platformVersion: nonEmptyString(raw.platformVersion),
    architecture: nonEmptyString(raw.architecture),
    model: nonEmptyString(raw.model),
    webgpuMode: nonEmptyString(raw.webGpu ?? raw.webgpuMode),
    webgpuVendor: nonEmptyString(raw.webGpuVendor ?? raw.webgpuVendor),
    screenWidth: optionalNumber(raw.resolutionX ?? raw.screenWidth),
    screenHeight: optionalNumber(raw.resolutionY ?? raw.screenHeight),
    devicePixelRatio: optionalNumber(raw.devicePixelRatio)
  };
  for (const key of ["userAgent", "browserVersion", "webglVendor", "webglRenderer", "computerName"]) {
    if (!record[key]) throw new Error(`Cloud base record is missing ${key}`);
  }
  return record;
}

function assertEngineUa(record, engine) {
  const patterns = engine === "Firefox" ? [/Firefox\//, /FxiOS\//] : [/Chrome\//, /CriOS\//];
  if (!patterns.some((pattern) => pattern.test(record.userAgent))) throw new Error(`Cloud user agent does not match ${engine}`);
}

function updateChromeMetadata(metadata, record) {
  const major = record.browserVersion.split(".")[0];
  const next = structuredClone(metadata);
  next.brands = next.brands.map((item) => ({ ...item, version: item.brand === "Not/A)Brand" ? "24" : major }));
  next.fullVersionList = next.fullVersionList.map((item) => ({ ...item, version: item.brand === "Not/A)Brand" ? "24.0.0.0" : record.browserVersion }));
  if (record.platform) next.platform = record.platform;
  if (record.platformVersion) next.platformVersion = record.platformVersion;
  if (record.architecture) next.architecture = record.architecture;
  if (record.model !== undefined) next.model = record.model;
  return next;
}

export function applyAuthorizedBaseRecord(profile, rawRecord, {
  providerName = "authorized-provider",
  deterministic = false,
  includeMac = true
} = {}) {
  const record = normalizeRecord(rawRecord, profile.engine.family);
  assertEngineUa(record, profile.engine.family);
  if (includeMac && !record.macAddress) throw new Error("Cloud base record is missing a valid macAddress");
  const next = structuredClone(profile);
  next.engine.version = record.browserVersion;
  next.engine.userAgent = record.userAgent;
  if (next.engine.userAgentMetadata) next.engine.userAgentMetadata = updateChromeMetadata(next.engine.userAgentMetadata, record);
  if (record.navigatorPlatform) next.navigator.platform = record.navigatorPlatform;
  next.graphics.webglVendor = record.webglVendor;
  next.graphics.webglRenderer = record.webglRenderer;
  if (record.webgpuMode) next.graphics.webgpu.mode = record.webgpuMode;
  if (record.webgpuVendor) next.graphics.webgpu.vendor = record.webgpuVendor;
  next.machine.computerName = record.computerName;
  if (includeMac && record.macAddress) next.machine.macAddress = record.macAddress;
  if (Number.isInteger(record.screenWidth) && record.screenWidth > 0) {
    next.screen.width = record.screenWidth;
    next.screen.availWidth = record.screenWidth;
  }
  if (Number.isInteger(record.screenHeight) && record.screenHeight > 0) {
    const adjustment = next.navigator.mobile || next.engine.family === "Firefox" ? 0 : 38;
    next.screen.height = record.screenHeight;
    next.screen.availHeight = Math.max(0, record.screenHeight - adjustment);
  }
  if (record.devicePixelRatio && record.devicePixelRatio > 0) next.screen.devicePixelRatio = record.devicePixelRatio;
  next.generator.deterministic = Boolean(deterministic);
  next.generator.baseDataSource = "authorized-provider";
  next.generator.provider = String(providerName);
  return next;
}

function normalizeBaseUrl(value) {
  if (!nonEmptyString(value)) throw new Error("Cloud provider baseUrl is required");
  const url = new URL(value);
  if (!new Set(["https:", "http:"]).has(url.protocol)) throw new Error("Cloud provider baseUrl must use HTTP or HTTPS");
  return url.toString().replace(/\/$/, "");
}

function validateHeaders(headers) {
  if (!headers || typeof headers !== "object" || Array.isArray(headers)) throw new Error("Cloud provider headers must be an object");
  return Object.fromEntries(Object.entries(headers).map(([name, value]) => {
    if (typeof value !== "string") throw new Error(`Cloud provider header ${name} must be a string`);
    return [name, value];
  }));
}

export function createRoxyHttpBaseRecordProvider({
  baseUrl,
  headers = {},
  fetchImpl = globalThis.fetch,
  timeoutMs = 15_000,
  name = "roxy-authorized-http"
} = {}) {
  if (typeof fetchImpl !== "function") throw new Error("A fetch implementation is required");
  const normalizedBaseUrl = normalizeBaseUrl(baseUrl);
  const normalizedHeaders = validateHeaders(headers);
  if (!Number.isInteger(timeoutMs) || timeoutMs < 1) throw new Error("timeoutMs must be a positive integer");

  async function request(path, query) {
    const url = new URL(`${normalizedBaseUrl}${path}`);
    for (const [key, value] of Object.entries(query)) url.searchParams.set(key, String(value));
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetchImpl(url, {
        method: "GET",
        headers: { accept: "application/json", source: "api", ...normalizedHeaders },
        signal: controller.signal
      });
      if (!response.ok) throw new Error(`Authorized cloud request failed with HTTP ${response.status}`);
      const body = await response.json();
      if (body?.code !== 0 || !Array.isArray(body?.data)) throw new Error(`Authorized cloud request failed: ${body?.msg ?? "invalid response"}`);
      return body.data;
    } finally {
      clearTimeout(timer);
    }
  }

  return Object.freeze({
    name,
    deterministic: false,
    async getRecords({ preset, count, browserVersion, includeMac = true }) {
      const template = PRESETS[preset];
      if (!template) throw new Error(`Unknown preset: ${preset}`);
      const coreVersion = browserVersion ?? template.browserVersion;
      const common = { coreType: template.engine, count };
      const requests = [
        request(ROXY_CLOUD_ENDPOINTS.uaWebgl, {
          os: template.os,
          osVersion: template.osVersion,
          coreVersion,
          ...common
        }),
        request(ROXY_CLOUD_ENDPOINTS.deviceName, { os: template.os, ...common })
      ];
      if (includeMac) requests.push(request(ROXY_CLOUD_ENDPOINTS.macAddress, common));
      const [uaRecords, deviceRecords, macRecords = []] = await Promise.all(requests);
      for (const [label, records] of [["UA/WebGL", uaRecords], ["device name", deviceRecords], ...(includeMac ? [["MAC", macRecords]] : [])]) {
        if (records.length < count) throw new Error(`${label} cloud response returned ${records.length}/${count} records`);
      }
      return Array.from({ length: count }, (_, index) => ({
        ...uaRecords[index],
        ...deviceRecords[index],
        ...(includeMac ? macRecords[index] : {})
      }));
    }
  });
}

export function createJsonBaseRecordProvider(records, { name = "authorized-json", deterministic = true } = {}) {
  if (!Array.isArray(records) || records.length === 0) throw new Error("records must be a non-empty array");
  const catalog = structuredClone(records);
  return Object.freeze({
    name,
    deterministic,
    async getRecords({ count }) {
      if (catalog.length < count) throw new Error(`JSON base-record catalog contains ${catalog.length}/${count} records`);
      return structuredClone(catalog.slice(0, count));
    }
  });
}

export async function generateProfilesWithProvider({ provider, includeMac = true, ...options } = {}) {
  if (!provider || typeof provider.getRecords !== "function") throw new Error("provider.getRecords is required");
  const count = options.count ?? 1;
  const profiles = generateProfiles({ ...options, count });
  const records = await provider.getRecords({
    preset: options.preset,
    count,
    browserVersion: options.browserVersion,
    includeMac
  });
  if (!Array.isArray(records) || records.length < count) throw new Error(`Provider returned ${records?.length ?? 0}/${count} records`);
  return profiles.map((profile, index) => applyAuthorizedBaseRecord(profile, records[index], {
    providerName: provider.name ?? "authorized-provider",
    deterministic: provider.deterministic,
    includeMac
  }));
}
