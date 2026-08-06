const chromeVersion = "144.0.7559.236";
const firefoxVersion = "146.0";

const locales = [
  { appLocale: "id-ID", acceptLanguage: "id-ID,id;q=0.9,en;q=0.7", timezone: "Asia/Jakarta" },
  { appLocale: "de-DE", acceptLanguage: "de-DE,de;q=0.9,en;q=0.7", timezone: "Europe/Berlin" },
  { appLocale: "es-ES", acceptLanguage: "es-ES,es;q=0.9,en;q=0.7", timezone: "Europe/Madrid" },
  { appLocale: "es-US", acceptLanguage: "es-US,es;q=0.9,en;q=0.7", timezone: "America/Chicago" },
  { appLocale: "fr-FR", acceptLanguage: "fr-FR,fr;q=0.9,en;q=0.7", timezone: "Europe/Paris" },
  { appLocale: "it-IT", acceptLanguage: "it-IT,it;q=0.9,en;q=0.7", timezone: "Europe/Rome" },
  { appLocale: "nl-NL", acceptLanguage: "nl-NL,nl;q=0.9,en;q=0.7", timezone: "Europe/Amsterdam" },
  { appLocale: "pl-PL", acceptLanguage: "pl-PL,pl;q=0.9,en;q=0.7", timezone: "Europe/Warsaw" },
  { appLocale: "pt-BR", acceptLanguage: "pt-BR,pt;q=0.9,en;q=0.7", timezone: "America/Sao_Paulo" },
  { appLocale: "en-US", acceptLanguage: "en-US,en;q=0.9", timezone: "America/New_York" },
  { appLocale: "en-US", acceptLanguage: "en-US,en;q=0.9", timezone: "America/Los_Angeles" },
  { appLocale: "en-GB", acceptLanguage: "en-GB,en;q=0.9", timezone: "Europe/London" },
  { appLocale: "ru-RU", acceptLanguage: "ru-RU,ru;q=0.9,en;q=0.7", timezone: "Europe/Moscow" },
  { appLocale: "hi-IN", acceptLanguage: "hi-IN,hi;q=0.9,en;q=0.7", timezone: "Asia/Kolkata" },
  { appLocale: "zh-TW", acceptLanguage: "zh-TW,zh;q=0.9,en;q=0.7", timezone: "Asia/Taipei" },
  { appLocale: "ja-JP", acceptLanguage: "ja-JP,ja;q=0.9,en;q=0.7", timezone: "Asia/Tokyo" },
  { appLocale: "ko-KR", acceptLanguage: "ko-KR,ko;q=0.9,en;q=0.7", timezone: "Asia/Seoul" },
  { appLocale: "zh-CN", acceptLanguage: "zh-CN,zh;q=0.9,en;q=0.7", timezone: "Asia/Shanghai" },
  { appLocale: "zh-HK", acceptLanguage: "zh-HK,zh;q=0.9,en;q=0.7", timezone: "Asia/Hong_Kong" }
];

export const FONT_CATALOGS = {
  Windows: [
    "Arial", "Calibri", "Cambria", "Cambria Math", "Candara", "Comic Sans MS",
    "Consolas", "Constantia", "Corbel", "Courier New", "Ebrima", "Georgia",
    "Impact", "Lucida Console", "Malgun Gothic", "Microsoft JhengHei",
    "Microsoft Sans Serif", "Microsoft YaHei", "Palatino Linotype", "Segoe UI",
    "Segoe UI Emoji", "Segoe UI Symbol", "Tahoma", "Times New Roman", "Trebuchet MS",
    "Verdana", "Yu Gothic"
  ],
  macOS: [
    "Arial", "Arial Hebrew", "Apple Color Emoji", "Apple SD Gothic Neo", "Avenir",
    "Courier New", "Geneva", "Georgia", "Helvetica", "Helvetica Neue", "Hiragino Kaku Gothic ProN",
    "Hiragino Mincho ProN", "Hoefler Text", "Lucida Grande", "Menlo", "Monaco",
    "New York", "Noteworthy", "Palatino", "SF Pro Display", "SF Pro Text", "Times New Roman",
    "Trebuchet MS", "Verdana"
  ],
  Linux: [
    "Arial", "Cantarell", "DejaVu Sans", "DejaVu Sans Mono", "DejaVu Serif", "Liberation Mono",
    "Liberation Sans", "Liberation Serif", "Noto Color Emoji", "Noto Sans", "Noto Sans CJK JP",
    "Noto Sans CJK SC", "Noto Serif", "Roboto", "Ubuntu", "Ubuntu Mono"
  ],
  Android: [
    "Droid Sans", "Droid Sans Mono", "Noto Color Emoji", "Noto Sans", "Noto Sans CJK JP",
    "Noto Sans CJK SC", "Noto Serif", "Roboto", "Roboto Condensed", "sans-serif"
  ],
  IOS: [
    "Arial", "Apple Color Emoji", "Courier New", "Georgia", "Helvetica", "Helvetica Neue",
    "Hiragino Kaku Gothic ProN", "Hiragino Mincho ProN", "Menlo", "New York", "SF Pro Display",
    "SF Pro Text", "Times New Roman", "Trebuchet MS", "Verdana"
  ]
};

const windowsScreens = [
  { width: 1920, height: 1080, devicePixelRatio: 1 },
  { width: 1920, height: 1080, devicePixelRatio: 1.25 },
  { width: 1536, height: 864, devicePixelRatio: 1.25 },
  { width: 2560, height: 1440, devicePixelRatio: 1 }
];

const desktopChrome = {
  engine: "Chrome",
  browserVersion: chromeVersion,
  mobile: false,
  maxTouchPoints: 0,
  deviceMemory: [8, 16],
  hardwareConcurrency: [4, 8, 12, 16],
  locales,
  webgpu: { mode: "real", vendor: "" }
};

const desktopFirefox = {
  engine: "Firefox",
  browserVersion: firefoxVersion,
  mobile: false,
  maxTouchPoints: 0,
  deviceMemory: [8, 16],
  hardwareConcurrency: [4, 8, 12, 16],
  locales
};

export const PRESETS = {
  "windows-11-chrome": {
    ...desktopChrome,
    os: "Windows", osVersion: "11", navigatorPlatform: "Win32", uaPlatform: "Windows",
    platformVersion: "15.0.0", architecture: "x86", screens: windowsScreens,
    ua: `Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/${chromeVersion} Safari/537.36`,
    graphics: [
      { vendor: "Google Inc. (Intel)", renderer: "ANGLE (Intel, Intel(R) UHD Graphics 770 (0x00004680) Direct3D11 vs_5_0 ps_5_0, D3D11)" },
      { vendor: "Google Inc. (NVIDIA)", renderer: "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)" }
    ]
  },
  "windows-10-chrome": {
    ...desktopChrome,
    os: "Windows", osVersion: "10", navigatorPlatform: "Win32", uaPlatform: "Windows",
    platformVersion: "10.0.0", architecture: "x86", screens: windowsScreens,
    ua: `Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/${chromeVersion} Safari/537.36`,
    graphics: [
      { vendor: "Google Inc. (Intel)", renderer: "ANGLE (Intel, Intel(R) UHD Graphics 620 (0x00005917) Direct3D11 vs_5_0 ps_5_0, D3D11)" },
      { vendor: "Google Inc. (NVIDIA)", renderer: "ANGLE (NVIDIA, NVIDIA GeForce GTX 1650 Direct3D11 vs_5_0 ps_5_0, D3D11)" }
    ]
  },
  "macos-intel-chrome": {
    ...desktopChrome,
    os: "macOS", osVersion: "13.6", navigatorPlatform: "MacIntel", uaPlatform: "macOS",
    platformVersion: "13.6.0", architecture: "x86", screens: [
      { width: 1440, height: 900, devicePixelRatio: 2 },
      { width: 1680, height: 1050, devicePixelRatio: 2 },
      { width: 2560, height: 1440, devicePixelRatio: 2 }
    ],
    hardwareConcurrency: [4, 8], deviceMemory: [8, 16],
    ua: `Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/${chromeVersion} Safari/537.36`,
    graphics: [
      { vendor: "Google Inc. (Intel Inc.)", renderer: "ANGLE (Intel Inc., Intel(R) Iris(TM) Plus Graphics 655, OpenGL 4.1)" },
      { vendor: "Google Inc. (AMD)", renderer: "ANGLE (AMD, AMD Radeon Pro 560 OpenGL Engine, OpenGL 4.1)" }
    ]
  },
  "macos-apple-chrome": {
    ...desktopChrome,
    os: "macOS", osVersion: "14.6", navigatorPlatform: "MacIntel", uaPlatform: "macOS",
    platformVersion: "14.6.0", architecture: "arm", screens: [
      { width: 1512, height: 982, devicePixelRatio: 2 },
      { width: 1728, height: 1117, devicePixelRatio: 2 },
      { width: 2560, height: 1440, devicePixelRatio: 2 }
    ],
    hardwareConcurrency: [8, 10, 12], deviceMemory: [8, 16, 32],
    ua: `Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/${chromeVersion} Safari/537.36`,
    graphics: [
      { vendor: "Google Inc. (Apple)", renderer: "ANGLE (Apple, ANGLE Metal Renderer: Apple M1, Unspecified Version)" },
      { vendor: "Google Inc. (Apple)", renderer: "ANGLE (Apple, ANGLE Metal Renderer: Apple M2, Unspecified Version)" }
    ],
    webgpu: { mode: "real", vendor: "Apple" }
  },
  "linux-x64-chrome": {
    ...desktopChrome,
    os: "Linux", osVersion: "6", navigatorPlatform: "Linux x86_64", uaPlatform: "Linux",
    platformVersion: "6.8.0", architecture: "x86", screens: windowsScreens,
    ua: `Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/${chromeVersion} Safari/537.36`,
    graphics: [
      { vendor: "Google Inc. (Intel)", renderer: "ANGLE (Intel, Mesa Intel(R) UHD Graphics 630 (CFL GT2), OpenGL 4.6)" },
      { vendor: "Google Inc. (AMD)", renderer: "ANGLE (AMD, AMD Radeon RX 6600 (radeonsi, navi23, LLVM 17.0.6), OpenGL 4.6)" }
    ]
  },
  "android-chrome": {
    engine: "Chrome", browserVersion: chromeVersion, os: "Android", osVersion: "14",
    navigatorPlatform: "Linux armv81", uaPlatform: "Android", platformVersion: "14.0.0",
    architecture: "arm", model: "Pixel 8", mobile: true, maxTouchPoints: 5,
    hardwareConcurrency: [8], deviceMemory: [8], locales, webgpu: { mode: "real", vendor: "Qualcomm" },
    screens: [
      { width: 412, height: 915, devicePixelRatio: 2.625 },
      { width: 393, height: 873, devicePixelRatio: 2.75 }
    ],
    ua: `Mozilla/5.0 (Linux; Android 14; Pixel 8 Build/AP2A.240905.003) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/${chromeVersion} Mobile Safari/537.36`,
    graphics: [{ vendor: "Google Inc. (Qualcomm)", renderer: "ANGLE (Qualcomm, Adreno (TM) 740, OpenGL ES 3.2)" }]
  },
  "ios-chrome": {
    engine: "Chrome", browserVersion: chromeVersion, os: "IOS", osVersion: "17.6",
    navigatorPlatform: "iPhone", uaPlatform: "iOS", platformVersion: "17.6.0",
    architecture: "arm", model: "iPhone", mobile: true, maxTouchPoints: 5,
    hardwareConcurrency: [6], deviceMemory: [8], locales, webgpu: { mode: "real", vendor: "Apple" },
    screens: [
      { width: 393, height: 852, devicePixelRatio: 3 },
      { width: 430, height: 932, devicePixelRatio: 3 }
    ],
    ua: `Mozilla/5.0 (iPhone; CPU iPhone OS 17_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/${chromeVersion} Mobile/15E148 Safari/604.1`,
    graphics: [{ vendor: "Apple Inc.", renderer: "Apple GPU" }]
  },
  "windows-firefox": {
    ...desktopFirefox,
    os: "Windows", osVersion: "11", navigatorPlatform: "Win32", screens: windowsScreens,
    ua: `Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:${firefoxVersion}) Gecko/20100101 Firefox/${firefoxVersion}`,
    graphics: [
      { vendor: "Google Inc. (Intel)", renderer: "ANGLE (Intel, Intel(R) UHD Graphics 770 Direct3D11 vs_5_0 ps_5_0)" },
      { vendor: "Google Inc. (NVIDIA)", renderer: "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0)" }
    ]
  },
  "macos-firefox": {
    ...desktopFirefox,
    os: "macOS", osVersion: "13.6", navigatorPlatform: "MacIntel",
    hardwareConcurrency: [4, 8], deviceMemory: [8, 16],
    screens: [{ width: 1440, height: 900, devicePixelRatio: 2 }, { width: 1680, height: 1050, devicePixelRatio: 2 }],
    ua: `Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:${firefoxVersion}) Gecko/20100101 Firefox/${firefoxVersion}`,
    graphics: [{ vendor: "Intel Inc.", renderer: "Intel Iris Plus Graphics 655" }]
  },
  "macos-apple-firefox": {
    ...desktopFirefox,
    os: "macOS", osVersion: "14.6", navigatorPlatform: "MacIntel", architecture: "arm",
    hardwareConcurrency: [8, 10, 12], deviceMemory: [8, 16, 32],
    screens: [{ width: 1512, height: 982, devicePixelRatio: 2 }, { width: 1728, height: 1117, devicePixelRatio: 2 }],
    ua: `Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:${firefoxVersion}) Gecko/20100101 Firefox/${firefoxVersion}`,
    graphics: [{ vendor: "Apple", renderer: "Apple M1" }, { vendor: "Apple", renderer: "Apple M2" }]
  },
  "linux-firefox": {
    ...desktopFirefox,
    os: "Linux", osVersion: "6", navigatorPlatform: "Linux x86_64", screens: windowsScreens,
    ua: `Mozilla/5.0 (X11; Linux x86_64; rv:${firefoxVersion}) Gecko/20100101 Firefox/${firefoxVersion}`,
    graphics: [
      { vendor: "Intel", renderer: "Mesa Intel(R) UHD Graphics 630 (CFL GT2)" },
      { vendor: "AMD", renderer: "AMD Radeon RX 6600 (radeonsi, navi23, LLVM 17.0.6)" }
    ]
  },
  "android-firefox": {
    engine: "Firefox", browserVersion: firefoxVersion, os: "Android", osVersion: "14",
    navigatorPlatform: "Linux armv81", architecture: "arm", model: "Pixel 8", mobile: true,
    maxTouchPoints: 5, hardwareConcurrency: [8], deviceMemory: [8], locales,
    screens: [{ width: 412, height: 915, devicePixelRatio: 2.625 }, { width: 393, height: 873, devicePixelRatio: 2.75 }],
    ua: `Mozilla/5.0 (Android 14; Mobile; rv:${firefoxVersion}) Gecko/${firefoxVersion} Firefox/${firefoxVersion}`,
    graphics: [{ vendor: "Qualcomm", renderer: "Adreno (TM) 740" }]
  },
  "ios-firefox": {
    engine: "Firefox", browserVersion: firefoxVersion, os: "IOS", osVersion: "17.6",
    navigatorPlatform: "iPhone", architecture: "arm", model: "iPhone", mobile: true,
    maxTouchPoints: 5, hardwareConcurrency: [6], deviceMemory: [8], locales,
    screens: [{ width: 393, height: 852, devicePixelRatio: 3 }, { width: 430, height: 932, devicePixelRatio: 3 }],
    ua: `Mozilla/5.0 (iPhone; CPU iPhone OS 17_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) FxiOS/${firefoxVersion} Mobile/15E148 Safari/605.1.15`,
    graphics: [{ vendor: "Apple Inc.", renderer: "Apple GPU" }]
  }
};

export function listPresets() {
  return Object.keys(PRESETS);
}
