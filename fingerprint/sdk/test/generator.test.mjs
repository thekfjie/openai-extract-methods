import assert from "node:assert/strict";
import test from "node:test";
import { listPresets, PRESETS } from "../src/catalog.mjs";
import { generateProfile } from "../src/generator.mjs";
import { toRoxyConfig } from "../src/roxy-config.mjs";
import { validateBundle, validateProfile } from "../src/validate.mjs";

test("all catalogued presets generate valid internally consistent profiles", () => {
  assert.equal(listPresets().length, 13);
  for (const preset of listPresets()) {
    const profile = generateProfile({ preset, seed: "catalog-test" });
    const result = validateProfile(profile);
    assert.deepEqual(result.errors, [], `${preset}: ${result.errors.join(", ")}`);
  }
});

test("a fixed seed is reproducible", () => {
  const first = generateProfile({ preset: "windows-11-chrome", seed: "repeatable" });
  const second = generateProfile({ preset: "windows-11-chrome", seed: "repeatable" });
  assert.deepEqual(first, second);
});

test("different seeds produce different local noise and machine identities", () => {
  const first = generateProfile({ preset: "linux-x64-chrome", seed: "one" });
  const second = generateProfile({ preset: "linux-x64-chrome", seed: "two" });
  assert.notEqual(first.canvas.value, second.canvas.value);
  assert.notEqual(first.machine.macAddress, second.machine.macAddress);
  assert.notEqual(first.clientRects.noiseFactorX, second.clientRects.noiseFactorX);
});

test("Chrome and Firefox use their original branch-specific Roxy field names", () => {
  const chromeProfile = generateProfile({ preset: "windows-11-chrome", seed: "chrome" });
  const firefoxProfile = generateProfile({ preset: "windows-firefox", seed: "firefox" });
  const chrome = toRoxyConfig(chromeProfile);
  const firefox = toRoxyConfig(firefoxProfile);
  assert.ok(chrome.WebGL);
  assert.equal(chrome.webGL, undefined);
  assert.equal(typeof chrome.webRtcMode, "string");
  assert.ok(firefox.webGL);
  assert.equal(firefox.WebGL, undefined);
  assert.ok(firefox.webrtc);
  assert.equal(firefoxProfile.engine.userAgentMetadata, null);
});

test("Roxy bundles validate for every preset", () => {
  for (const preset of Object.keys(PRESETS)) {
    const profile = generateProfile({ preset, seed: `bundle-${preset}` });
    const result = validateBundle({ profile, roxyConfig: toRoxyConfig(profile) });
    assert.equal(result.valid, true, `${preset}: ${result.errors.join(", ")}`);
  }
});
