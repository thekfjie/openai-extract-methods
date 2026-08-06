const COLOR_SCHEME_CODES = Object.freeze({ system: 0, light: 1, dark: 2 });
const FIREFOX_THEME_IDS = Object.freeze({
  system: "default-theme@mozilla.org",
  light: "firefox-compact-light@mozilla.org",
  dark: "firefox-compact-dark@mozilla.org"
});
const FIREFOX_MANAGED_THEME_IDS = Object.freeze(Object.values(FIREFOX_THEME_IDS));

function clone(value) {
  return structuredClone(value ?? {});
}

function normalizeHostOs(value = process.platform) {
  return { win32: "windows", darwin: "macos", linux: "linux" }[value] ?? String(value).toLowerCase();
}

function majorVersion(value) {
  const major = Number.parseInt(String(value).split(".")[0], 10);
  return Number.isFinite(major) ? major : 0;
}

function validateRuntime(runtime) {
  if (!runtime || typeof runtime !== "object") throw new Error("profile.runtime is required");
  if (!["system", "light", "dark"].includes(runtime.colorScheme)) throw new Error(`Unsupported color scheme: ${runtime.colorScheme}`);
  if (typeof runtime.gpuAcceleration !== "boolean") throw new Error("runtime.gpuAcceleration must be boolean");
  if (typeof runtime.sandboxPermission !== "boolean") throw new Error("runtime.sandboxPermission must be boolean");
  if (!["maximized", "normal"].includes(runtime.window?.mode)) throw new Error(`Unsupported window mode: ${runtime.window?.mode}`);
  if (!Number.isInteger(runtime.window.width) || runtime.window.width < 1) throw new Error("runtime.window.width must be a positive integer");
  if (!Number.isInteger(runtime.window.height) || runtime.window.height < 1) throw new Error("runtime.window.height must be a positive integer");
  const position = runtime.window.position;
  if (!position || !Number.isFinite(position.xRatio) || !Number.isFinite(position.yRatio)) throw new Error("runtime.window.position ratios must be finite numbers");
  if (position.xRatio < 0 || position.xRatio > 1 || position.yRatio < 0 || position.yRatio > 1) throw new Error("runtime.window.position ratios must be in [0, 1]");
  if (!new Set(["tl", "tr", "br", "bl"]).has(position.anchor)) throw new Error(`Unsupported window anchor: ${position.anchor}`);
}

function windowPosition(runtime, virtualWorkArea) {
  const area = {
    originX: Number(virtualWorkArea?.originX ?? 0),
    originY: Number(virtualWorkArea?.originY ?? 0),
    width: Number(virtualWorkArea?.width ?? runtime.window.width),
    height: Number(virtualWorkArea?.height ?? runtime.window.height)
  };
  const { xRatio, yRatio, anchor } = runtime.window.position;
  let x = area.originX + Math.floor(area.width * xRatio);
  let y = area.originY + Math.floor(area.height * yRatio);
  if (anchor === "tr") x -= runtime.window.width;
  else if (anchor === "br") {
    x -= runtime.window.width;
    y -= runtime.window.height;
  } else if (anchor === "bl") y -= runtime.window.height;
  return { x, y };
}

function chromePreferences(runtime, existingPreferences) {
  const preferences = clone(existingPreferences);
  preferences.browser ??= {};
  preferences.browser.theme ??= {};
  preferences.browser.theme.color_scheme2 = COLOR_SCHEME_CODES[runtime.colorScheme];
  preferences.browser.theme.color_scheme = preferences.browser.theme.color_scheme2;
  return preferences;
}

function chromeLocalState(runtime, coreVersion, existingLocalState) {
  const localState = clone(existingLocalState);
  localState.background_mode = { enabled: false };
  if (majorVersion(coreVersion) < 136) {
    localState.browser ??= {};
    const experiments = new Set(localState.browser.enabled_labs_experiments ?? []);
    if (runtime.colorScheme === "dark") experiments.add("enable-force-dark@1");
    else experiments.delete("enable-force-dark@1");
    localState.browser.enabled_labs_experiments = [...experiments];
  }
  return localState;
}

function firefoxPreferences(runtime) {
  const preferences = {
    "browser.startup.homepage": "about:home",
    "browser.newtabpage.enabled": true,
    "layers.acceleration.disabled": !runtime.gpuAcceleration,
    "gfx.webrender.disabled": !runtime.gpuAcceleration,
    "security.sandbox.content.level": runtime.sandboxPermission ? 0 : -1,
    "layout.css.prefers-color-scheme.content-override": runtime.colorScheme === "system" ? 2 : runtime.colorScheme === "dark" ? 0 : 1,
    "extensions.activeThemeID": FIREFOX_THEME_IDS[runtime.colorScheme]
  };
  if (runtime.colorScheme !== "system") preferences["ui.systemUsesDarkTheme"] = runtime.colorScheme === "dark" ? 1 : 0;
  return preferences;
}

function firefoxUserJs(preferences) {
  const lines = ["// Roxy Browser managed preferences"];
  for (const [name, value] of Object.entries(preferences)) lines.push(`user_pref(${JSON.stringify(name)}, ${JSON.stringify(value)});`);
  return `${lines.join("\n")}\n`;
}

function firefoxXulstore(runtime, virtualWorkArea, existingXulstore) {
  const xulstore = clone(existingXulstore);
  const browserKey = "chrome://browser/content/browser.xhtml";
  xulstore[browserKey] ??= {};
  const mainWindow = { ...(xulstore[browserKey]["main-window"] ?? {}) };
  if (runtime.window.mode === "maximized") {
    mainWindow.sizemode = "maximized";
  } else {
    mainWindow.sizemode = "normal";
    mainWindow.width = String(runtime.window.width);
    mainWindow.height = String(runtime.window.height);
    const position = windowPosition(runtime, virtualWorkArea);
    mainWindow.screenX = String(position.x);
    mainWindow.screenY = String(position.y);
  }
  xulstore[browserKey]["main-window"] = mainWindow;
  return xulstore;
}

export function toBrowserRuntime(profile, options = {}) {
  return profile.engine.family === "Firefox" ? toFirefoxRuntime(profile, options) : toChromeRuntime(profile, options);
}

export function toChromeRuntime(profile, {
  hostOs = process.platform,
  userDataDir = "<USER_DATA_DIR>",
  virtualWorkArea,
  coreVersion = profile.engine.version,
  existingPreferences,
  existingLocalState
} = {}) {
  validateRuntime(profile.runtime);
  const runtime = profile.runtime;
  const args = [
    "--disable-background-mode",
    "--disable-popup-blocking",
    "--no-first-run",
    "--no-default-browser-check",
    "--remote-debugging-port=0"
  ];
  if (normalizeHostOs(hostOs) !== "macos") args.push("--use-mock-keychain");
  args.push(
    `--user-data-dir=${userDataDir}`,
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--password-store=basic",
    "--disable-backgrounding-occluded-windows"
  );
  if (normalizeHostOs(hostOs) === "macos") args.push("--disable-features=MacAppCodeSignClone");
  if (runtime.window.mode === "maximized") args.push("--start-maximized");
  else {
    const position = windowPosition(runtime, virtualWorkArea);
    args.push(`--window-size=${runtime.window.width},${runtime.window.height}`);
    args.push(`--window-position=${position.x},${position.y}`);
  }
  if (profile.navigator.mobile) args.push("--auto-open-devtools-for-tabs");
  if (!runtime.gpuAcceleration) args.push("--disable-gpu");

  return {
    engine: "Chrome",
    launchArgs: args,
    preferences: chromePreferences(runtime, existingPreferences),
    localState: chromeLocalState(runtime, coreVersion, existingLocalState),
    colorSchemeCache: runtime.colorScheme,
    originalSemantics: {
      chromeSandboxAlwaysDisabled: true,
      requestedSandboxPermission: runtime.sandboxPermission,
      arbitraryStartupParametersAccepted: false
    }
  };
}

export function toFirefoxRuntime(profile, {
  userDataDir = "<USER_DATA_DIR>",
  virtualWorkArea,
  existingXulstore
} = {}) {
  validateRuntime(profile.runtime);
  const runtime = profile.runtime;
  const args = ["-profile", userDataDir, "--marionette", "--remote-debugging-port=0"];
  if (runtime.window.mode === "maximized") args.push("-maximized");
  args.push("-no-remote");
  const preferences = firefoxPreferences(runtime);
  const activeThemeId = FIREFOX_THEME_IDS[runtime.colorScheme];
  return {
    engine: "Firefox",
    launchArgs: args,
    userPreferences: preferences,
    userJs: firefoxUserJs(preferences),
    xulstore: firefoxXulstore(runtime, virtualWorkArea, existingXulstore),
    theme: {
      activeThemeId,
      managedThemeIds: [...FIREFOX_MANAGED_THEME_IDS],
      extensionStates: Object.fromEntries(FIREFOX_MANAGED_THEME_IDS.map((id) => [id, {
        active: id === activeThemeId,
        userDisabled: id !== activeThemeId
      }])),
      invalidateFilesOnChange: ["addonStartup.json.lz4"],
      removePrefsJsKeysOnSystem: runtime.colorScheme === "system" ? ["ui.systemUsesDarkTheme"] : []
    },
    colorSchemeCache: runtime.colorScheme,
    originalSemantics: {
      sandboxContentLevel: runtime.sandboxPermission ? 0 : -1,
      arbitraryStartupParametersAccepted: false
    }
  };
}

export const RUNTIME_CONSTANTS = Object.freeze({
  colorSchemeCodes: COLOR_SCHEME_CODES,
  firefoxThemeIds: FIREFOX_THEME_IDS,
  firefoxManagedThemeIds: FIREFOX_MANAGED_THEME_IDS
});
