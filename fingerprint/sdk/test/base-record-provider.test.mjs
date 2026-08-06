import assert from "node:assert/strict";
import test from "node:test";
import {
  ROXY_CLOUD_ENDPOINTS,
  createJsonBaseRecordProvider,
  createRoxyHttpBaseRecordProvider,
  generateProfilesWithProvider,
  validateProfile
} from "../src/index.mjs";

function jsonResponse(body) {
  return {
    ok: true,
    status: 200,
    async json() { return body; }
  };
}

test("authorized HTTP provider calls the three original Roxy endpoints with original queries", async () => {
  const requests = [];
  const fetchImpl = async (url, options) => {
    requests.push({ url: new URL(url), options });
    if (url.pathname.endsWith(ROXY_CLOUD_ENDPOINTS.uaWebgl)) return jsonResponse({
      code: 0,
      data: [{
        userAgentNew: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.7559.236 Safari/537.36",
        userAgentVersion: "144.0.7559.236",
        webGLManufacturer: "Google Inc. (Cloud Intel)",
        webGLRender: "ANGLE (Cloud Intel Renderer)",
        platform: "Windows",
        platformVersion: "15.0.0",
        architecture: "x86",
        navigatorPlatform: "Win32",
        resolutionX: 1920,
        resolutionY: 1080,
        devicePixelRatio: 1.25
      }]
    });
    if (url.pathname.endsWith(ROXY_CLOUD_ENDPOINTS.deviceName)) return jsonResponse({ code: 0, data: [{ computerName: "DESKTOP-CLOUD1" }] });
    if (url.pathname.endsWith(ROXY_CLOUD_ENDPOINTS.macAddress)) return jsonResponse({ code: 0, data: [{ macAddr: "00-11-22-33-44-55" }] });
    throw new Error(`Unexpected URL: ${url}`);
  };
  const provider = createRoxyHttpBaseRecordProvider({
    baseUrl: "https://authorized.example/api/window",
    headers: { token: "official-test-token" },
    fetchImpl
  });
  const [profile] = await generateProfilesWithProvider({
    provider,
    preset: "windows-11-chrome",
    seed: "cloud-provider-test",
    count: 1
  });

  assert.equal(requests.length, 3);
  assert.deepEqual(requests.map(({ url }) => url.pathname).sort(), [
    "/api/window/user_get_device_name_v2",
    "/api/window/user_get_mac_addr_v2",
    "/api/window/user_get_ua_webgl_v2"
  ]);
  for (const { url, options } of requests) {
    assert.equal(url.searchParams.get("coreType"), "Chrome");
    assert.equal(url.searchParams.get("count"), "1");
    assert.equal(options.headers.token, "official-test-token");
    assert.equal(options.headers.source, "api");
  }
  const uaRequest = requests.find(({ url }) => url.pathname.endsWith(ROXY_CLOUD_ENDPOINTS.uaWebgl)).url;
  assert.equal(uaRequest.searchParams.get("os"), "Windows");
  assert.equal(uaRequest.searchParams.get("osVersion"), "11");
  assert.equal(uaRequest.searchParams.get("coreVersion"), "144.0.7559.236");
  assert.equal(profile.generator.baseDataSource, "authorized-provider");
  assert.equal(profile.generator.provider, "roxy-authorized-http");
  assert.equal(profile.generator.deterministic, false);
  assert.equal(profile.machine.computerName, "DESKTOP-CLOUD1");
  assert.equal(profile.machine.macAddress, "00:11:22:33:44:55");
  assert.equal(profile.graphics.webglVendor, "Google Inc. (Cloud Intel)");
  assert.equal(profile.graphics.webglRenderer, "ANGLE (Cloud Intel Renderer)");
  assert.equal(profile.screen.devicePixelRatio, 1.25);
  assert.equal(validateProfile(profile).valid, true);
});

test("authorized JSON provider supports offline imports without network access", async () => {
  const provider = createJsonBaseRecordProvider([{
    userAgentNew: "Mozilla/5.0 (X11; Linux x86_64; rv:146.0) Gecko/20100101 Firefox/146.0",
    userAgentVersion: "146.0",
    webGLManufacturer: "Intel",
    webGLRender: "Mesa Intel Cloud Catalog",
    deviceName: "linux-authorized",
    macAddr: "02:AA:BB:CC:DD:EE"
  }]);
  const [profile] = await generateProfilesWithProvider({
    provider,
    preset: "linux-firefox",
    seed: "json-provider-test",
    count: 1
  });
  assert.equal(profile.generator.baseDataSource, "authorized-provider");
  assert.equal(profile.generator.provider, "authorized-json");
  assert.equal(profile.generator.deterministic, true);
  assert.equal(profile.machine.computerName, "linux-authorized");
  assert.equal(profile.graphics.webglRenderer, "Mesa Intel Cloud Catalog");
  assert.equal(validateProfile(profile).valid, true);
});

test("authorized HTTP provider can skip the MAC endpoint and preserve the local generated MAC", async () => {
  const paths = [];
  const provider = createRoxyHttpBaseRecordProvider({
    baseUrl: "https://authorized.example/api/window",
    headers: { token: "official-test-token" },
    fetchImpl: async (url) => {
      paths.push(url.pathname);
      if (url.pathname.endsWith(ROXY_CLOUD_ENDPOINTS.uaWebgl)) return jsonResponse({ code: 0, data: [{
        userAgentNew: "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.7559.236 Safari/537.36",
        userAgentVersion: "144.0.7559.236",
        webGLManufacturer: "Google Inc. (Intel)",
        webGLRender: "ANGLE (Authorized Intel Renderer)"
      }] });
      return jsonResponse({ code: 0, data: [{ deviceName: "linux-authorized-http" }] });
    }
  });
  const [profile] = await generateProfilesWithProvider({
    provider,
    preset: "linux-x64-chrome",
    seed: "no-cloud-mac",
    count: 1,
    includeMac: false
  });
  assert.equal(paths.length, 2);
  assert.equal(paths.some((path) => path.endsWith(ROXY_CLOUD_ENDPOINTS.macAddress)), false);
  assert.match(profile.machine.macAddress, /^02:/);
  assert.equal(validateProfile(profile).valid, true);
});

test("provider rejects incomplete or wrong-engine cloud records", async () => {
  await assert.rejects(
    generateProfilesWithProvider({
      provider: createJsonBaseRecordProvider([{ userAgentNew: "Mozilla/5.0 Chrome/144.0.0.0" }]),
      preset: "windows-11-chrome",
      count: 1
    }),
    /missing webglVendor/
  );
  await assert.rejects(
    generateProfilesWithProvider({
      provider: createJsonBaseRecordProvider([{
        userAgentNew: "Mozilla/5.0 Firefox/146.0",
        userAgentVersion: "146.0",
        webGLManufacturer: "Intel",
        webGLRender: "Renderer",
        computerName: "wrong-engine"
      }]),
      preset: "windows-11-chrome",
      count: 1
    }),
    /does not match Chrome/
  );
});
