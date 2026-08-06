import { PRESETS } from "./catalog.mjs";

function add(errors, condition, message) {
  if (!condition) errors.push(message);
}

function isLocalAdminMac(value) {
  if (!/^([0-9A-F]{2}:){5}[0-9A-F]{2}$/i.test(value ?? "")) return false;
  const first = Number.parseInt(value.slice(0, 2), 16);
  return (first & 0b10) !== 0 && (first & 0b1) === 0;
}

function isMac(value) {
  return /^([0-9A-F]{2}:){5}[0-9A-F]{2}$/i.test(value ?? "");
}

function validOptionalNumber(value) {
  return value === "" || Number.isFinite(Number(value));
}

export function validateProfile(profile) {
  const errors = [];
  const preset = PRESETS[profile?.preset];
  add(errors, profile?.schemaVersion === 2, "schemaVersion must be 2");
  add(errors, profile?.generator?.algorithm === "roxybrowser-3.9.2-compatible", "unknown generator algorithm");
  add(errors, typeof profile?.generator?.deterministic === "boolean", "generator.deterministic must be boolean");
  add(errors, ["local-template", "authorized-provider"].includes(profile?.generator?.baseDataSource), "invalid generator base-data source");
  add(errors, profile?.generator?.baseDataSource !== "local-template" || profile?.generator?.provider === null, "local templates must not name a provider");
  add(errors, profile?.generator?.baseDataSource !== "authorized-provider" || typeof profile?.generator?.provider === "string", "authorized provider profiles must name their provider");
  add(errors, Boolean(preset), `unknown preset: ${profile?.preset}`);
  if (!preset) return { valid: false, errors };

  add(errors, profile.engine.family === preset.engine, "engine does not match preset");
  add(errors, profile.os.name === preset.os, "OS does not match preset");
  add(errors, profile.navigator.platform === preset.navigatorPlatform, "navigator.platform does not match preset");
  add(errors, profile.navigator.mobile === preset.mobile, "mobile flag does not match preset");
  add(errors, profile.navigator.maxTouchPoints === preset.maxTouchPoints, "touch points do not match preset");
  const versionToken = preset.ua.includes("CriOS/") ? `CriOS/${profile.engine.version}` : preset.ua.includes("FxiOS/") ? `FxiOS/${profile.engine.version}` : `${preset.engine}/${profile.engine.version}`;
  add(errors, profile.engine.userAgent.includes(versionToken), "user agent does not contain the selected browser version");
  add(errors, profile.generator.baseDataSource === "authorized-provider" ? isMac(profile.machine.macAddress) : isLocalAdminMac(profile.machine.macAddress), profile.generator.baseDataSource === "authorized-provider" ? "authorized-provider MAC has invalid syntax" : "MAC must be a locally administered unicast address");
  add(errors, Number.isInteger(profile.navigator.hardwareConcurrency) && preset.hardwareConcurrency.includes(profile.navigator.hardwareConcurrency), "invalid hardwareConcurrency");
  add(errors, preset.deviceMemory.includes(profile.navigator.deviceMemory), "invalid deviceMemory");
  add(errors, typeof profile.navigator.doNotTrack === "boolean", "doNotTrack must preserve the original boolean type");
  add(errors, profile.screen.width > 0 && profile.screen.height > 0, "invalid screen dimensions");
  add(errors, profile.screen.availWidth <= profile.screen.width && profile.screen.availHeight <= profile.screen.height, "available screen exceeds screen dimensions");
  add(errors, profile.screen.colorDepth === 24 && profile.screen.pixelDepth === 24, "normalized color depth must be 24");
  add(errors, profile.clientRects.noiseFactorX >= -1 && profile.clientRects.noiseFactorX < 1, "clientRects X noise is outside [-1, 1)");
  add(errors, profile.clientRects.noiseFactorY >= -1 && profile.clientRects.noiseFactorY < 1, "clientRects Y noise is outside [-1, 1)");
  add(errors, profile.audioContext.value >= 0 && profile.audioContext.value < 1, "audio noise is outside [0, 1)");
  add(errors, profile.audioContext.version === "2" && profile.audioContext.interval === 100, "Chrome-compatible audio metadata is invalid");
  add(errors, /^[0-9A-F]{32}$/.test(profile.canvas.value), "canvasValue must be 32 uppercase hex characters");
  add(errors, preset.locales.some((item) => item.appLocale === profile.locale.appLocale && item.acceptLanguage === profile.locale.acceptLanguage && item.timezone === profile.locale.timezone), "locale and timezone are not a catalogued pair");
  add(errors, ["altered", "real", "disable"].includes(profile.webrtc.mode), "invalid WebRTC mode");
  add(errors, profile.webrtc.mode !== "altered" || Boolean(profile.webrtc.remoteIp), "altered WebRTC mode requires an IP");
  add(errors, profile.graphics.noise.interval === 1, "WebGL noise interval must be 1");
  const geolocationModes = preset.engine === "Firefox" ? ["prompt", "allow", "disable"] : ["prompt", "allow", "block"];
  add(errors, geolocationModes.includes(profile.geolocation.mode), `invalid ${preset.engine} geolocation mode`);
  add(errors, profile.geolocation.enableFakeLocationData === true, "geolocation fake-data flag must match Roxy");
  for (const field of ["latitude", "longitude", "accuracy", "altitude"]) {
    add(errors, profile.geolocation[field] === null || Number.isFinite(profile.geolocation[field]), `invalid geolocation ${field}`);
  }

  if (profile.fonts.enabled) {
    add(errors, profile.fonts.allowed.length >= 100 && profile.fonts.allowed.length <= 499, "allowFontList must contain 100..499 entries");
    add(errors, profile.fonts.disabled.length <= 10, "disabled font list exceeds the original maximum of 10");
  } else {
    add(errors, profile.fonts.allowed.length === 0 && profile.fonts.disabled.length === 0, "disabled font mode must not emit font lists");
  }

  add(errors, Array.isArray(profile.speechSynthesis.voices) && profile.speechSynthesis.voices.length > 0, "speech voice catalog is empty");
  add(errors, Array.isArray(profile.mediaDevices.devices) && profile.mediaDevices.devices.length > 0, "media device list is empty");
  add(errors, typeof profile.security.ignoreCertificateErrors === "boolean", "ignoreCertificateErrors must be boolean");
  add(errors, Array.isArray(profile.security.sslCipherSuiteBlacklist), "SSL blacklist must be an array");
  add(errors, Array.isArray(profile.security.portScanAllowList), "port-scan allow list must be an array");
  add(errors, typeof profile.runtime?.gpuAcceleration === "boolean", "runtime.gpuAcceleration must be boolean");
  add(errors, typeof profile.runtime?.sandboxPermission === "boolean", "runtime.sandboxPermission must be boolean");
  add(errors, ["system", "light", "dark"].includes(profile.runtime?.colorScheme), "invalid runtime color scheme");
  add(errors, ["maximized", "normal"].includes(profile.runtime?.window?.mode), "invalid runtime window mode");
  add(errors, Number.isInteger(profile.runtime?.window?.width) && profile.runtime.window.width > 0, "invalid runtime window width");
  add(errors, Number.isInteger(profile.runtime?.window?.height) && profile.runtime.window.height > 0, "invalid runtime window height");
  add(errors, Number.isFinite(profile.runtime?.window?.position?.xRatio) && profile.runtime.window.position.xRatio >= 0 && profile.runtime.window.position.xRatio <= 1, "invalid runtime window X ratio");
  add(errors, Number.isFinite(profile.runtime?.window?.position?.yRatio) && profile.runtime.window.position.yRatio >= 0 && profile.runtime.window.position.yRatio <= 1, "invalid runtime window Y ratio");
  add(errors, ["tl", "tr", "br", "bl"].includes(profile.runtime?.window?.position?.anchor), "invalid runtime window anchor");
  add(errors, validOptionalNumber(profile.battery.chargingTime) && validOptionalNumber(profile.battery.dischargingTime) && validOptionalNumber(profile.battery.level), "battery values must be blank or numeric");
  add(errors, !profile.battery.enabled || (Number(profile.battery.level) >= 0 && Number(profile.battery.level) <= 1), "battery level must be in [0, 1]");
  add(errors, !profile.network.enabled || ["wifi", "ethernet", "cellular"].includes(profile.network.type), "invalid network type");
  add(errors, !profile.network.enabled || ["slow-2g", "2g", "3g", "4g"].includes(profile.network.effectiveType), "invalid effective network type");

  if (preset.engine === "Firefox") {
    add(errors, profile.engine.userAgentMetadata === null, "Firefox must not emit Chrome userAgentMetadata");
    add(errors, typeof profile.canvas.valueV2 === "number" && profile.canvas.valueV2 >= 0 && profile.canvas.valueV2 < 1, "Firefox canvas noise must be in [0, 1)");
    add(errors, typeof profile.graphics.noise.value === "number" && profile.graphics.noise.value >= 0 && profile.graphics.noise.value < 1, "Firefox WebGL noise must be in [0, 1)");
    add(errors, [36, 37].includes(profile.speechSynthesis.voices.length), "Firefox voice count must match the original 36/37 behavior");
  } else {
    add(errors, profile.engine.userAgentMetadata?.platform === preset.uaPlatform, "Chrome UA metadata platform does not match preset");
    add(errors, profile.engine.userAgentMetadata?.mobile === preset.mobile, "Chrome UA metadata mobile flag does not match preset");
    add(errors, profile.engine.userAgentMetadata?.brands?.length === 3, "Chrome UA metadata must contain the original three brands");
    add(errors, profile.engine.userAgentMetadata?.fullVersionList?.length === 3, "Chrome UA metadata must contain a three-item fullVersionList");
    add(errors, profile.engine.userAgentMetadata?.bitness === "64" && profile.engine.userAgentMetadata?.wow64 === false, "Chrome UA metadata bitness/wow64 is invalid");
    add(errors, Number.isInteger(profile.canvas.valueV2) && profile.canvas.valueV2 >= 1_000 && profile.canvas.valueV2 <= 99_999, "Chrome canvasValueV2 must be an integer in 1000..99999");
    add(errors, typeof profile.graphics.noise.value === "string" && /^[0-9]{2}[A-Z0-9]{14}$/.test(profile.graphics.noise.value), "Chrome WebGL noise must match the original 16-character format");
    const expectedVoiceCounts = profile.os.name === "Windows" ? [21, 22] : [18, 19];
    add(errors, expectedVoiceCounts.includes(profile.speechSynthesis.voices.length), `Chrome voice count must match the original ${expectedVoiceCounts.join("/")} behavior`);
  }

  return { valid: errors.length === 0, errors };
}

export function validateBundle(value) {
  const profile = value?.profile ?? value;
  const result = validateProfile(profile);
  if (value?.roxyConfig) {
    const roxy = value.roxyConfig;
    if (profile.engine.family === "Firefox") {
      add(result.errors, Boolean(roxy.webGL) && !roxy.WebGL, "Firefox Roxy config must use webGL");
      add(result.errors, Boolean(roxy.webrtc), "Firefox Roxy config is missing webrtc");
      add(result.errors, roxy.userAgentMetadata === undefined, "Firefox Roxy config must not contain userAgentMetadata");
    } else {
      add(result.errors, Boolean(roxy.WebGL) && !roxy.webGL, "Chrome Roxy config must use WebGL");
      add(result.errors, typeof roxy.webRtcMode === "string", "Chrome Roxy config is missing webRtcMode");
      add(result.errors, Boolean(roxy.WebGPU), "Chrome Roxy config is missing WebGPU");
      add(result.errors, Boolean(roxy.battery) && Boolean(roxy.network) && Boolean(roxy.bluetooth), "Chrome Roxy config is missing extended device APIs");
    }
  }
  result.valid = result.errors.length === 0;
  return result;
}
