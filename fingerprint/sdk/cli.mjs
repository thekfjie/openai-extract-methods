#!/usr/bin/env node
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { parseArgs } from "node:util";
import { listPresets } from "./src/catalog.mjs";
import { createJsonBaseRecordProvider, createRoxyHttpBaseRecordProvider, generateProfilesWithProvider } from "./src/base-record-provider.mjs";
import { generateProfiles } from "./src/generator.mjs";
import { ORIGINAL_CATALOG_COUNTS } from "./src/original-algorithms.mjs";
import { toPortableFingerprint } from "./src/portable.mjs";
import { toRoxyConfig } from "./src/roxy-config.mjs";
import { toBrowserRuntime } from "./src/runtime.mjs";
import { validateBundle } from "./src/validate.mjs";

function usage() {
  return `Usage:
  node cli.mjs presets
  node cli.mjs catalogs
  node cli.mjs generate --preset windows-11-chrome --seed demo-001 [--count 5] [--out samples] [--format bundle|normalized|portable|roxy|runtime]
    [--feature-mode random|original-defaults] [--browser-version VERSION]
    [--webrtc disable|real|altered] [--remote-ip IP] [--host-os linux|windows|macos] [--app-port PORT]
    [--color-scheme system|light|dark] [--disable-gpu] [--sandbox-permission]
    [--window-mode maximized|normal] [--window-width 1280] [--window-height 720] [--window-position 0,0,tl]
    [--geolocation prompt|allow|block|disable] [--latitude N] [--longitude N] [--accuracy N]
  node cli.mjs generate-cloud --preset windows-11-chrome --base-url https://authorized.example/api/window --headers-file ./cloud-headers.json
    [all generate options] [--no-cloud-mac] [--base-records-out ./authorized-records.json]
  node cli.mjs validate samples/profile-001.json`;
}

function artifact(profile, format, roxyOptions, runtimeOptions) {
  const roxyConfig = toRoxyConfig(profile, roxyOptions);
  const runtimeConfig = toBrowserRuntime(profile, runtimeOptions);
  if (format === "normalized") return profile;
  if (format === "portable") return toPortableFingerprint(profile);
  if (format === "roxy") return roxyConfig;
  if (format === "runtime") return runtimeConfig;
  return { profile, roxyConfig, runtimeConfig };
}

function parseWindowPosition(value) {
  const [x = "0", y = "0", anchor = "tl"] = String(value).split(",");
  const position = { xRatio: Number(x), yRatio: Number(y), anchor };
  if (!Number.isFinite(position.xRatio) || !Number.isFinite(position.yRatio)) throw new Error("--window-position must be X_RATIO,Y_RATIO,ANCHOR");
  return position;
}

async function readHeadersFile(file) {
  if (!file) throw new Error("generate-cloud requires --headers-file containing your officially issued authorization headers");
  const headers = JSON.parse(await readFile(resolve(file), "utf8"));
  if (!headers || typeof headers !== "object" || Array.isArray(headers)) throw new Error("--headers-file must contain a JSON object");
  return headers;
}

async function generate(argv, cloud = false) {
  const { values } = parseArgs({
    args: argv,
    options: {
      preset: { type: "string" },
      seed: { type: "string", default: "local-test" },
      count: { type: "string", default: "1" },
      out: { type: "string" },
      format: { type: "string", default: "bundle" },
      "no-noise": { type: "boolean", default: false },
      "feature-mode": { type: "string", default: "random" },
      "browser-version": { type: "string" },
      webrtc: { type: "string", default: "disable" },
      "remote-ip": { type: "string" },
      "host-os": { type: "string" },
      "app-port": { type: "string", default: "0" },
      "color-scheme": { type: "string", default: "system" },
      "disable-gpu": { type: "boolean", default: false },
      "sandbox-permission": { type: "boolean", default: false },
      "window-mode": { type: "string", default: "maximized" },
      "window-width": { type: "string", default: "1280" },
      "window-height": { type: "string", default: "720" },
      "window-position": { type: "string", default: "0,0,tl" },
      "user-data-dir": { type: "string", default: "<USER_DATA_DIR>" },
      geolocation: { type: "string" },
      latitude: { type: "string" },
      longitude: { type: "string" },
      accuracy: { type: "string" },
      "base-url": { type: "string" },
      "headers-file": { type: "string" },
      "no-cloud-mac": { type: "boolean", default: false },
      "base-records-out": { type: "string" }
    },
    strict: true
  });
  if (!values.preset) throw new Error("--preset is required");
  if (!listPresets().includes(values.preset)) throw new Error(`Unknown preset: ${values.preset}`);
  if (!["bundle", "normalized", "portable", "roxy", "runtime"].includes(values.format)) throw new Error(`Unknown format: ${values.format}`);
  const count = Number(values.count);
  if (!Number.isInteger(count) || count < 1 || count > 1_000) throw new Error("--count must be an integer from 1 to 1000");
  const appPort = Number(values["app-port"]);
  if (!Number.isInteger(appPort) || appPort < 0 || appPort > 65_535) throw new Error("--app-port must be an integer from 0 to 65535");
  const windowWidth = Number(values["window-width"]);
  const windowHeight = Number(values["window-height"]);
  const windowPosition = parseWindowPosition(values["window-position"]);

  const generateOptions = {
    preset: values.preset,
    seed: values.seed,
    count,
    noise: !values["no-noise"],
    featureMode: values["feature-mode"],
    browserVersion: values["browser-version"],
    webrtcMode: values.webrtc,
    remoteIp: values["remote-ip"],
    gpuAcceleration: !values["disable-gpu"],
    sandboxPermission: values["sandbox-permission"],
    colorScheme: values["color-scheme"],
    windowMode: values["window-mode"],
    windowWidth,
    windowHeight,
    windowPosition,
    geolocationMode: values.geolocation,
    latitude: values.latitude,
    longitude: values.longitude,
    accuracy: values.accuracy
  };
  let profiles;
  if (cloud) {
    if (!values["base-url"]) throw new Error("generate-cloud requires --base-url for the officially authorized API prefix");
    const headers = await readHeadersFile(values["headers-file"]);
    let provider = createRoxyHttpBaseRecordProvider({ baseUrl: values["base-url"], headers });
    if (values["base-records-out"]) {
      const records = await provider.getRecords({
        preset: values.preset,
        count,
        browserVersion: values["browser-version"],
        includeMac: !values["no-cloud-mac"]
      });
      const recordsPath = resolve(values["base-records-out"]);
      await mkdir(dirname(recordsPath), { recursive: true });
      await writeFile(recordsPath, `${JSON.stringify(records, null, 2)}\n`, { mode: 0o600 });
      provider = createJsonBaseRecordProvider(records, { name: "authorized-http-snapshot", deterministic: true });
    }
    profiles = await generateProfilesWithProvider({
      ...generateOptions,
      provider,
      includeMac: !values["no-cloud-mac"]
    });
  } else {
    profiles = generateProfiles(generateOptions);
  }
  const results = profiles.map((profile) => artifact(profile, values.format, {
    hostOs: values["host-os"],
    appPort
  }, {
    hostOs: values["host-os"],
    userDataDir: values["user-data-dir"]
  }));

  if (!values.out) {
    process.stdout.write(`${JSON.stringify(count === 1 ? results[0] : results, null, 2)}\n`);
    return;
  }

  const outputDir = resolve(values.out);
  await mkdir(outputDir, { recursive: true });
  await Promise.all(results.map((value, index) => writeFile(
    resolve(outputDir, `profile-${String(index + 1).padStart(3, "0")}.json`),
    `${JSON.stringify(value, null, 2)}\n`,
    "utf8"
  )));
  process.stdout.write(`Generated ${count} profile(s) in ${outputDir}\n`);
}

async function validate(file) {
  if (!file) throw new Error("validate requires a JSON file");
  const value = JSON.parse(await readFile(resolve(file), "utf8"));
  const result = validateBundle(value);
  if (!result.valid) {
    process.stderr.write(`${result.errors.map((error) => `- ${error}`).join("\n")}\n`);
    process.exitCode = 1;
    return;
  }
  process.stdout.write(`Valid fingerprint profile: ${resolve(file)}\n`);
}

const [command, ...argv] = process.argv.slice(2);
try {
  if (command === "presets") process.stdout.write(`${listPresets().join("\n")}\n`);
  else if (command === "catalogs") process.stdout.write(`${JSON.stringify(ORIGINAL_CATALOG_COUNTS, null, 2)}\n`);
  else if (command === "generate") await generate(argv);
  else if (command === "generate-cloud") await generate(argv, true);
  else if (command === "validate") await validate(argv[0]);
  else throw new Error(usage());
} catch (error) {
  process.stderr.write(`${error.message}\n`);
  process.exitCode = 1;
}
