import { createHash } from "node:crypto";
import { FONT_CATALOGS, PRESETS } from "./catalog.mjs";
import {
  generateAllowFonts,
  generateCanvasNoise,
  generateClientRectsNoise,
  generateDisabledFonts,
  generateSpeechVoices,
  generateWebglNoise
} from "./original-algorithms.mjs";
import { createPrng, deriveSeed } from "./prng.mjs";

function stableId(seed, preset, version) {
  return createHash("sha256").update(`${preset}:${version}:${seed}`).digest("hex").slice(0, 16);
}

function localMac(prng) {
  const bytes = [0x02, ...Array.from({ length: 5 }, () => prng.int(0, 255))];
  return bytes.map((value) => value.toString(16).padStart(2, "0")).join(":").toUpperCase();
}

function computerName(os, prng) {
  if (os === "Windows") return `DESKTOP-${prng.hex(7)}`;
  if (os === "macOS") return `Mac-${prng.hex(8)}`;
  if (os === "Android") return `android-${prng.hex(10, false)}`;
  if (os === "IOS") return `iPhone-${prng.hex(8)}`;
  return `linux-${prng.hex(8, false)}`;
}

function pluginList(engine) {
  if (engine !== "Chrome") return [];
  const mime = [
    { mime_type: "application/pdf", file_extensions: "pdf" },
    { mime_type: "text/pdf", file_extensions: "pdf" }
  ];
  return ["PDF Viewer", "Chrome PDF Viewer", "Chromium PDF Viewer", "Microsoft Edge PDF Viewer", "WebKit built-in PDF"].map((name) => ({
    name,
    path: "internal-pdf-viewer",
    desc: "Portable Document Format|Portable Document Format",
    version: "",
    mime: mime.map((item) => ({ ...item }))
  }));
}

function userAgentForVersion(template, version) {
  if (template.engine === "Firefox") {
    return template.ua.replace(/rv:[\d.]+/, `rv:${version}`).replace(/Firefox\/[\d.]+/, `Firefox/${version}`).replace(/FxiOS\/[\d.]+/, `FxiOS/${version}`);
  }
  return template.ua.replace(/Chrome\/[\d.]+/, `Chrome/${version}`).replace(/CriOS\/[\d.]+/, `CriOS/${version}`);
}

function userAgentMetadata(template, version) {
  if (template.engine === "Firefox") return null;
  const major = version.split(".")[0];
  const brands = [
    { brand: "Chromium", version: major },
    { brand: "Google Chrome", version: major },
    { brand: "Not/A)Brand", version: "24" }
  ];
  return {
    brands,
    fullVersionList: brands.map((item) => ({
      ...item,
      version: item.brand === "Not/A)Brand" ? "24.0.0.0" : version
    })),
    platform: template.uaPlatform,
    mobile: template.mobile,
    platformVersion: template.platformVersion,
    architecture: template.architecture,
    ...(template.model ? { model: template.model } : { model: "" }),
    bitness: "64",
    wow64: false
  };
}

function featureEnabled(featureMode, prng, probability, originalDefault) {
  return featureMode === "original-defaults" ? originalDefault : prng.bool(probability);
}

function batteryProfile(template, featureMode, prng) {
  const enabled = featureEnabled(featureMode, prng, template.mobile ? 0.95 : 0.35, false);
  if (!enabled) return { enabled: false, charging: false, chargingTime: "", dischargingTime: "", level: "" };
  const charging = prng.bool(template.mobile ? 0.45 : 0.7);
  return {
    enabled: true,
    charging,
    chargingTime: charging ? prng.int(0, 7_200) : 0,
    dischargingTime: charging ? 0 : prng.int(3_600, 43_200),
    level: Number(prng.float(0.12, 1).toFixed(3))
  };
}

function networkProfile(template, featureMode, prng) {
  const enabled = featureEnabled(featureMode, prng, 0.85, false);
  if (!enabled) return { enabled: false, type: "", effectiveType: "", downlink: "", downlinkMax: "", rtt: "", saveData: false };
  const choices = template.mobile ? [
    { effectiveType: "4g", downlink: 12, downlinkMax: 100, rtt: 55 },
    { effectiveType: "3g", downlink: 1.8, downlinkMax: 8, rtt: 180 },
    { effectiveType: "slow-2g", downlink: 0.05, downlinkMax: 0.1, rtt: 2_000 }
  ] : [
    { effectiveType: "4g", downlink: 10, downlinkMax: 100, rtt: 50 },
    { effectiveType: "4g", downlink: 25, downlinkMax: 1_000, rtt: 25 },
    { effectiveType: "3g", downlink: 2.5, downlinkMax: 10, rtt: 140 }
  ];
  const selected = prng.pick(choices);
  return {
    enabled: true,
    type: template.mobile ? "cellular" : prng.pick(["wifi", "ethernet"]),
    ...selected,
    saveData: template.mobile && prng.bool(0.15)
  };
}

function bluetoothProfile(template, featureMode, prng) {
  const enabled = featureEnabled(featureMode, prng, template.mobile ? 0.95 : 0.55, false);
  return { enabled, adapterAvailable: enabled && prng.bool(0.9) };
}

function mediaDevicesProfile(template, featureMode, prng, seed) {
  const enabled = featureMode === "random" && prng.bool(template.mobile ? 0.7 : 0.55);
  if (!enabled) {
    return { enabled: false, devices: [{ deviceType: "audioinput", deviceId: "", label: "", groupId: "" }] };
  }
  const groupId = createHash("sha256").update(`${seed}:media-group`).digest("hex").slice(0, 32);
  const entries = template.mobile ? [
    ["audioinput", "Default - Microphone"],
    ["videoinput", "Front Camera"],
    ["videoinput", "Back Camera"]
  ] : [
    ["audioinput", "Default - Microphone"],
    ["audiooutput", "Default - Speakers"],
    ["videoinput", "Integrated Camera"]
  ];
  return {
    enabled: true,
    devices: entries.map(([deviceType, label], index) => ({
      deviceType,
      deviceId: createHash("sha256").update(`${seed}:${deviceType}:${index}`).digest("hex"),
      label,
      groupId
    }))
  };
}

function normalizeWebrtc(mode, remoteIp) {
  if (!new Set(["altered", "real", "disable"]).has(mode)) throw new Error(`Unsupported WebRTC mode: ${mode}`);
  if (mode === "altered" && !remoteIp) throw new Error("WebRTC altered mode requires remoteIp");
  return { mode, remoteIp: mode === "altered" ? remoteIp : null };
}

function geolocationProfile(engine, mode, latitude, longitude, accuracy) {
  const isFirefox = engine === "Firefox";
  const selectedMode = mode ?? (isFirefox ? "disable" : "block");
  const allowedModes = isFirefox ? ["prompt", "allow", "disable"] : ["prompt", "allow", "block"];
  if (!allowedModes.includes(selectedMode)) throw new Error(`${engine} does not support geolocation mode: ${selectedMode}`);
  const disabled = selectedMode === "block" || selectedMode === "disable";
  const fallback = isFirefox ? 0 : null;
  const normalize = (value) => {
    if (disabled) return fallback;
    if (value === undefined || value === null || value === "") return fallback;
    const number = Number(value);
    if (!Number.isFinite(number)) throw new Error("geolocation coordinates and accuracy must be finite numbers");
    return number;
  };
  return {
    mode: selectedMode,
    enableFakeLocationData: true,
    latitude: normalize(latitude),
    longitude: normalize(longitude),
    accuracy: normalize(accuracy),
    altitude: fallback
  };
}

export function generateProfile({
  preset,
  seed = "local-test",
  noise = true,
  featureMode = "random",
  browserVersion,
  webrtcMode = "disable",
  remoteIp = null,
  gpuAcceleration = true,
  sandboxPermission = false,
  colorScheme = "system",
  windowMode = "maximized",
  windowWidth = 1280,
  windowHeight = 720,
  windowPosition = { xRatio: 0, yRatio: 0, anchor: "tl" },
  geolocationMode,
  latitude,
  longitude,
  accuracy
} = {}) {
  const template = PRESETS[preset];
  if (!template) throw new Error(`Unknown preset: ${preset}`);
  if (!new Set(["random", "original-defaults"]).has(featureMode)) throw new Error(`Unsupported featureMode: ${featureMode}`);
  if (!new Set(["system", "light", "dark"]).has(colorScheme)) throw new Error(`Unsupported colorScheme: ${colorScheme}`);
  if (!new Set(["maximized", "normal"]).has(windowMode)) throw new Error(`Unsupported windowMode: ${windowMode}`);
  if (typeof gpuAcceleration !== "boolean") throw new Error("gpuAcceleration must be boolean");
  if (typeof sandboxPermission !== "boolean") throw new Error("sandboxPermission must be boolean");
  if (!Number.isInteger(windowWidth) || windowWidth < 1) throw new Error("windowWidth must be a positive integer");
  if (!Number.isInteger(windowHeight) || windowHeight < 1) throw new Error("windowHeight must be a positive integer");
  if (!windowPosition || !Number.isFinite(windowPosition.xRatio) || !Number.isFinite(windowPosition.yRatio)) throw new Error("windowPosition requires finite xRatio/yRatio");
  if (windowPosition.xRatio < 0 || windowPosition.xRatio > 1 || windowPosition.yRatio < 0 || windowPosition.yRatio > 1) throw new Error("windowPosition ratios must be in [0, 1]");
  if (!new Set(["tl", "tr", "br", "bl"]).has(windowPosition.anchor)) throw new Error(`Unsupported window anchor: ${windowPosition.anchor}`);

  const version = browserVersion || template.browserVersion;
  const prng = createPrng(`${preset}:${version}:${seed}`);
  const screenChoice = prng.pick(template.screens);
  const graphics = prng.pick(template.graphics);
  const locale = { ...prng.pick(template.locales) };
  const localFonts = [...(FONT_CATALOGS[template.os] ?? FONT_CATALOGS.Linux)];
  const isFirefox = template.engine === "Firefox";
  const canvas = generateCanvasNoise(template.engine, prng);
  const rects = generateClientRectsNoise(prng);
  const fontsEnabled = featureEnabled(featureMode, prng, 0.9, false);
  const speechEnabled = featureEnabled(featureMode, prng, 0.9, true);
  const pluginsEnabled = template.engine === "Chrome" && !template.mobile && featureEnabled(featureMode, prng, 0.85, false);
  const mediaDevices = mediaDevicesProfile(template, featureMode, prng, `${preset}:${seed}`);
  const taskbarAdjustment = template.mobile ? 0 : isFirefox ? 0 : 38;

  return {
    schemaVersion: 2,
    purpose: "local-browser-compatibility-testing",
    id: stableId(seed, preset, version),
    seed: String(seed),
    preset,
    generator: {
      algorithm: "roxybrowser-3.9.2-compatible",
      featureMode,
      deterministic: true,
      baseDataSource: "local-template",
      provider: null
    },
    engine: {
      family: template.engine,
      version,
      userAgent: userAgentForVersion(template, version),
      userAgentMetadata: userAgentMetadata(template, version)
    },
    os: {
      name: template.os,
      version: template.osVersion,
      architecture: template.architecture ?? "x86",
      model: template.model ?? ""
    },
    machine: {
      computerName: computerName(template.os, prng),
      macAddress: localMac(prng)
    },
    locale,
    navigator: {
      platform: template.navigatorPlatform,
      hardwareConcurrency: prng.pick(template.hardwareConcurrency),
      deviceMemory: prng.pick(template.deviceMemory),
      maxTouchPoints: template.maxTouchPoints,
      mobile: template.mobile,
      doNotTrack: featureEnabled(featureMode, prng, 0.2, true),
      pluginsEnabled,
      plugins: pluginsEnabled ? pluginList(template.engine) : []
    },
    screen: {
      width: screenChoice.width,
      height: screenChoice.height,
      availWidth: screenChoice.width,
      availHeight: Math.max(0, screenChoice.height - taskbarAdjustment),
      colorDepth: 24,
      pixelDepth: 24,
      devicePixelRatio: screenChoice.devicePixelRatio
    },
    graphics: {
      webglInfoEnabled: true,
      webglVendor: graphics.vendor,
      webglRenderer: graphics.renderer,
      webgpu: template.webgpu ?? { mode: "webgl", vendor: graphics.vendor },
      noise: { enabled: noise, interval: 1, value: generateWebglNoise(template.engine, prng) }
    },
    canvas: {
      enabled: noise,
      value: canvas.value,
      valueV2: canvas.valueV2
    },
    audioContext: {
      enabled: noise,
      value: prng.float(),
      version: "2",
      interval: 100
    },
    clientRects: {
      enabled: noise,
      noiseFactorX: rects.noiseFactorX,
      noiseFactorY: rects.noiseFactorY
    },
    fonts: {
      enabled: fontsEnabled,
      localCatalog: localFonts,
      disabled: fontsEnabled ? generateDisabledFonts(localFonts, prng) : [],
      allowed: fontsEnabled ? generateAllowFonts(prng) : []
    },
    speechSynthesis: {
      enabled: speechEnabled,
      voices: generateSpeechVoices(template.os, template.engine, prng)
    },
    mediaDevices,
    webrtc: normalizeWebrtc(webrtcMode, remoteIp),
    geolocation: geolocationProfile(template.engine, geolocationMode, latitude, longitude, accuracy),
    content: {
      blockImages: false,
      imageSizeLimit: null,
      disablePlayVideo: false,
      disablePlaySound: false,
      disablePasswordSaveTips: false
    },
    security: {
      ignoreCertificateErrors: false,
      sslCipherSuiteBlacklist: [],
      portScanProtection: true,
      portScanAllowList: []
    },
    runtime: {
      gpuAcceleration,
      sandboxPermission,
      colorScheme,
      window: {
        mode: windowMode,
        width: windowWidth,
        height: windowHeight,
        position: {
          xRatio: Number(windowPosition.xRatio),
          yRatio: Number(windowPosition.yRatio),
          anchor: windowPosition.anchor
        }
      }
    },
    battery: batteryProfile(template, featureMode, prng),
    network: networkProfile(template, featureMode, prng),
    bluetooth: bluetoothProfile(template, featureMode, prng)
  };
}

export function generateProfiles({ count = 1, seed = "local-test", ...options } = {}) {
  if (!Number.isInteger(count) || count < 1) throw new Error("count must be a positive integer");
  return Array.from({ length: count }, (_, index) => generateProfile({
    ...options,
    seed: count === 1 ? seed : deriveSeed(seed, index + 1)
  }));
}
