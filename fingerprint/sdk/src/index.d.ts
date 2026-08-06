export type PresetName =
  | "windows-11-chrome"
  | "windows-10-chrome"
  | "macos-intel-chrome"
  | "macos-apple-chrome"
  | "linux-x64-chrome"
  | "android-chrome"
  | "ios-chrome"
  | "windows-firefox"
  | "macos-firefox"
  | "macos-apple-firefox"
  | "linux-firefox"
  | "android-firefox"
  | "ios-firefox";

export type BrowserEngine = "Chrome" | "Firefox";
export type FeatureMode = "random" | "original-defaults";
export type WebRtcMode = "altered" | "real" | "disable";
export type ColorScheme = "system" | "light" | "dark";
export type WindowMode = "maximized" | "normal";
export type WindowAnchor = "tl" | "tr" | "br" | "bl";

export interface Voice {
  name: string;
  lang: string;
  isLocalService: boolean;
}

export interface PluginMime {
  mime_type: string;
  file_extensions: string;
}

export interface Plugin {
  name: string;
  path: string;
  desc: string;
  version: string;
  mime: PluginMime[];
}

export interface MediaDevice {
  deviceType: "audioinput" | "audiooutput" | "videoinput" | string;
  deviceId: string;
  label: string;
  groupId: string;
}

export interface FingerprintProfile {
  schemaVersion: 2;
  purpose: "local-browser-compatibility-testing";
  id: string;
  seed: string;
  preset: PresetName;
  generator: {
    algorithm: "roxybrowser-3.9.2-compatible";
    featureMode: FeatureMode;
    deterministic: boolean;
    baseDataSource: "local-template" | "authorized-provider";
    provider: string | null;
  };
  engine: {
    family: BrowserEngine;
    version: string;
    userAgent: string;
    userAgentMetadata: null | {
      brands: Array<{ brand: string; version: string }>;
      fullVersionList: Array<{ brand: string; version: string }>;
      platform: string;
      mobile: boolean;
      platformVersion: string;
      architecture: string;
      model: string;
      bitness: string;
      wow64: boolean;
    };
  };
  os: { name: "Windows" | "macOS" | "Linux" | "Android" | "IOS"; version: string; architecture: string; model: string };
  machine: { computerName: string; macAddress: string };
  locale: { appLocale: string; acceptLanguage: string; timezone: string };
  navigator: {
    platform: string;
    hardwareConcurrency: number;
    deviceMemory: number;
    maxTouchPoints: number;
    mobile: boolean;
    doNotTrack: boolean;
    pluginsEnabled: boolean;
    plugins: Plugin[];
  };
  screen: {
    width: number;
    height: number;
    availWidth: number;
    availHeight: number;
    colorDepth: number;
    pixelDepth: number;
    devicePixelRatio: number;
  };
  graphics: {
    webglInfoEnabled: boolean;
    webglVendor: string;
    webglRenderer: string;
    webgpu: { mode: string; vendor: string };
    noise: { enabled: boolean; interval: 1; value: string | number };
  };
  canvas: { enabled: boolean; value: string; valueV2: number };
  audioContext: { enabled: boolean; value: number; version: "2"; interval: 100 };
  clientRects: { enabled: boolean; noiseFactorX: number; noiseFactorY: number };
  fonts: { enabled: boolean; localCatalog: string[]; disabled: string[]; allowed: string[] };
  speechSynthesis: { enabled: boolean; voices: Voice[] };
  mediaDevices: { enabled: boolean; devices: MediaDevice[] };
  webrtc: { mode: WebRtcMode; remoteIp: string | null };
  geolocation: {
    mode: "prompt" | "allow" | "block" | "disable";
    enableFakeLocationData: boolean;
    latitude: number | null;
    longitude: number | null;
    accuracy: number | null;
    altitude: number | null;
  };
  content: {
    blockImages: boolean;
    imageSizeLimit: number | null;
    disablePlayVideo: boolean;
    disablePlaySound: boolean;
    disablePasswordSaveTips: boolean;
  };
  security: {
    ignoreCertificateErrors: boolean;
    sslCipherSuiteBlacklist: string[];
    portScanProtection: boolean;
    portScanAllowList: Array<string | number>;
  };
  runtime: {
    gpuAcceleration: boolean;
    sandboxPermission: boolean;
    colorScheme: ColorScheme;
    window: {
      mode: WindowMode;
      width: number;
      height: number;
      position: { xRatio: number; yRatio: number; anchor: WindowAnchor };
    };
  };
  battery: {
    enabled: boolean;
    charging: boolean;
    chargingTime: number | string;
    dischargingTime: number | string;
    level: number | string;
  };
  network: {
    enabled: boolean;
    type: string;
    effectiveType: string;
    downlink: number | string;
    downlinkMax: number | string;
    rtt: number | string;
    saveData: boolean;
  };
  bluetooth: { enabled: boolean; adapterAvailable: boolean };
}

export interface GenerateOptions {
  preset: PresetName;
  seed?: string;
  noise?: boolean;
  featureMode?: FeatureMode;
  browserVersion?: string;
  webrtcMode?: WebRtcMode;
  remoteIp?: string | null;
  gpuAcceleration?: boolean;
  sandboxPermission?: boolean;
  colorScheme?: ColorScheme;
  windowMode?: WindowMode;
  windowWidth?: number;
  windowHeight?: number;
  windowPosition?: { xRatio: number; yRatio: number; anchor: WindowAnchor };
  geolocationMode?: "prompt" | "allow" | "block" | "disable";
  latitude?: number | string | null;
  longitude?: number | string | null;
  accuracy?: number | string | null;
}

export interface GenerateManyOptions extends GenerateOptions {
  count?: number;
}

export interface RoxyAdapterOptions {
  hostOs?: "linux" | "windows" | "macos" | "win32" | "darwin";
  appPort?: number;
  windowNum?: number;
}

export interface RuntimeAdapterOptions {
  hostOs?: "linux" | "windows" | "macos" | "win32" | "darwin";
  userDataDir?: string;
  virtualWorkArea?: { originX: number; originY: number; width: number; height: number };
  coreVersion?: string;
  existingPreferences?: Record<string, unknown>;
  existingLocalState?: Record<string, unknown>;
  existingXulstore?: Record<string, unknown>;
}

export interface BaseRecordProvider {
  name?: string;
  deterministic?: boolean;
  getRecords(options: {
    preset: PresetName;
    count: number;
    browserVersion?: string;
    includeMac: boolean;
  }): Promise<Record<string, unknown>[]>;
}

export interface HttpBaseRecordProviderOptions {
  baseUrl: string;
  headers?: Record<string, string>;
  fetchImpl?: (input: URL | string, init?: Record<string, unknown>) => Promise<{
    ok: boolean;
    status: number;
    json(): Promise<unknown>;
  }>;
  timeoutMs?: number;
  name?: string;
}

export interface ValidationResult {
  valid: boolean;
  errors: string[];
}

export const PRESETS: Record<PresetName, Record<string, unknown>>;
export const FONT_CATALOGS: Record<string, string[]>;
export const ORIGINAL_CATALOG_COUNTS: Readonly<{
  knownFonts: number;
  windowsCoreFonts: number;
  allowFonts: number;
  chromeVoices: number;
  firefoxVoices: number;
}>;
export const RUNTIME_CONSTANTS: Readonly<{
  colorSchemeCodes: Readonly<Record<ColorScheme, number>>;
  firefoxThemeIds: Readonly<Record<ColorScheme, string>>;
  firefoxManagedThemeIds: readonly string[];
}>;
export const ROXY_CLOUD_ENDPOINTS: Readonly<{
  uaWebgl: "/user_get_ua_webgl_v2";
  deviceName: "/user_get_device_name_v2";
  macAddress: "/user_get_mac_addr_v2";
}>;

export function listPresets(): PresetName[];
export function generateProfile(options: GenerateOptions): FingerprintProfile;
export function generateProfiles(options: GenerateManyOptions): FingerprintProfile[];
export function generateProfilesWithProvider(options: GenerateManyOptions & { provider: BaseRecordProvider; includeMac?: boolean }): Promise<FingerprintProfile[]>;
export function applyAuthorizedBaseRecord(profile: FingerprintProfile, record: Record<string, unknown>, options?: { providerName?: string; deterministic?: boolean; includeMac?: boolean }): FingerprintProfile;
export function createRoxyHttpBaseRecordProvider(options: HttpBaseRecordProviderOptions): BaseRecordProvider;
export function createJsonBaseRecordProvider(records: Record<string, unknown>[], options?: { name?: string; deterministic?: boolean }): BaseRecordProvider;
export function toPortableFingerprint(profile: FingerprintProfile): Record<string, unknown>;
export function toRoxyConfig(profile: FingerprintProfile, options?: RoxyAdapterOptions): Record<string, unknown>;
export function toChromeConfig(profile: FingerprintProfile, options?: RoxyAdapterOptions): Record<string, unknown>;
export function toFirefoxConfig(profile: FingerprintProfile, options?: RoxyAdapterOptions): Record<string, unknown>;
export function toBrowserRuntime(profile: FingerprintProfile, options?: RuntimeAdapterOptions): Record<string, unknown>;
export function toChromeRuntime(profile: FingerprintProfile, options?: RuntimeAdapterOptions): Record<string, unknown>;
export function toFirefoxRuntime(profile: FingerprintProfile, options?: RuntimeAdapterOptions): Record<string, unknown>;
export function validateProfile(profile: FingerprintProfile): ValidationResult;
export function validateBundle(value: FingerprintProfile | {
  profile: FingerprintProfile;
  roxyConfig?: Record<string, unknown>;
  runtimeConfig?: Record<string, unknown>;
}): ValidationResult;
export function deriveSeed(seed: string, index: number): string;
export function createPrng(seed: string): {
  float(min?: number, max?: number): number;
  int(min: number, max: number): number;
  bool(probability?: number): boolean;
  pick<T>(values: T[]): T;
  sample<T>(values: T[], count: number): T[];
  hex(length: number, uppercase?: boolean): string;
};
