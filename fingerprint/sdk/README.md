# RoxyBrowser Fingerprint Lab 2.0

这是从 RoxyBrowser 3.9.2 中独立抽取的、可复用的确定性随机指纹 SDK。它只处理浏览器环境模拟，不包含会员、登录、令牌、配额、支付、Cookie 同步或代理账号逻辑。

## 已恢复的指纹范围

- 浏览器引擎、版本、UA 和 Chrome UA Client Hints metadata。
- OS、架构、设备型号、`navigator.platform`、CPU 核数、内存、触控点和 DNT。
- 分辨率、可用区域、色深、像素深度、DPR，以及原程序的宿主系统特殊分支。
- Canvas V1/V2、WebGL vendor/renderer/noise、WebGPU mode/vendor。
- AudioContext、ClientRects。
- 原包完整字体数据：486 个已知字体、55 个 Windows 核心字体、1484 个允许字体。
- 原包完整语音数据：19 个 Chrome voices、37 个 Firefox voices，以及 Windows Chrome 的 3 个额外本地语音。
- PDF plugins、mediaDevices、WebRTC、地理位置。
- Battery Status、Network Information、Bluetooth。
- 图片/媒体/声音/密码提示行为、HTTPS 错误、SSL cipher blacklist、端口扫描保护。
- 设备名和本地管理 MAC 地址。
- GPU 开关、Chrome 固定启动 flags、Firefox WebRender、内容沙箱、颜色主题和窗口尺寸/位置。

字段与原代码的逐项覆盖见 [FIELD-COVERAGE.md](./FIELD-COVERAGE.md)，源码函数和云端/本地边界见 [EXTRACTION-NOTES.md](./EXTRACTION-NOTES.md)。

## CLI

```bash
cd /opt/automyai/fingerprint/sdk

node cli.mjs presets
node cli.mjs catalogs

node cli.mjs generate \
  --preset windows-11-chrome \
  --seed demo-001 \
  --count 5 \
  --out samples/demo

node cli.mjs validate samples/demo/profile-001.json
npm test
```

获得官方授权后，可以让基础 UA/WebGL/设备记录来自原版三条接口：

```bash
cp cloud-headers.example.json /secure/path/roxy-cloud-headers.json
chmod 600 /secure/path/roxy-cloud-headers.json

node cli.mjs generate-cloud \
  --preset windows-11-chrome \
  --seed authorized-001 \
  --base-url https://YOUR-OFFICIALLY-AUTHORIZED-API-PREFIX \
  --headers-file /secure/path/roxy-cloud-headers.json \
  --base-records-out /secure/path/authorized-fingerprint-records.json \
  --out samples/authorized-001
```

`--base-url` 是三条 endpoint 之前的 API 前缀。headers 文件必须来自你自己正式获批的账号或 API 凭据；SDK 不读取 Roxy 登录状态、不提取 token，也不内置账号信息。不要把真实 headers 文件放入项目、npm 包或 Git。

RoxyBrowser 当前公开文档说明的常规 API 是桌面程序开启后的本地 API，需要在“API 配置”中启用并取得 API Key；其调用额度取决于订阅方案。这里恢复的三条基础记录 endpoint 并未在公开文档中承诺对外开放，因此实际 API 前缀和访问权限需要向官方技术支持书面申请，不能把普通本地 API Key 默认视为拥有这些内部接口权限。

AutoMyAI 已将两条路径分别接入：`OAI_FINGERPRINT_CLOUD_ENABLED` 控制下方的云端
基础记录 Provider；`ROXY_OPENAPI_ENABLED` 控制官方本地 OpenAPI 管理连接。后者可
读取工作区和环境详情，并调用 `browser/random_env`，但不会被标记为云端基础记录来源。

支持的模板：

- `windows-11-chrome`
- `windows-10-chrome`
- `macos-intel-chrome`
- `macos-apple-chrome`
- `linux-x64-chrome`
- `android-chrome`
- `ios-chrome`
- `windows-firefox`
- `macos-firefox`
- `macos-apple-firefox`
- `linux-firefox`
- `android-firefox`
- `ios-firefox`

可选参数：

```bash
--format bundle|normalized|portable|roxy|runtime
--feature-mode random|original-defaults
--browser-version 145.0.0.0
--no-noise
--webrtc disable|real|altered
--remote-ip 203.0.113.8
--geolocation prompt|allow|block|disable
--latitude 37.7749 --longitude=-122.4194 --accuracy 25
--host-os linux|windows|macos
--app-port 45535
--color-scheme system|light|dark
--disable-gpu
--sandbox-permission
--window-mode maximized|normal
--window-width 1280 --window-height 720
--window-position 0.5,0.5,tl
--user-data-dir /path/to/profile
```

`bundle` 同时输出标准化 `profile`、Roxy `roxyConfig` 和浏览器 `runtimeConfig`。`portable` 输出适合其他项目消费的浏览器 API 分类结构，`runtime` 只输出启动参数和 profile 文件内容。

`generate-cloud` 支持相同的输出格式和生成选项，并额外支持：

```bash
--base-url https://YOUR-OFFICIALLY-AUTHORIZED-API-PREFIX
--headers-file /secure/path/roxy-cloud-headers.json
--no-cloud-mac
--base-records-out /secure/path/authorized-fingerprint-records.json
```

`--base-records-out` 会以权限 `0600` 保存合并后的 UA/WebGL/设备/MAC 基础记录，不写入请求 headers。当前生成会立即改用该快照，因此 `generator.provider` 为 `authorized-http-snapshot` 且可确定性重放。

## SDK

已经生成可直接安装的本地包：

```bash
npm install /opt/automyai/fingerprint/sdk/dist/roxybrowser-fingerprint-lab-2.0.0.tgz
```

```js
import { readFile } from "node:fs/promises";
import {
  generateProfile,
  generateProfiles,
  generateProfilesWithProvider,
  createRoxyHttpBaseRecordProvider,
  createJsonBaseRecordProvider,
  toPortableFingerprint,
  toRoxyConfig,
  toBrowserRuntime,
  validateProfile
} from "/opt/automyai/fingerprint/sdk/src/index.mjs";

const profile = generateProfile({
  preset: "linux-x64-chrome",
  seed: "project-A-user-001",
  featureMode: "random",
  browserVersion: "144.0.7559.236"
});

const result = validateProfile(profile);
if (!result.valid) throw new Error(result.errors.join("\n"));

const portable = toPortableFingerprint(profile);
const roxyConfig = toRoxyConfig(profile, {
  hostOs: "linux",
  appPort: 45535
});
const runtimeConfig = toBrowserRuntime(profile, {
  hostOs: "linux",
  userDataDir: "/var/lib/my-project/profiles/user-001",
  virtualWorkArea: { originX: 0, originY: 0, width: 1920, height: 1080 }
});

const batch = generateProfiles({
  preset: "windows-firefox",
  seed: "batch-2026-07-25",
  count: 100
});

const authorizedProvider = createRoxyHttpBaseRecordProvider({
  baseUrl: process.env.ROXY_AUTHORIZED_API_BASE_URL,
  headers: JSON.parse(await readFile(process.env.ROXY_AUTHORIZED_HEADERS_FILE, "utf8"))
});
const authorizedProfiles = await generateProfilesWithProvider({
  provider: authorizedProvider,
  preset: "windows-11-chrome",
  seed: "authorized-batch",
  count: 10
});

// 将正式获得并审计过的响应保存后，可以完全离线重复使用。
const offlineProvider = createJsonBaseRecordProvider(savedAuthorizedRecords);
```

同一个 preset、version 和 seed 始终生成同一份结果；不同 seed 会改变机器名、MAC、屏幕选择、硬件参数、字体、语音顺序、Canvas、WebGL、Audio 和其他可选 API。

`toBrowserRuntime()` 是无文件写入的纯适配器：Chrome 分支返回受控启动参数、Preferences 和 Local State；Firefox 分支返回启动参数、`user.js`、`xulstore.json` 和主题状态。它不接受原程序的任意 `startupParam`，调用方只能使用白名单化的 GPU、沙箱、主题和窗口字段。

## 可选的正式授权云端基础记录

原 `Ln(...)` 在本地生成噪声前会并行申请：

- `/user_get_ua_webgl_v2`：UA、版本、UA metadata、WebGL 以及可能的分辨率/DPR。
- `/user_get_device_name_v2`：设备名。
- `/user_get_mac_addr_v2`：MAC；使用 `--no-cloud-mac` 时继续保留本地生成的 MAC。

`createRoxyHttpBaseRecordProvider()` 已恢复这三条请求的原参数：`os`、`osVersion`、`coreVersion`、`coreType` 和 `count`。响应必须是原版的 `{code: 0, data: [...]}` 结构，并通过字段、浏览器引擎和最终 Profile 校验才会应用。

Provider 只覆盖基础记录白名单，不会接收代理、Cookie、凭据、会员或同步数据。HTTP Provider 的结果标记为：

```json
{
  "baseDataSource": "authorized-provider",
  "provider": "roxy-authorized-http",
  "deterministic": false
}
```

远端每次可能返回不同记录，因此不是 seed 决定的。将获批响应保存并通过 `createJsonBaseRecordProvider()` 导入后，可以得到可重复、无网络的基础记录组合。

## 随机模式

- `random`：在保持模板内部一致的前提下，确定性随机启用 fonts、plugins、mediaDevices、battery、network 和 Bluetooth，并随机生成对应参数。
- `original-defaults`：使用原 OpenAPI 创建参数更接近的开关默认值，但所有噪声值仍由 seed 决定。

原程序的本地随机算法已经逐项恢复：

- Canvas：32 位大写十六进制；Chrome V2 为 `1000..99999`，Firefox 为 `[0,1)`。
- WebGL：Chrome 为 2 位数字加 14 位原字符表内容；Firefox 为 `[0,1)`。
- AudioContext：`[0,1)`。
- ClientRects X/Y：`[-1,1)`。
- 允许字体：从 1484 项池中无放回抽取 `100..499` 项。
- 禁用字体：按原来的 Windows 核心字体排除、已知字体交集及两次最多 5 项抽样合并。
- 语音：保留原来的可选删除 0/1 项、抽出 3 项并强制为本地语音、再合并剩余列表的顺序。

## 启动环境一致性

Profile 的 `runtime` 保存原程序的默认值：GPU 加速开启、`sandboxPermission=false`、跟随系统主题、窗口最大化，备用普通窗口尺寸为 `1280x720`。

- Chrome：恢复 `--disable-background-mode`、`--disable-popup-blocking`、`--no-first-run`、`--no-default-browser-check`、远程调试随机端口、keychain、沙箱、密码存储、后台窗口、GPU 和窗口 flags；macOS 宿主保留其专用分支。
- Chrome Preferences：恢复 `browser.theme.color_scheme/color_scheme2`；136 以前的内核恢复 `enable-force-dark@1` Local State 处理。
- Firefox：恢复 Marionette/远程调试/profile flags、`layers.acceleration.disabled`、`gfx.webrender.disabled`、`security.sandbox.content.level`、`prefers-color-scheme`、主题 ID 和 `xulstore.json` 窗口位置。
- 原代码的 `startupParam` 能替换或追加任意命令行参数。SDK 故意不暴露这个入口，只输出已审计的固定参数。

## JSON Schema

标准化 Profile schema 位于：

`/opt/automyai/fingerprint/sdk/schema/fingerprint-profile.schema.json`

可以在其他 Node、Python、Go 或 Rust 项目中按普通 JSON 使用。SDK 本身没有第三方运行时依赖。

## 边界与限制

- 不访问会员接口，不修改订阅或配额，不绕过授权。
- 不包含独立的 `lumi.conf` 加密器或原包静态加密参数。
- SDK 和本地 API 不包含桌面程序、Electron、浏览器内核、QEMU、roxynet、更新器或在线控制台。
- 生成结果是可供其他项目消费的 JSON；SDK 自身不会启动浏览器。
- 环境声明不能单独改变 TLS、内核实现、真实字体文件、GPU 驱动或操作系统行为；其他项目接入时应让这些底层信号与 Profile 保持一致。
- 默认模式不调用在线接口，使用 13 个本地一致性模板。只有显式使用 `generate-cloud` 或传入授权 Provider 时才申请原三类基础记录；原包内可离线恢复的字体、语音和噪声算法始终在本地生成。
