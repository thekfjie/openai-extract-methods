import {
  ORIGINAL_ALLOW_FONTS,
  ORIGINAL_CHROME_VOICES,
  ORIGINAL_FIREFOX_VOICES,
  ORIGINAL_KNOWN_FONTS,
  ORIGINAL_WINDOWS_CORE_FONTS
} from "./original-data.mjs";

function lower(value) {
  return value.toLocaleLowerCase("en-US");
}

function originalIntersection(first, second) {
  const larger = first.length > second.length ? first : second;
  const smaller = first.length > second.length ? second : first;
  const result = [];
  for (const smallValue of smaller) {
    for (const largeValue of larger) {
      if (lower(smallValue) === lower(largeValue)) result.push(largeValue);
    }
  }
  return result;
}

function difference(first, second) {
  const lookup = new Set(second.map(lower));
  return first.filter((value) => !lookup.has(lower(value)));
}

function originalUnion(first, second) {
  return [...new Set([...first, ...second])];
}

function originalRandomSubset(values, maximum, prng) {
  if (values.length === 0 || maximum <= 0) return [];
  const count = prng.int(1, maximum);
  if (values.length < count) return [...values];
  const selected = new Set();
  let attempts = 0;
  while (selected.size < count && attempts < 100) {
    selected.add(values[prng.int(0, values.length - 1)]);
    attempts += 1;
  }
  return [...selected];
}

function originalSample(values, size, prng) {
  if (size > values.length) throw new Error("Size must be less than or equal to the length of array.");
  const result = Array(size);
  const selectedIndexes = new Set();
  for (let index = values.length - size, outputIndex = 0; index < values.length; index += 1, outputIndex += 1) {
    let selectedIndex = prng.int(0, index);
    if (selectedIndexes.has(selectedIndex)) selectedIndex = index;
    selectedIndexes.add(selectedIndex);
    result[outputIndex] = values[selectedIndex];
  }
  return result;
}

export function generateDisabledFonts(localFonts, prng) {
  const withoutWindowsCore = difference(localFonts, originalIntersection(localFonts, ORIGINAL_WINDOWS_CORE_FONTS));
  const knownLocalFonts = originalIntersection(withoutWindowsCore, ORIGINAL_KNOWN_FONTS);
  const knownSelection = originalRandomSubset(knownLocalFonts, Math.min(5, knownLocalFonts.length), prng);
  const localSelection = originalRandomSubset(withoutWindowsCore, Math.min(5, withoutWindowsCore.length), prng);
  return originalUnion(knownSelection, localSelection);
}

export function generateAllowFonts(prng) {
  return originalSample(ORIGINAL_ALLOW_FONTS, prng.int(100, 499), prng);
}

export function generateCanvasNoise(engine, prng) {
  return {
    value: prng.hex(32),
    valueV2: engine === "Firefox" ? prng.float() : prng.int(1_000, 99_999)
  };
}

export function generateWebglNoise(engine, prng) {
  if (engine === "Firefox") return prng.float();
  const alphabet = "ABCDEFGHIJKLMNOPQISTUVWXYZ0123456789";
  return `${prng.int(0, 9)}${prng.int(0, 9)}${Array.from({ length: 14 }, () => alphabet[prng.int(0, 35)]).join("")}`;
}

function removeAtOriginalRange(values, prng) {
  if (values.length <= 1) return values.shift();
  const index = prng.int(0, values.length - 2);
  return values.splice(index, 1)[0];
}

export function generateSpeechVoices(os, engine, prng) {
  const voices = [...(engine === "Firefox" ? ORIGINAL_FIREFOX_VOICES : ORIGINAL_CHROME_VOICES)].map((voice) => ({ ...voice }));
  if (prng.bool(0.5)) removeAtOriginalRange(voices, prng);

  const selected = [];
  for (let index = 0; index < 3 && voices.length > 0; index += 1) {
    const voice = removeAtOriginalRange(voices, prng);
    selected.push({ ...voice, isLocalService: true });
  }

  const windowsChromeVoices = engine === "Chrome" && (os === "Windows" || !os) ? [
    { name: "Microsoft David - English (United States)", isLocalService: true, lang: "en-US" },
    { name: "Microsoft Mark - English (United States)", isLocalService: true, lang: "en-US" },
    { name: "Microsoft Zira - English (United States)", isLocalService: true, lang: "en-US" }
  ] : [];

  return [...windowsChromeVoices, ...selected, ...voices];
}

export function generateClientRectsNoise(prng) {
  return {
    noiseFactorX: prng.float(-1, 1),
    noiseFactorY: prng.float(-1, 1)
  };
}

export const ORIGINAL_CATALOG_COUNTS = Object.freeze({
  knownFonts: ORIGINAL_KNOWN_FONTS.length,
  windowsCoreFonts: ORIGINAL_WINDOWS_CORE_FONTS.length,
  allowFonts: ORIGINAL_ALLOW_FONTS.length,
  chromeVoices: ORIGINAL_CHROME_VOICES.length,
  firefoxVoices: ORIGINAL_FIREFOX_VOICES.length
});
