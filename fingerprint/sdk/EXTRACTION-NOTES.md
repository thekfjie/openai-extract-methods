# RoxyBrowser 3.9.2 指纹功能抽取记录

## 来源

- 来源安装包：RoxyBrowser x64 3.9.2（抽取完成后已从运行目录删除）
- 解包 Source Map：`/tmp/roxybrowser-audit/asar/dist/main.mjs.map`
- 主要可读源码：Source Map 的 `sourcesContent[76]`

## 原程序函数边界

| 原函数 | 作用 | 本模块对应实现 |
| --- | --- | --- |
| `xn(localFonts)` | 原 Windows 核心字体排除、已知字体交集、两次随机抽样与合并 | `original-algorithms.mjs:generateDisabledFonts()` |
| `An()` | 生成 32 位大写十六进制 Canvas 值 | `prng.hex(32)` |
| `Mn(coreType)` | Chrome 原字符表的 16 字符或 Firefox 浮点 WebGL noise | `generateWebglNoise()` |
| `Pn()` | 生成 `[-1, 1)` 的 ClientRects X/Y noise | `clientRects` 生成逻辑 |
| `In(os, coreType)` | 原 Chrome/Firefox 语音池、可选删除、3 项本地化重排 | `generateSpeechVoices()` |
| `Ln(options)` | 合并云端基础记录和本地噪声字段 | `generateProfile()` |
| `fa(winInfo, proxyInfo, dir)` | 转换 Chrome 最终配置 | `toChromeConfig()` |
| `pa(winInfo, proxyInfo, dir)` | 转换 Firefox 最终配置 | `toFirefoxConfig()` |
| `ma(...)` | 按 `coreType` 选择 Chrome/Firefox 分支 | `toRoxyConfig()` |
| `genChromeLaunchCLIArgs()` | Chrome 固定 flags、GPU、最大化/尺寸/位置和宿主 macOS 分支 | `toChromeRuntime()` |
| `genFirefoxLaunchCLIArgs()` | Firefox profile、Marionette、调试、最大化和 no-remote flags | `toFirefoxRuntime()` |
| `updateBrowserPreferences()` / `modifyLocalStateFile()` | Chrome 颜色主题和旧内核 force-dark | `toChromeRuntime()` |
| `prepareFirefoxProfile()` / `writeFirefoxWindowPosition()` | WebRender、沙箱、颜色主题、`user.js` 和 `xulstore.json` | `toFirefoxRuntime()` |

## 原始本地算法的数据范围

- `canvasValue`：32 位大写十六进制字符串。
- Chrome `canvasValueV2`：`1000..99999` 整数。
- Firefox `canvasValueV2`：`[0, 1)` 浮点值。
- Chrome `webGLValue`：2 位数字加 14 位大写字母/数字。
- Firefox `webGLValue`：`[0, 1)` 浮点值。
- `audioContextValue`：`[0, 1)` 浮点值。
- `clientRectsNoiseFactorX/Y`：`[-1, 1)` 浮点值。
- `allowFontList`：从 1484 项原始字体池中抽取 `100..499` 项。
- Chrome voices：原始池 19 项；Windows 额外加入 3 个 Microsoft 本地语音。
- Firefox voices：原始池 37 项。

完整静态数据由以下命令从 Source Map 可复现生成：

```bash
node scripts/extract-original-catalogs.mjs \
  /tmp/roxybrowser-audit/asar/dist/main.mjs.map \
  src/original-data.mjs
```

## 原程序依赖云端的字段

原 `Ln(...)` 会调用：

- `userGetUaWebglV2List`
- `userGetDeviceNameV2List`
- `userGetMacAddrV2List`

这些接口返回 UA、UA metadata、WebGL vendor/renderer、设备名和可选 MAC。默认生成器用经过一致性检查的本地模板填充；`createRoxyHttpBaseRecordProvider()` 则允许在调用方提供正式授权 API 基址和 headers 后按原参数申请。

恢复的 endpoint 和请求参数：

| endpoint | 参数 |
| --- | --- |
| `/user_get_ua_webgl_v2` | `os`、`osVersion`、`coreVersion`、`coreType`、`count` |
| `/user_get_device_name_v2` | `os`、`coreType`、`count` |
| `/user_get_mac_addr_v2` | `coreType`、`count` |

SDK 不包含原登录会话、token 获取、签名或会员逻辑。授权信息必须由调用方显式提供，并且不会写入生成结果。

## 最终配置的关键分支差异

| 项目 | Chrome | Firefox |
| --- | --- | --- |
| WebGL 对象名 | `WebGL` | `webGL` |
| Canvas V1 | `canvasContextNoiseValue` 字符串 | 不使用 |
| Canvas V2 | `canvasContextNoiseValueV2` 整数 | `canvasContextNoiseValue` 浮点数 |
| WebRTC | `webRtcMode` / `webRtcRemoteIp` | `webrtc.mode` / `loaclIP` / `remoteIP` |
| UA metadata | 使用 | 不使用 |
| 移动标志 | `userAgentMetadata.mobile` | `navigator.mobile` |

Firefox 原代码中的 `loaclIP` 拼写即为如此，本模块为结构兼容保留该拼写，但默认禁用 WebRTC，因此不会写入 IP。

## 启动层抽取边界

原程序启动层还允许用分号拆分的 `startupParam` 任意替换/追加浏览器参数。该能力不是指纹数据本身，也会形成未审计的命令行注入面，因此没有复制。运行时适配器只生成 Source Map 中已核对的固定参数，以及结构化的 GPU、沙箱、主题和窗口选项。

Chrome 原代码无论 `sandboxPermission` 值如何都会加入 `--no-sandbox` 与 `--disable-setuid-sandbox`；Firefox 则把该值映射为 `security.sandbox.content.level=0/-1`。SDK 保留这一原始行为，并通过 `originalSemantics` 明示，避免把字段名误解为浏览器实际开启了沙箱。

## 未抽取或未恢复的内容

- 登录、会员、订阅、配额和支付逻辑。
- 任何云端令牌、接口签名或授权绕过。
- 原程序静态加密密钥和加密 `lumi.conf` 写入格式。
- 代理账号、Cookie、同步数据和远程更新功能。

这些内容不属于本地环境模拟，也不会被生成器访问。
