#!/usr/bin/env node

import { readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";

const [sourceMapPath = "/tmp/roxybrowser-audit/asar/dist/main.mjs.map", outputPath = "src/original-data.mjs"] = process.argv.slice(2);
const sourceMap = JSON.parse(await readFile(resolve(sourceMapPath), "utf8"));
const source = sourceMap.sourcesContent?.[76];

if (!source) throw new Error("sourcesContent[76] is missing");

function decodeString(value) {
  return JSON.parse(`"${value.replaceAll('"', '\\"')}"`);
}

function extractSplit(label, pattern, delimiter) {
  const match = source.match(pattern);
  if (!match) throw new Error(`Unable to extract ${label}`);
  return decodeString(match[1]).split(delimiter);
}

function extractVoices(label, startMarker, endMarker) {
  const start = source.indexOf(startMarker);
  const end = source.indexOf(endMarker, start);
  if (start < 0 || end < 0) throw new Error(`Unable to locate ${label}`);
  const block = source.slice(start, end);
  const voices = [];
  const pattern = /\{\s*name:\s*"([^"]*)",\s*lang:\s*"([^"]*)",\s*isLocalService:\s*!(\d)\s*\}/g;
  for (const match of block.matchAll(pattern)) {
    voices.push({ name: match[1], lang: match[2], isLocalService: match[3] === "0" });
  }
  if (voices.length === 0) throw new Error(`No voices extracted for ${label}`);
  return voices;
}

const knownFonts = extractSplit(
  "known font pool",
  /var hn = \/\* @__PURE__ \*\/ "([\s\S]*?)"\.split\(","\), gn =/,
  ","
);
const windowsCoreFonts = extractSplit(
  "Windows core font pool",
  /gn = \/\* @__PURE__ \*\/ "([\s\S]*?)"\.split\("\."\);/,
  "."
);
const allowFonts = extractSplit(
  "allow-font pool",
  /\], kn = \/\* @__PURE__ \*\/ "([\s\S]*?)"\.split\(","\);\s*function An/,
  ","
);
const chromeVoices = extractVoices("Chrome voices", "var Dn = [", "], On = [");
const firefoxVoices = extractVoices("Firefox voices", "], On = [", "], kn =");

const output = `// Generated from RoxyBrowser 3.9.2 sourcesContent[76].\n` +
  `// Run scripts/extract-original-catalogs.mjs to reproduce this file.\n\n` +
  `export const ORIGINAL_KNOWN_FONTS = ${JSON.stringify(knownFonts, null, 2)};\n\n` +
  `export const ORIGINAL_WINDOWS_CORE_FONTS = ${JSON.stringify(windowsCoreFonts, null, 2)};\n\n` +
  `export const ORIGINAL_ALLOW_FONTS = ${JSON.stringify(allowFonts, null, 2)};\n\n` +
  `export const ORIGINAL_CHROME_VOICES = ${JSON.stringify(chromeVoices, null, 2)};\n\n` +
  `export const ORIGINAL_FIREFOX_VOICES = ${JSON.stringify(firefoxVoices, null, 2)};\n`;

await writeFile(resolve(outputPath), output, "utf8");
console.log(JSON.stringify({
  output: resolve(outputPath),
  knownFonts: knownFonts.length,
  windowsCoreFonts: windowsCoreFonts.length,
  allowFonts: allowFonts.length,
  chromeVoices: chromeVoices.length,
  firefoxVoices: firefoxVoices.length
}, null, 2));
