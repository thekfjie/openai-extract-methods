function fontList(values) {
  return values.join(";");
}

function normalizeHostOs(value = process.platform) {
  return { win32: "windows", darwin: "macos", linux: "linux" }[value] ?? String(value).toLowerCase();
}

function profileOsKey(profile) {
  return profile.os.name.toLowerCase() === "macos" ? "macos" : profile.os.name.toLowerCase();
}

function chromeScreen(profile, hostOs) {
  const sameAsHost = normalizeHostOs(hostOs) === profileOsKey(profile);
  const mobile = profile.navigator.mobile;
  return {
    pixelDepth: sameAsHost ? 0 : 24,
    colorDepth: sameAsHost ? 0 : 24,
    width: profile.screen.width,
    height: profile.screen.height,
    availWidth: profile.screen.width,
    availHeight: Math.max(0, profile.screen.height - (mobile ? 0 : 38)),
    ...(!sameAsHost ? { devicePixelRatio: profile.screen.devicePixelRatio } : {})
  };
}

function firefoxScreen(profile, hostOs) {
  const sameAsHost = normalizeHostOs(hostOs) === profileOsKey(profile);
  return {
    pixelDepth: sameAsHost ? 0 : 24,
    colorDepth: sameAsHost ? 0 : 24,
    width: profile.screen.width,
    height: profile.screen.height,
    availWidth: profile.screen.width,
    availHeight: profile.screen.height,
    devicePixelRatio: sameAsHost ? 1 : profile.screen.devicePixelRatio
  };
}

function portScan(profile, appPort) {
  const values = [appPort, ...profile.security.portScanAllowList].filter((value) => value !== undefined && value !== null && String(value) !== "");
  return {
    enablePortScanWhiteList: profile.security.portScanProtection,
    portScanWhiteList: values.join(";")
  };
}

export function toRoxyConfig(profile, options = {}) {
  return profile.engine.family === "Firefox" ? toFirefoxConfig(profile, options) : toChromeConfig(profile, options);
}

export function toChromeConfig(profile, { hostOs = process.platform, appPort = 0 } = {}) {
  const metadata = {
    platform: profile.engine.userAgentMetadata.platform,
    mobile: profile.engine.userAgentMetadata.mobile,
    platformVersion: profile.engine.userAgentMetadata.platformVersion,
    ...(profile.os.name === "macOS" && profile.graphics.webglInfoEnabled ? { architecture: profile.engine.userAgentMetadata.architecture } : {}),
    ...(profile.os.name === "Android" ? { model: profile.engine.userAgentMetadata.model } : {})
  };

  return {
    computerName: profile.machine.computerName,
    macAddress: profile.machine.macAddress,
    searchEngine: { name: "Google" },
    appLocale: profile.locale.appLocale,
    acceptLang: profile.locale.acceptLanguage,
    timeZone: profile.locale.timezone,
    chromeVersion: profile.engine.version,
    chromeType: "Google Chrome",
    userAgent: profile.engine.userAgent,
    audioBuffer: {
      version: profile.audioContext.version,
      enableAudioBufferNoise: profile.audioContext.enabled,
      audioBufferNoiseValue: Number(profile.audioContext.value),
      audioBufferNoiseInterval: profile.audioContext.interval
    },
    canvasContext: {
      enableCanvasContextNoise: profile.canvas.enabled,
      canvasContextNoiseValue: profile.canvas.value,
      canvasContextNoiseValueV2: Number(profile.canvas.valueV2)
    },
    clientRects: {
      enable: profile.clientRects.enabled,
      clientRectsNoiseFactorX: profile.clientRects.noiseFactorX,
      clientRectsNoiseFactorY: profile.clientRects.noiseFactorY
    },
    WebGL: {
      webglRenderer: profile.graphics.webglInfoEnabled ? profile.graphics.webglRenderer : "",
      webglVendor: profile.graphics.webglInfoEnabled ? profile.graphics.webglVendor : "",
      enableWebGLRendererNoise: profile.graphics.noise.enabled,
      webGLRendererNoiseInterval: profile.graphics.noise.interval,
      webGLRendererNoiseValue: profile.graphics.noise.value
    },
    WebGPU: {
      mode: profile.graphics.webgpu.mode,
      vendor: profile.graphics.webgpu.vendor
    },
    ...(profile.fonts.enabled ? {
      disableFontListV2: fontList(profile.fonts.disabled),
      allowFontList: fontList(profile.fonts.allowed)
    } : {}),
    doNotTrack: profile.navigator.doNotTrack,
    geoLocation: {
      mode: profile.geolocation.mode,
      enableFakeLocationData: profile.geolocation.enableFakeLocationData,
      locationLatitude: profile.geolocation.latitude,
      locationLongitude: profile.geolocation.longitude,
      locationAccuracy: profile.geolocation.accuracy,
      locationAltitude: profile.geolocation.altitude
    },
    blockImages: profile.content.blockImages,
    ignoreCertificateErrors: profile.security.ignoreCertificateErrors,
    disablePlayVideo: profile.content.disablePlayVideo,
    disablePlaySound: profile.content.disablePlaySound,
    disablePasswordSaveTips: profile.content.disablePasswordSaveTips,
    navigator: {
      platform: profile.navigator.platform,
      hardwareConcurrency: Number(profile.navigator.hardwareConcurrency),
      maxTouchPoints: profile.navigator.maxTouchPoints,
      deviceMemory: Number(profile.navigator.deviceMemory),
      plugins: profile.navigator.pluginsEnabled ? profile.navigator.plugins : []
    },
    userAgentMetadata: metadata,
    portScan: portScan(profile, appPort),
    screen: chromeScreen(profile, hostOs),
    speechSynthesis: {
      enable: profile.speechSynthesis.enabled,
      voices: profile.speechSynthesis.voices
    },
    webRtcMode: { altered: "altered", real: "real", disable: "disableOk" }[profile.webrtc.mode],
    ...(profile.webrtc.mode === "altered" && profile.webrtc.remoteIp ? { webRtcRemoteIp: profile.webrtc.remoteIp } : {}),
    ssl: { cipherSuiteBlacklist: profile.security.sslCipherSuiteBlacklist.join(",") },
    ...(profile.content.blockImages ? { imageSizeLimit: Number(profile.content.imageSizeLimit ?? 0) } : {}),
    battery: {
      enable: profile.battery.enabled,
      charging: profile.battery.charging,
      chargingTime: profile.battery.chargingTime,
      dischargingTime: profile.battery.dischargingTime,
      level: profile.battery.level
    },
    network: {
      enable: profile.network.enabled,
      nettype: profile.network.type === "cellular" ? String(profile.network.effectiveType).replace(/slow-2G/i, "2G") : profile.network.type,
      effectiveType: profile.network.effectiveType,
      downlink: profile.network.downlink,
      downlinkMax: profile.network.downlinkMax,
      rtt: profile.network.rtt,
      saveData: profile.network.saveData
    },
    bluetooth: {
      enable: profile.bluetooth.enabled,
      bluetoothAdapter: profile.bluetooth.adapterAvailable
    }
  };
}

export function toFirefoxConfig(profile, { hostOs = process.platform, appPort = 0, windowNum = 1 } = {}) {
  return {
    userAgent: profile.engine.userAgent,
    navigator: {
      platform: profile.navigator.platform,
      hardwareConcurrency: Number(profile.navigator.hardwareConcurrency),
      maxTouchPoints: profile.navigator.maxTouchPoints,
      deviceMemory: Number(profile.navigator.deviceMemory),
      mobile: profile.navigator.mobile
    },
    windowNum,
    timeZone: profile.locale.timezone,
    appLocale: profile.locale.appLocale,
    acceptLang: profile.locale.acceptLanguage,
    audioBuffer: {
      enableAudioBufferNoise: profile.audioContext.enabled,
      audioBufferNoiseValue: Number(profile.audioContext.value)
    },
    canvasContext: {
      enableCanvasContextNoise: profile.canvas.enabled,
      canvasContextNoiseValue: Number(profile.canvas.valueV2)
    },
    clientRects: {
      enable: profile.clientRects.enabled,
      clientRectsNoiseFactorX: profile.clientRects.noiseFactorX,
      clientRectsNoiseFactorY: profile.clientRects.noiseFactorY
    },
    webGL: {
      enableWebGLRendererNoise: profile.graphics.noise.enabled,
      webglRenderer: profile.graphics.webglInfoEnabled ? profile.graphics.webglRenderer : "",
      webglVendor: profile.graphics.webglInfoEnabled ? profile.graphics.webglVendor : "",
      webGLRendererNoiseValue: Number(profile.graphics.noise.value)
    },
    geoLocation: {
      mode: profile.geolocation.mode,
      enableFakeLocationData: profile.geolocation.enableFakeLocationData,
      locationLatitude: Number(profile.geolocation.latitude ?? 0),
      locationLongitude: Number(profile.geolocation.longitude ?? 0),
      locationAccuracy: Number(profile.geolocation.accuracy ?? 0),
      locationAltitude: Number(profile.geolocation.altitude ?? 0)
    },
    doNotTrack: profile.navigator.doNotTrack,
    imageSizeLimit: profile.content.blockImages ? Number(profile.content.imageSizeLimit ?? 0) : -1,
    disablePlayVideo: profile.content.disablePlayVideo,
    portScan: portScan(profile, appPort),
    screen: firefoxScreen(profile, hostOs),
    speechSynthesis: {
      enable: profile.speechSynthesis.enabled,
      voices: profile.speechSynthesis.voices
    },
    webrtc: {
      mode: profile.webrtc.mode,
      ...(profile.webrtc.mode === "altered" && profile.webrtc.remoteIp ? {
        loaclIP: profile.webrtc.remoteIp,
        remoteIP: profile.webrtc.remoteIp
      } : {})
    },
    ssl: { cipherSuiteBlacklist: profile.security.sslCipherSuiteBlacklist.join(",") },
    disablePlaySound: profile.content.disablePlaySound,
    disablePasswordSaveTips: profile.content.disablePasswordSaveTips,
    ...(profile.fonts.enabled ? { allowFontList: fontList(profile.fonts.allowed) } : {})
  };
}
