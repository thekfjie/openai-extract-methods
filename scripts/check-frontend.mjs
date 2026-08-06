#!/usr/bin/env node
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { extractEyJTokens } from '../frontend/src/utils/extractEyJ.js';
import {
  MAX_TEST_PROFILE_BATCH,
  TEST_PROFILE_COUNTRY_REGISTRY,
  generateTestProfiles,
  testProfilesToCsv,
} from '../frontend/src/utils/testProfileGenerator.js';
import {
  REMOTE_ADDRESS_COUNTRIES,
  remoteAddressFields,
} from '../frontend/src/utils/addressProfileSource.js';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const frontend = path.join(root, 'frontend');
const contractPath = path.join(frontend, 'docs', 'endpoints.json');
const contract = JSON.parse(fs.readFileSync(contractPath, 'utf8'));

const staticPages = fs.readdirSync(path.join(frontend, 'pages')).filter((name) => name.endsWith('.html')).map((name) => `pages/${name}`);
const currentPages = ['index.html', ...staticPages];

const failures = [];
for (const relative of staticPages) {
  const source = fs.readFileSync(path.join(frontend, relative), 'utf8');
  if (!source.includes('runtime-config.js')) failures.push(`${relative}: runtime-config.js is missing`);
  if (!source.includes('api-client.js')) failures.push(`${relative}: api-client.js is missing`);
  if (/(?:src|href)=["']\/ui\/(?:js|css)\//.test(source)) {
    failures.push(`${relative}: frontend asset is coupled to /ui`);
  }
}

const reactIndex = fs.readFileSync(path.join(frontend, 'index.html'), 'utf8');
if (!reactIndex.includes('/ui/js/runtime-config.js')) failures.push('index.html: runtime-config.js is missing');
if (!reactIndex.includes('/src/main.jsx')) failures.push('index.html: React entry /src/main.jsx is missing');
if (!fs.existsSync(path.join(frontend, 'dist', 'index.html'))) failures.push('dist/index.html is missing; run npm run build');

for (const relative of ['auth/login.html', 'legacy/control-panel.html']) {
  const source = fs.readFileSync(path.join(frontend, relative), 'utf8');
  if (!source.includes('runtime-config.js') || !source.includes('api-client.js')) {
    failures.push(`${relative}: shared runtime/API client is missing`);
  }
}

for (const relative of Object.keys(contract.pages || {})) {
  if (!fs.existsSync(path.join(frontend, relative))) failures.push(`contract page does not exist: ${relative}`);
}

const appSources = currentPages
  .map((relative) => fs.readFileSync(path.join(frontend, relative), 'utf8'))
  .concat([
    fs.readFileSync(path.join(frontend, 'js', 'app.js'), 'utf8'),
    fs.readFileSync(path.join(frontend, 'js', 'settings.js'), 'utf8'),
  ])
  .join('\n');

function walkSources(dir) {
  const result = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const target = path.join(dir, entry.name);
    if (entry.isDirectory()) result.push(...walkSources(target));
    else if (/\.(?:js|jsx)$/.test(entry.name)) result.push(target);
  }
  return result;
}

const reactSources = walkSources(path.join(frontend, 'src')).map((file) => fs.readFileSync(file, 'utf8')).join('\n');
for (const forbidden of [/fetch\(["']\/api\//, /fetch\(["']\/openai[234]\/api\//]) {
  if (forbidden.test(reactSources)) failures.push(`React source bypasses apiClient: ${forbidden}`);
}

const layoutSource = fs.readFileSync(path.join(frontend, 'src/components/layout/Layout.jsx'), 'utf8');
const sidebarSource = fs.readFileSync(path.join(frontend, 'src/components/layout/Sidebar.jsx'), 'utf8');
const navigationSource = fs.readFileSync(path.join(frontend, 'src/components/layout/navigation.js'), 'utf8');
const convertersSource = fs.readFileSync(path.join(frontend, 'src/pages/Converters.jsx'), 'utf8');
const extractionSource = fs.readFileSync(path.join(frontend, 'src/pages/ExtractionCenter.jsx'), 'utf8');
const extractionApiSource = fs.readFileSync(path.join(frontend, 'src/api/extraction.js'), 'utf8');
const remoteAddressSource = fs.readFileSync(path.join(frontend, 'src/components/tools/RemoteAddressProfiles.jsx'), 'utf8');
const customSelectSource = fs.readFileSync(path.join(frontend, 'src/ui/CustomSelect.jsx'), 'utf8');
const appCss = fs.readFileSync(path.join(frontend, 'src/index.css'), 'utf8');
const themeTokens = fs.readFileSync(path.join(frontend, 'src/ui/tokens.css'), 'utf8');
const legacyCss = fs.readFileSync(path.join(frontend, 'css/nature.css'), 'utf8');
const legacyControlPanel = fs.readFileSync(path.join(frontend, 'legacy/control-panel.html'), 'utf8');
const sidebarDrivenPages = ['OpenAIAutomation.jsx', 'GrokAutomation.jsx', 'Infrastructure.jsx', 'Tools.jsx', 'Settings.jsx', 'Converters.jsx', 'Payments.jsx']
  .map((name) => fs.readFileSync(path.join(frontend, 'src/pages', name), 'utf8'))
  .join('\n');

if (!sidebarSource.includes('NAV_ITEMS.map')) failures.push('Sidebar must render the ordered navigation schema');
if (sidebarSource.includes('GROUPS.slice')) failures.push('Sidebar navigation must not be assembled with GROUPS.slice');
for (const sub of ['convert', 'token', 'promo']) {
  if (!navigationSource.includes(`sub: '${sub}'`)) failures.push(`Converters sidebar submodule is missing: ${sub}`);
}
if (!navigationSource.includes("{ sub: 'token', label: '提取 eyJ' }")) failures.push('eyJ extractor must remain visible in converter navigation');
if (!navigationSource.includes("{ sub: 'test_profiles', label: '多国测试资料' }")) failures.push('local multi-country test profiles must remain visible in tools navigation');
if (!navigationSource.includes("{ sub: 'extract', label: '提炼中心', to: '/payments/extract' }") || !navigationSource.includes("{ sub: 'center', label: '支付中心', to: '/payments/center' }")) failures.push('Payment navigation must separate extraction and protocol payment centers');
if (!remoteAddressSource.includes("apiClient.post('/address-profiles/random'")) failures.push('remote address profile tool must use the authenticated local proxy');
if (!remoteAddressSource.includes('automyai.remote-address-profile.v1')) failures.push('current remote address profile must persist across page refreshes');
if (!remoteAddressSource.includes('CollapsiblePanel') || !remoteAddressSource.includes('就业与公司资料') || !remoteAddressSource.includes('其他身份、设备与附加资料')) failures.push('non-priority remote profile fields must remain collapsible');
if (!convertersSource.includes("useNavigationSub('/converters')")) failures.push('Converters must use canonical URL submodule navigation');
if (convertersSource.includes('converter-tabs')) failures.push('Converters must not duplicate sidebar navigation with page tabs');
if (!extractionSource.includes('proxyByMethod') || !extractionSource.includes('[methodID]: activeProxy')) failures.push('Extraction proxies must be remembered independently by method');
if (!extractionSource.includes('WORKBENCH_STORAGE_KEY') || !extractionSource.includes('PROXY_STORAGE_KEY') || !extractionSource.includes("writeStoredJSON('localStorage', PROXY_STORAGE_KEY")) failures.push('Extraction method, form and per-method proxies must survive browser restarts');
if (!extractionSource.includes("countryMode: 'single'") || !extractionSource.includes('countryPool') || !extractionSource.includes("assignmentStrategy: 'random_balanced'") || !extractionSource.includes('随机账单地区池') || !extractionSource.includes('同一批次内绑定不变')) failures.push('PayPal extraction must support a persisted random balanced billing-country pool with per-batch account bindings');
if (!extractionSource.includes("const defaultPayPalRandomCountries = ['DE', 'GB', 'US', 'TH', 'BR']") || !extractionSource.includes('>全球预设</button>')) failures.push('PayPal global random preset must remain DE / GB / US / TH / BR');
if (!extractionSource.includes('代理出口仍以下方你选择的代理为准')) failures.push('PayPal random billing countries must remain explicitly separate from proxy exit regions');
if (!extractionSource.includes('CREDENTIAL_SESSION_KEY') || !extractionSource.includes("readStoredText('sessionStorage', CREDENTIAL_SESSION_KEY)")) failures.push('Extraction credentials must survive refresh only within the current tab session');
if (!extractionSource.includes('JOB_DRAFTS_SESSION_KEY') || !extractionSource.includes('setJobDrafts') || !extractionSource.includes('sessionDraft?.input')) failures.push('Extraction history must restore each batch form from a tab-session snapshot');
if (!extractionSource.includes('JOB_CONFIG_STORAGE_KEY') || !extractionSource.includes('persistentJobConfig') || !extractionSource.includes("writeStoredJSON('localStorage', JOB_CONFIG_STORAGE_KEY") || !extractionSource.includes('代理、模式、并发、次数等配置按渠道和批次保存在本浏览器 localStorage')) failures.push('Extraction history must persist per-job proxy/config snapshots in browser localStorage');
if (extractionSource.includes('该批次创建时未保存浏览器表单快照，凭证和完整代理无法还原')) failures.push('Extraction history must not fall back to the obsolete non-restorable snapshot warning');
if (!extractionSource.includes('continueKakaoProvider') || !extractionSource.includes('整批转支付链（25 次起）') || !extractionSource.includes('每账号最多 ${providerSettings.maxAttempts} 次完整链路') || !extractionSource.includes("continuation?.status !== 'requested'") || !extractionSource.includes('const pendingContinuation = history.find') || !extractionSource.includes('detailedJob.continuation?.maxAttempts')) failures.push('Completed Kakao eligibility batches must prioritize and honor requested full-batch provider continuation from the tab snapshot');
if (!extractionApiSource.includes('requestKakaoProviderContinuation') || !extractionApiSource.includes('markKakaoProviderContinuationSubmitted')) failures.push('Extraction client must support persisted Kakao continuation requests');
if (!/\.extraction-proxy-grid\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)/s.test(appCss)) failures.push('Extraction main and promotion proxies must be stacked vertically');
if (/Promotion 代理；刷新后仍保留在当前浏览器会话/.test(extractionSource)) failures.push('Extraction proxy fields must not show per-channel persistence helper text');
if (!extractionSource.includes('账号并发数') || !extractionSource.includes('每账号最多尝试次数') || !extractionSource.includes('包含首次执行')) failures.push('Extraction concurrency and attempt-count labels must state their distinct semantics');
if (!extractionSource.includes('kakaoModeOptions') || !extractionSource.includes("value: 'eligibility'") || !extractionSource.includes("value: 'provider_link'") || !extractionSource.includes('supportsConcurrency: true')) failures.push('Kakao UI must expose both modes with configurable batch concurrency');
if (!extractionSource.includes('Kakao 可配置批量流程') || !extractionSource.includes('只接受上游真实展示的 kakao_pay') || !extractionSource.includes('成功出链即停止') || !extractionSource.includes('每账号最多尝试次数')) failures.push('Kakao UI must explain configurable batch/attempt behavior and the upstream-only stopping boundary');
if (!extractionSource.includes('显式优惠代理（原样使用）') || !extractionSource.includes('不改地区、不替换 SID') || !extractionSource.includes('默认 VN') || !extractionSource.includes('重跑完整支付链')) failures.push('Kakao provider-link UI must explain exact explicit proxies, VN fallback, and full-chain retries');
if (!/kakaoMode === 'provider_link' \? 'VN' : 'TR'/.test(extractionSource) || !/promotionProxyRegion:[\s\S]{0,260}targetSettings\.kakaoMode === 'provider_link'[\s\S]{0,180}: 'TR'/.test(extractionSource)) failures.push('Kakao eligibility must keep TR while provider-link fallback defaults to VN');
if (!extractionSource.includes('原始默认 25 次') || !extractionSource.includes("max={methodID === 'kakao' ? 100 : 10}")) failures.push('Kakao provider-link attempt budget must restore the original multi-round range');
if (extractionSource.includes('inputRows.length !== 1') || extractionSource.includes('资格观察固定 1 次') || extractionSource.includes('不会自动批量轮账号')) failures.push('Kakao UI must not reintroduce forced single-account or one-shot limits');
if (extractionSource.includes('Math.min(kakaoEligibleAccounts.length, 3)') || extractionSource.includes(' / 3</strong>')) failures.push('Kakao self-service UI must not keep the obsolete 3/3 target');
if (!extractionSource.includes('saveKakaoRunReport') || !extractionSource.includes('kakao-self-service-results.txt') || !extractionSource.includes('不写入凭证/代理') || !extractionSource.includes("window.location.assign('/ui/file-library')")) failures.push('Kakao self-service records and guide must be reachable through the file library');
if (!extractionSource.includes('UPI 支付材料') || !extractionSource.includes('qrPngUrl') || !extractionSource.includes('upiPayload') || !extractionSource.includes('QRCode.toDataURL')) failures.push('UPI result details must expose QR material and render payload-only QR codes');
if (!extractionSource.includes('完整执行时间线') || !extractionSource.includes('buildTimeline')) failures.push('Extraction results must present the complete ordered account flow');
if (!extractionSource.includes('deleteHistoryJob') || !extractionApiSource.includes('deleteJob(jobID')) failures.push('Extraction history must support deleting terminal batches');
if (!extractionSource.includes("useState('success')") || !extractionSource.includes("['all', '全部'") || !extractionSource.includes('visibleHistoryJobs')) failures.push('Extraction history must load all batches and default to the successful-batch filter');
if (!extractionApiSource.includes('Number.isFinite(limit)') || !extractionApiSource.includes('`${JOBS_PATH}${query}`')) failures.push('Extraction history API must omit the limit query when loading the complete history');
if (!extractionApiSource.includes('icon-pm-upi') || !extractionApiSource.includes("checkout.stripe.com")) failures.push('Extraction client must reject static UPI icons and generic Stripe Checkout URLs');
if (layoutSource.includes('key={location.pathname}')) failures.push('Layout must not remount route content by pathname');
if (layoutSource.includes('automyai-sidebar-expanded')) failures.push('Sidebar state must not be restored from stale localStorage');
if (/\.route-stage\s*\{[^}]*animation/s.test(appCss)) failures.push('route-stage must remain visible during sidebar navigation');
if (/engine-view view-transition|key=\{activeTab\}|key=\{activeTool\}/.test(sidebarDrivenPages)) {
  failures.push('Sidebar-driven submodules must not remount or replay enter animations');
}
if (!appCss.includes('--sidebar-motion: 300ms cubic-bezier(.16, 1, .3, 1)')) failures.push('MathModels sidebar motion curve is missing');
if (!/\.sidebar\.collapsed:not\(\.mobile-open\)\s*\{[^}]*width:\s*84px/s.test(appCss)) failures.push('Collapsed sidebar width must remain 84px');
if (!/\.sidebar\s*\{[^}]*flex:\s*0 0 280px[^}]*transition:[^}]*flex-basis/s.test(appCss)) failures.push('Desktop sidebar must animate flex-basis so content reflows smoothly');
if (/\.nav-item:hover\s*\{[^}]*transform/s.test(appCss)) failures.push('Sidebar hover must not move navigation items');
if (!/select option,\s*select optgroup\s*\{[^}]*background-color:\s*var\(--select-option-bg\)[^}]*color:\s*var\(--select-option-text\)/s.test(appCss)) {
  failures.push('React native select popup colors are missing');
}
if ((themeTokens.match(/--select-option-bg:/g) || []).length < 5 || (themeTokens.match(/--control-color-scheme:/g) || []).length < 5) {
  failures.push('Every React theme must define opaque native select popup colors');
}
if (!legacyCss.includes('select option,') || !legacyCss.includes('--select-option-bg:')) {
  failures.push('Legacy nature UI native select popup colors are missing');
}
if (!legacyControlPanel.includes('select option,') || !legacyControlPanel.includes('--select-option-bg:')) {
  failures.push('Legacy control panel native select popup colors are missing');
}
if (/<select\b/.test(reactSources)) failures.push('React console must use CustomSelect instead of native select controls');
if (!customSelectSource.includes('createPortal') || !customSelectSource.includes('custom-select-menu')) {
  failures.push('CustomSelect must render its popup through a portal');
}
if (!/\.custom-select-menu\s*\{[^}]*position:\s*fixed[^}]*z-index:\s*3200/s.test(appCss)) {
  failures.push('CustomSelect popup must use a fixed, top-level stacking layer');
}
if (!/\.custom-select\.open \.custom-select-chevron\s*\{[^}]*transform:\s*rotate\(180deg\)/s.test(appCss)) {
  failures.push('CustomSelect open state must animate the chevron rotation');
}

const eyjOne = 'eyJheader.payload.signature';
const eyjTwo = 'eyJsecond.payload.signature';
const eyjNested = extractEyJTokens(JSON.stringify({ tokens: { access_token: eyjOne }, ignored: { id_token: eyjTwo } }));
if (eyjNested.length !== 1 || eyjNested[0] !== eyjOne) failures.push('eyJ extractor must prefer nested access_token fields');
const eyjMany = extractEyJTokens(`Bearer ${eyjOne}\nlog token=${eyjTwo}\n${eyjOne}`);
if (eyjMany.length !== 2 || eyjMany[0] !== eyjOne || eyjMany[1] !== eyjTwo) failures.push('eyJ extractor must support logs and deduplicate tokens');

for (const country of ['JP', 'BR', 'US', 'GB', 'TR']) {
  if (!TEST_PROFILE_COUNTRY_REGISTRY[country]) {
    failures.push(`test profile country is missing: ${country}`);
    continue;
  }
  const first = generateTestProfiles({ country, count: 2, seed: 'frontend-check' });
  const repeated = generateTestProfiles({ country, count: 2, seed: 'frontend-check' });
  if (JSON.stringify(first) !== JSON.stringify(repeated)) failures.push(`test profile generation must be deterministic: ${country}`);
  if (first.length !== 2 || first.some((profile) => !profile.testOnly || !profile.synthetic)) failures.push(`test profile safety flags are missing: ${country}`);
  if (first.some((profile) => !profile.email.endsWith('.example.test'))) failures.push(`test profile email must use a reserved test domain: ${country}`);
  const approvedSandboxCards = new Set(['3530111333300000', '4111111111111111', '5555555555554444', '4012000033330620', '4444333322221111', '5555444433331111', '2223000048400011']);
  if (first.some((profile) => !approvedSandboxCards.has(profile.cardNumber.replace(/\D/g, '')) || !profile.cardLuhnValid || profile.safety.cardFormatValid !== true || profile.safety.sandboxPaymentMethod !== true || profile.safety.paymentInstrumentValid !== false)) failures.push(`test profile cards must stay inside the approved non-live sandbox catalog: ${country}`);
  if (first.some((profile) => profile.safety.nationalIdValid !== false || profile.safety.phoneCallable !== false)) failures.push(`test profile identity and phone placeholders must remain non-live: ${country}`);
}
if (generateTestProfiles({ country: 'JP', count: MAX_TEST_PROFILE_BATCH + 50, seed: 'bounded' }).length !== MAX_TEST_PROFILE_BATCH) failures.push('test profile batch limit must be enforced');
const testProfileCsv = testProfilesToCsv(generateTestProfiles({ country: 'BR', count: 1, seed: 'csv' }));
if (!testProfileCsv.startsWith('\ufeff') || !testProfileCsv.includes('card_sandbox_number')) failures.push('test profile CSV must include a UTF-8 BOM and sandbox card fields');

const remoteCountryCodes = new Set(REMOTE_ADDRESS_COUNTRIES.map((item) => item.code));
for (const country of ['JP', 'BR', 'US', 'GB', 'TR']) {
  if (!remoteCountryCodes.has(country)) failures.push(`remote address country is missing: ${country}`);
}
const remotePriority = remoteAddressFields({ fields: {
  Occupation: 'Tester', Birthday: '2000-01-01', Address: 'Example Street',
  Full_Name: 'Test Person', Temporary_mail: 'test@example.invalid', Zip_Code: '10000',
} });
if (remotePriority[0]?.key !== 'Temporary_mail' || remotePriority[1]?.key !== 'Full_Name') failures.push('remote address priority fields must follow the target profile panel order');

const routeInventory = JSON.parse(fs.readFileSync(path.join(frontend, 'docs', 'backend-routes.json'), 'utf8')).routes;
const mainCall = /apiClient\.(get|post|put|delete|blob)\(\s*(["'`])([^"'`$?]+)(?:[^"'`]*)\2/g;
let match;
while ((match = mainCall.exec(reactSources))) {
  const method = match[1] === 'blob' ? 'GET' : match[1].toUpperCase();
  const endpoint = match[3];
  const fullPath = `/api${endpoint.startsWith('/') ? '' : '/'}${endpoint}`;
  const exists = routeInventory.some((route) => {
    if (!route.methods.includes('ANY') && !route.methods.includes(method)) return false;
    if ((route.paths || []).includes(fullPath)) return true;
    return route.prefix && fullPath.startsWith(route.prefix) && (!route.suffix || fullPath.endsWith(route.suffix));
  });
  if (!exists) failures.push(`React endpoint is not served: ${method} ${fullPath}`);
}
for (const forbidden of [
  /fetch\(["']\/api\//,
  /fetch\(["']\/openai2\/api\//,
  /fetch\(["']\/openai3\/api\//,
  /fetch\(["']\/openai4\/api\//,
  /const\s+API\s*=\s*["']\/openai3\/api/,
]) {
  if (forbidden.test(appSources)) failures.push(`direct backend fetch remains: ${forbidden}`);
}

if (failures.length) {
  console.error(failures.join('\n'));
  process.exit(1);
}
console.log(`Frontend contract OK: ${currentPages.length + 2} UI documents, ${Object.keys(contract.pages).length} page contracts`);
