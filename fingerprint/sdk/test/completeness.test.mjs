import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import * as sdk from "../src/index.mjs";
import { ORIGINAL_CATALOG_COUNTS } from "../src/original-algorithms.mjs";

test("original Roxy 3.9.2 catalogs are fully extracted", () => {
  assert.deepEqual(ORIGINAL_CATALOG_COUNTS, {
    knownFonts: 486,
    windowsCoreFonts: 55,
    allowFonts: 1484,
    chromeVoices: 19,
    firefoxVoices: 37
  });
});

test("public SDK exports generation, validation and both adapters", () => {
  for (const name of [
    "generateProfile", "generateProfiles", "listPresets", "toPortableFingerprint",
    "toRoxyConfig", "toChromeConfig", "toFirefoxConfig", "toBrowserRuntime",
    "toChromeRuntime", "toFirefoxRuntime", "validateProfile", "validateBundle",
    "generateProfilesWithProvider", "applyAuthorizedBaseRecord",
    "createRoxyHttpBaseRecordProvider", "createJsonBaseRecordProvider"
  ]) assert.equal(typeof sdk[name], "function", `${name} is not exported`);
});

test("original font and voice random ranges are preserved", () => {
  for (const preset of sdk.listPresets()) {
    for (let index = 0; index < 12; index += 1) {
      const profile = sdk.generateProfile({ preset, seed: `range-${index}` });
      if (profile.fonts.enabled) {
        assert.ok(profile.fonts.allowed.length >= 100 && profile.fonts.allowed.length <= 499);
        assert.ok(profile.fonts.disabled.length <= 10);
      }
      if (profile.engine.family === "Firefox") {
        assert.ok([36, 37].includes(profile.speechSynthesis.voices.length));
      } else if (profile.os.name === "Windows") {
        assert.ok([21, 22].includes(profile.speechSynthesis.voices.length));
      } else {
        assert.ok([18, 19].includes(profile.speechSynthesis.voices.length));
      }
    }
  }
});

test("Chrome UA metadata follows the exact Roxy branch conditions", () => {
  const windowsProfile = sdk.generateProfile({ preset: "windows-11-chrome", seed: "metadata" });
  const windows = sdk.toRoxyConfig(windowsProfile);
  const mac = sdk.toRoxyConfig(sdk.generateProfile({ preset: "macos-intel-chrome", seed: "metadata" }));
  const android = sdk.toRoxyConfig(sdk.generateProfile({ preset: "android-chrome", seed: "metadata" }));
  assert.equal(windows.userAgentMetadata.architecture, undefined);
  assert.equal(windows.userAgentMetadata.model, undefined);
  assert.equal(mac.userAgentMetadata.architecture, "x86");
  assert.equal(mac.userAgentMetadata.model, undefined);
  assert.equal(android.userAgentMetadata.architecture, undefined);
  assert.equal(android.userAgentMetadata.model, "Pixel 8");
  assert.deepEqual(windowsProfile.engine.userAgentMetadata.brands, [
    { brand: "Chromium", version: "144" },
    { brand: "Google Chrome", version: "144" },
    { brand: "Not/A)Brand", version: "24" }
  ]);
  assert.equal(windowsProfile.engine.userAgentMetadata.fullVersionList[0].version, "144.0.7559.236");
  assert.equal(windowsProfile.engine.userAgentMetadata.bitness, "64");
  assert.equal(windowsProfile.engine.userAgentMetadata.wow64, false);
});

test("screen conversion preserves Roxy host-dependent behavior", () => {
  const linuxProfile = sdk.generateProfile({ preset: "linux-x64-chrome", seed: "screen" });
  const sameHost = sdk.toRoxyConfig(linuxProfile, { hostOs: "linux" });
  const foreignHost = sdk.toRoxyConfig(linuxProfile, { hostOs: "windows" });
  assert.equal(sameHost.screen.pixelDepth, 0);
  assert.equal(sameHost.screen.colorDepth, 0);
  assert.equal(sameHost.screen.devicePixelRatio, undefined);
  assert.equal(foreignHost.screen.pixelDepth, 24);
  assert.equal(foreignHost.screen.devicePixelRatio, linuxProfile.screen.devicePixelRatio);
  assert.equal(foreignHost.screen.availHeight, linuxProfile.screen.height - 38);

  const firefoxProfile = sdk.generateProfile({ preset: "linux-firefox", seed: "screen" });
  const firefox = sdk.toRoxyConfig(firefoxProfile, { hostOs: "linux" });
  assert.equal(firefox.screen.pixelDepth, 0);
  assert.equal(firefox.screen.devicePixelRatio, 1);
  assert.equal(firefox.screen.availHeight, firefoxProfile.screen.height);
});

test("random feature mode covers optional browser APIs without breaking validation", () => {
  const seen = {
    fonts: new Set(), plugins: new Set(), battery: new Set(), network: new Set(), bluetooth: new Set(), media: new Set()
  };
  for (let index = 0; index < 80; index += 1) {
    const profile = sdk.generateProfile({ preset: "windows-11-chrome", seed: `features-${index}`, featureMode: "random" });
    seen.fonts.add(profile.fonts.enabled);
    seen.plugins.add(profile.navigator.pluginsEnabled);
    seen.battery.add(profile.battery.enabled);
    seen.network.add(profile.network.enabled);
    seen.bluetooth.add(profile.bluetooth.enabled);
    seen.media.add(profile.mediaDevices.enabled);
    assert.equal(sdk.validateProfile(profile).valid, true);
  }
  for (const [name, values] of Object.entries(seen)) {
    assert.deepEqual([...values].sort(), [false, true], `${name} did not produce both states`);
  }
});

test("browser version and WebRTC options are reusable project inputs", () => {
  const chrome = sdk.generateProfile({ preset: "windows-11-chrome", seed: "version", browserVersion: "145.0.0.0" });
  assert.equal(chrome.engine.version, "145.0.0.0");
  assert.match(chrome.engine.userAgent, /Chrome\/145\.0\.0\.0/);
  const firefox = sdk.generateProfile({
    preset: "linux-firefox",
    seed: "webrtc",
    browserVersion: "146.0",
    webrtcMode: "altered",
    remoteIp: "203.0.113.8"
  });
  const config = sdk.toRoxyConfig(firefox);
  assert.equal(config.webrtc.mode, "altered");
  assert.equal(config.webrtc.loaclIP, "203.0.113.8");
  assert.equal(config.webrtc.remoteIP, "203.0.113.8");
});

test("geolocation supports every original engine branch without inventing an IP-derived position", () => {
  const chrome = sdk.generateProfile({
    preset: "windows-11-chrome",
    seed: "geo-chrome",
    geolocationMode: "allow",
    latitude: 37.7749,
    longitude: -122.4194,
    accuracy: 25
  });
  assert.deepEqual(chrome.geolocation, {
    mode: "allow", enableFakeLocationData: true,
    latitude: 37.7749, longitude: -122.4194, accuracy: 25, altitude: null
  });
  const firefox = sdk.generateProfile({ preset: "linux-firefox", seed: "geo-firefox", geolocationMode: "prompt" });
  assert.deepEqual(firefox.geolocation, {
    mode: "prompt", enableFakeLocationData: true,
    latitude: 0, longitude: 0, accuracy: 0, altitude: 0
  });
  assert.throws(
    () => sdk.generateProfile({ preset: "windows-11-chrome", seed: "geo-invalid", geolocationMode: "disable" }),
    /does not support geolocation mode/
  );
  assert.throws(
    () => sdk.generateProfile({ preset: "linux-firefox", seed: "geo-invalid", geolocationMode: "block" }),
    /does not support geolocation mode/
  );
});

test("portable adapter contains every browser-observable family", () => {
  const portable = sdk.toPortableFingerprint(sdk.generateProfile({ preset: "android-chrome", seed: "portable" }));
  assert.deepEqual(Object.keys(portable.exposedApis).sort(), [
    "audioContext", "battery", "bluetooth", "canvas", "clientRects", "fonts", "geolocation",
    "mediaDevices", "navigator", "networkInformation", "screen", "speechSynthesis", "webgl", "webgpu", "webrtc"
  ].sort());
  assert.deepEqual(portable.runtimeEnvironment, sdk.generateProfile({ preset: "android-chrome", seed: "portable" }).runtime);
});

test("Chrome runtime adapter preserves the original managed launch and theme behavior", () => {
  const profile = sdk.generateProfile({
    preset: "windows-11-chrome",
    seed: "chrome-runtime",
    colorScheme: "dark",
    gpuAcceleration: false,
    sandboxPermission: false,
    windowMode: "normal",
    windowWidth: 1280,
    windowHeight: 720,
    windowPosition: { xRatio: 1, yRatio: 1, anchor: "br" }
  });
  const runtime = sdk.toChromeRuntime(profile, {
    hostOs: "linux",
    userDataDir: "/tmp/profile",
    coreVersion: "135.0.0.0",
    virtualWorkArea: { originX: -1920, originY: 0, width: 3840, height: 1080 },
    existingPreferences: { untouched: true },
    existingLocalState: { browser: { enabled_labs_experiments: ["existing@1"] } },
    startupParam: "--load-extension=/tmp/untrusted"
  });
  for (const arg of [
    "--disable-background-mode", "--disable-popup-blocking", "--no-first-run",
    "--no-default-browser-check", "--remote-debugging-port=0", "--use-mock-keychain",
    "--user-data-dir=/tmp/profile", "--no-sandbox", "--disable-setuid-sandbox",
    "--password-store=basic", "--disable-backgrounding-occluded-windows",
    "--window-size=1280,720", "--window-position=640,360", "--disable-gpu"
  ]) assert.ok(runtime.launchArgs.includes(arg), `Chrome runtime arg missing: ${arg}`);
  assert.equal(runtime.launchArgs.some((arg) => arg.includes("untrusted")), false);
  assert.equal(runtime.preferences.untouched, true);
  assert.equal(runtime.preferences.browser.theme.color_scheme2, 2);
  assert.deepEqual(runtime.localState.background_mode, { enabled: false });
  assert.deepEqual(runtime.localState.browser.enabled_labs_experiments, ["existing@1", "enable-force-dark@1"]);
  assert.equal(runtime.originalSemantics.chromeSandboxAlwaysDisabled, true);
  assert.equal(runtime.originalSemantics.arbitraryStartupParametersAccepted, false);
});

test("Firefox runtime adapter preserves WebRender, sandbox, color and xulstore preferences", () => {
  const profile = sdk.generateProfile({
    preset: "linux-firefox",
    seed: "firefox-runtime",
    colorScheme: "light",
    gpuAcceleration: true,
    sandboxPermission: true,
    windowMode: "normal",
    windowWidth: 1000,
    windowHeight: 800,
    windowPosition: { xRatio: 0.5, yRatio: 0.5, anchor: "tl" }
  });
  const runtime = sdk.toFirefoxRuntime(profile, {
    userDataDir: "/tmp/firefox-profile",
    virtualWorkArea: { originX: 0, originY: 0, width: 1920, height: 1080 },
    existingXulstore: { untouched: true },
    startupParam: "--profile=/tmp/untrusted"
  });
  assert.deepEqual(runtime.launchArgs, [
    "-profile", "/tmp/firefox-profile", "--marionette", "--remote-debugging-port=0", "-no-remote"
  ]);
  assert.equal(runtime.userPreferences["layers.acceleration.disabled"], false);
  assert.equal(runtime.userPreferences["gfx.webrender.disabled"], false);
  assert.equal(runtime.userPreferences["security.sandbox.content.level"], 0);
  assert.equal(runtime.userPreferences["layout.css.prefers-color-scheme.content-override"], 1);
  assert.equal(runtime.userPreferences["ui.systemUsesDarkTheme"], 0);
  assert.equal(runtime.userPreferences["extensions.activeThemeID"], "firefox-compact-light@mozilla.org");
  assert.match(runtime.userJs, /user_pref\("gfx\.webrender\.disabled", false\);/);
  assert.deepEqual(runtime.xulstore.untouched, true);
  assert.deepEqual(runtime.xulstore["chrome://browser/content/browser.xhtml"]["main-window"], {
    sizemode: "normal", width: "1000", height: "800", screenX: "960", screenY: "540"
  });
  assert.equal(runtime.theme.extensionStates["firefox-compact-light@mozilla.org"].active, true);
  assert.deepEqual(runtime.theme.invalidateFilesOnChange, ["addonStartup.json.lz4"]);
  assert.equal(runtime.originalSemantics.arbitraryStartupParametersAccepted, false);
});

test("runtime defaults match the original Roxy create-window defaults", () => {
  for (const preset of sdk.listPresets()) {
    const profile = sdk.generateProfile({ preset, seed: "runtime-defaults", featureMode: "original-defaults" });
    assert.deepEqual(profile.runtime, {
      gpuAcceleration: true,
      sandboxPermission: false,
      colorScheme: "system",
      window: {
        mode: "maximized",
        width: 1280,
        height: 720,
        position: { xRatio: 0, yRatio: 0, anchor: "tl" }
      }
    });
  }
});

test("content behavior preserves Roxy's inverted forbid-field conversion", () => {
  const chromeProfile = sdk.generateProfile({ preset: "windows-11-chrome", seed: "content-defaults", featureMode: "original-defaults" });
  const firefoxProfile = sdk.generateProfile({ preset: "windows-firefox", seed: "content-defaults", featureMode: "original-defaults" });
  const chrome = sdk.toChromeConfig(chromeProfile);
  const firefox = sdk.toFirefoxConfig(firefoxProfile);
  assert.deepEqual(chromeProfile.content, {
    blockImages: false,
    imageSizeLimit: null,
    disablePlayVideo: false,
    disablePlaySound: false,
    disablePasswordSaveTips: false
  });
  assert.equal(chrome.blockImages, false);
  assert.equal(chrome.imageSizeLimit, undefined);
  assert.equal(firefox.imageSizeLimit, -1);
  assert.equal(firefox.disablePlayVideo, false);
  assert.equal(firefox.disablePlaySound, false);
  assert.equal(firefox.disablePasswordSaveTips, false);

  chromeProfile.content.blockImages = true;
  chromeProfile.content.imageSizeLimit = 256;
  firefoxProfile.content.blockImages = true;
  firefoxProfile.content.imageSizeLimit = 256;
  assert.equal(sdk.toChromeConfig(chromeProfile).imageSizeLimit, 256);
  assert.equal(sdk.toFirefoxConfig(firefoxProfile).imageSizeLimit, 256);
});

test("JSON Schema requires every generated top-level profile family", async () => {
  const schema = JSON.parse(await readFile(new URL("../schema/fingerprint-profile.schema.json", import.meta.url), "utf8"));
  const profile = sdk.generateProfile({ preset: "windows-11-chrome", seed: "schema" });
  assert.deepEqual([...schema.required].sort(), Object.keys(profile).sort());
  assert.equal(schema.properties.schemaVersion.const, 2);
});

test("Roxy adapters cover every fingerprint-related final-config family", () => {
  const chrome = sdk.toRoxyConfig(sdk.generateProfile({ preset: "windows-11-chrome", seed: "coverage" }));
  const firefox = sdk.toRoxyConfig(sdk.generateProfile({ preset: "windows-firefox", seed: "coverage" }));
  const chromeFamilies = [
    "computerName", "macAddress", "appLocale", "acceptLang", "timeZone", "chromeVersion", "userAgent",
    "audioBuffer", "canvasContext", "clientRects", "WebGL", "WebGPU", "doNotTrack", "geoLocation",
    "navigator", "userAgentMetadata", "portScan", "screen", "speechSynthesis", "webRtcMode", "ssl",
    "battery", "network", "bluetooth", "blockImages", "ignoreCertificateErrors", "disablePlayVideo",
    "disablePlaySound", "disablePasswordSaveTips"
  ];
  const firefoxFamilies = [
    "userAgent", "navigator", "timeZone", "appLocale", "acceptLang", "audioBuffer", "canvasContext",
    "clientRects", "webGL", "geoLocation", "doNotTrack", "disablePlayVideo", "portScan", "screen",
    "speechSynthesis", "webrtc", "ssl", "disablePlaySound", "disablePasswordSaveTips"
  ];
  for (const key of chromeFamilies) assert.ok(key in chrome, `Chrome field missing: ${key}`);
  for (const key of firefoxFamilies) assert.ok(key in firefox, `Firefox field missing: ${key}`);
});
