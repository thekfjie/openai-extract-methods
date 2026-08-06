# 指纹字段完整覆盖表

本表以 RoxyBrowser 3.9.2 的 `Ln(...)`、`fa(...)` 和 `pa(...)` 为基准。

## 本地随机算法

| 原字段/函数 | 标准化字段 | 随机规则 |
| --- | --- | --- |
| `An()` / `canvasValue` | `canvas.value` | 32 位大写十六进制 |
| `canvasValueV2` | `canvas.valueV2` | Chrome `1000..99999`；Firefox `[0,1)` |
| `Mn()` / `webGLValue` | `graphics.noise.value` | Chrome 16 字符；Firefox `[0,1)` |
| `audioContextValue` | `audioContext.value` | `[0,1)` |
| `Pn()` | `clientRects.noiseFactorX/Y` | `[-1,1)` |
| `xn(localFonts)` | `fonts.disabled` | 原交集、差集、两次最多 5 项抽样和去重合并 |
| `jn()` | `fonts.allowed` | 1484 项字体池中抽取 `100..499` 项 |
| `In(os, coreType)` | `speechSynthesis.voices` | 原 Chrome/Firefox voice 池和顺序算法 |

## 身份与 Navigator

| 原输入 | Chrome 输出 | Firefox 输出 | 标准化字段 |
| --- | --- | --- | --- |
| `computerName` | `computerName` | 无 | `machine.computerName` |
| `macAddress` | `macAddress` | 无 | `machine.macAddress` |
| `userAgentNew` | `userAgent` | `userAgent` | `engine.userAgent` |
| `userAgentVersion` | `chromeVersion` | UA 内版本 | `engine.version` |
| `navigatorPlatform` | `navigator.platform` | `navigator.platform` | `navigator.platform` |
| `hardwareConcurrent` | `navigator.hardwareConcurrency` | 同 | `navigator.hardwareConcurrency` |
| `deviceMemory` | `navigator.deviceMemory` | 同 | `navigator.deviceMemory` |
| OS 移动判断 | `navigator.maxTouchPoints` | `maxTouchPoints`、`mobile` | `navigator.maxTouchPoints/mobile` |
| `enablePlugin` | 5 个 PDF plugins 或空数组 | 不输出 | `navigator.pluginsEnabled/plugins` |
| `doNotTrack` | `doNotTrack` | `doNotTrack` | `navigator.doNotTrack`，保持原 boolean 类型 |

`generator.baseDataSource` 标记基础数据来源：默认 `local-template`；通过正式授权 Provider 获取时为 `authorized-provider`。Provider 只允许覆盖 UA、版本、UA metadata、Navigator platform、WebGL/WebGPU、设备名、MAC、分辨率和 DPR。

## UA Client Hints

Chrome 输出 `userAgentMetadata.platform`、`mobile` 和 `platformVersion`。

- `architecture` 仅在模拟 macOS 且 WebGL 信息开启时输出。
- `model` 仅在 Android 模板中输出。
- Firefox 不输出 Chrome UA metadata。

## Locale、时区和位置

| 原字段 | 最终字段 | 标准化字段 |
| --- | --- | --- |
| `language` / IP 映射 | `appLocale` | `locale.appLocale` |
| `displayLanguage` / IP 映射 | `acceptLang` | `locale.acceptLanguage` |
| `timeZone` / IP 时区 | `timeZone` | `locale.timezone` |
| `position` | Chrome `prompt/allow/block`；Firefox `prompt/allow/disable` | `geolocation.mode` |
| 经纬度/精度 | `geoLocation.location*` | `geolocation.latitude/longitude/accuracy/altitude` |

模板把 locale、Accept-Language 和 timezone 作为一个整体选择，避免彼此冲突。默认位置阻止，避免在没有出口 IP 上下文时生成矛盾坐标。

## 图形、声音和布局

| 原字段 | Chrome | Firefox | 标准化字段 |
| --- | --- | --- | --- |
| `canvas` / values | `canvasContext` V1+V2 | `canvasContext` V2 | `canvas` |
| `webGLInfo` | vendor/renderer 可清空 | 同 | `graphics.webglInfoEnabled` |
| `webGL` / noise | `WebGL` | `webGL` | `graphics` |
| `webGpu` / vendor | `WebGPU` | 不输出 | `graphics.webgpu` |
| `audioContext` | `audioBuffer` version 2、interval 100 | `audioBuffer` | `audioContext` |
| `clientRects` | `clientRects` | `clientRects` | `clientRects` |

## Screen 的宿主分支

原程序会比较运行 RoxyBrowser 的宿主 OS 与被模拟 OS：

- 相同时 `pixelDepth/colorDepth` 输出 `0`。
- Chrome 相同时省略 DPR；Firefox 相同时强制 DPR 为 `1`。
- Chrome 桌面环境的 `availHeight = height - 38`，移动环境不减。
- Firefox 的 `availHeight = height`。

`toRoxyConfig(profile, { hostOs })` 完整保留该行为；标准化 Profile 始终保留目标环境的正常色深和 DPR。

## Fonts、Speech 和 Media

| 功能 | Chrome | Firefox | 标准化字段 |
| --- | --- | --- | --- |
| 禁用字体 | `disableFontListV2` | 不输出 | `fonts.disabled` |
| 允许字体 | `allowFontList` | `allowFontList` | `fonts.allowed` |
| 语音 | `speechSynthesis` | `speechSynthesis` | `speechSynthesis` |
| mediaDevices | `Ln` 会生成但 `fa/pa` 未写入最终配置 | 同 | `mediaDevices`，保留给其他项目适配 |

## WebRTC

| 标准模式 | Chrome | Firefox |
| --- | --- | --- |
| `altered` | `webRtcMode=altered`，可输出 `webRtcRemoteIp` | `webrtc.mode=altered`，保留原拼写 `loaclIP` 和 `remoteIP` |
| `real` | `webRtcMode=real` | `webrtc.mode=real` |
| `disable` | `webRtcMode=disableOk` | `webrtc.mode=disable` |

生成器不会凭空伪造出口 IP；`altered` 模式必须由调用方提供 `remoteIp`。

## Chrome 扩展设备 API

| 原字段 | Chrome 最终字段 | 标准化字段 |
| --- | --- | --- |
| `openBattery` 等 | `battery.enable/charging/chargingTime/dischargingTime/level` | `battery` |
| `openNetwork` 等 | `network.nettype/effectiveType/downlink/downlinkMax/rtt/saveData` | `network` |
| `openBluetooth` | `bluetooth.enable/bluetoothAdapter` | `bluetooth` |

Firefox 3.9.2 的配置转换函数没有输出这三组字段，但标准化 Profile 仍保留，便于其他项目使用。

## 内容和安全行为

| Chrome/Firefox 最终字段 | 标准化字段 |
| --- | --- |
| `blockImages`、`imageSizeLimit` | `content.blockImages/imageSizeLimit` |
| `disablePlayVideo`、`disablePlaySound` | `content.disablePlayVideo/disablePlaySound` |
| `disablePasswordSaveTips` | `content.disablePasswordSaveTips` |
| `ignoreCertificateErrors` | `security.ignoreCertificateErrors` |
| `ssl.cipherSuiteBlacklist` | `security.sslCipherSuiteBlacklist` |
| `portScan.*` | `security.portScanProtection/portScanAllowList` |

原 UI 输入字段使用反向转换：`blockImages=!forbidImage`、`disablePlayVideo=!forbidMedia`、`disablePlaySound=!forbidAudio`、`disablePasswordSaveTips=!forbidSavePassword`。SDK 的 `content` 保存转换后的最终语义；`original-defaults` 对应的五项默认值均为 `false`。Firefox 没有 `blockImages` 字段，原分支在不限制图片时输出 `imageSizeLimit=-1`，限制时输出指定大小。

本地 Roxy 注入白名单故意不允许 JSON 覆盖 SSL、端口扫描、代理或凭据；这些字段仍存在于 SDK 和纯 `toRoxyConfig` 导出中，供自有项目使用。

## 浏览器启动与 Profile 文件

| 原行为 | Chrome runtime | Firefox runtime | 标准化字段 |
| --- | --- | --- | --- |
| `useGpu` | 关闭时 `--disable-gpu` | `layers.acceleration.disabled`、`gfx.webrender.disabled` | `runtime.gpuAcceleration` |
| `sandboxPermission` | 原代码始终带 `--no-sandbox` 和 `--disable-setuid-sandbox` | `security.sandbox.content.level` 为 `0/-1` | `runtime.sandboxPermission` |
| `browserColorScheme` | Preferences 的 `color_scheme/color_scheme2`；旧内核 Local State force-dark | CSS override、系统暗色值、主题 ID | `runtime.colorScheme` |
| `positionSwitch/openWidth/openHeight/windowRatioPosition` | `--start-maximized` 或 size/position flags | `-maximized` 或 `xulstore.json` | `runtime.window` |

`toChromeRuntime()` 和 `toFirefoxRuntime()` 还恢复了原固定启动参数、Firefox `user.js`、三个受管主题的启用状态、主题缓存和旧内核深色实验。原 `startupParam` 属于任意参数注入通道，SDK 不接受或透传。

## 不属于指纹生成器的字段

以下字段出现在完整浏览器配置中，但属于业务、数据同步或 UI，而不是环境指纹，因此没有放入可复用指纹 SDK：

- `fproxy`、代理用户名和密码、代理 bypass。
- 自动填充账号、Cookie、默认打开 URL。
- allow/block domain、网页拦截页面。
- SessionStorage/IndexedDB/LocalStorage/Cookie/历史/书签/扩展同步。
- Google 登录开关、任务栏图标、窗口名称和排序号。
- 人类操作 simulation、虚拟摄像头上传 UI。
- 登录、会员、工作区、订阅、配额和支付字段。

授权 Provider 同样不会接收或透传以上字段；HTTP headers 只用于发起调用，不进入 Profile、Roxy 配置、runtime 配置或样本文件。
