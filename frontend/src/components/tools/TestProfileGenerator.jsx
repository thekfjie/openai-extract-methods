import React, { useMemo, useState } from 'react';
import {
  Copy,
  Dice5,
  Download,
  FileJson2,
  RefreshCw,
  ShieldCheck,
  Table2,
} from 'lucide-react';
import GlassPanel from '../../ui/GlassPanel';
import GlassButton from '../../ui/GlassButton';
import { CompactNumberInput } from '../../ui/ConsolePrimitives';
import { useToast } from '../../contexts/ToastContext';
import RemoteAddressProfiles from './RemoteAddressProfiles';
import {
  MAX_TEST_PROFILE_BATCH,
  TEST_PROFILE_COUNTRIES,
  TEST_PROFILE_COUNTRY_REGISTRY,
  createTestProfileSeed,
  formatTestProfile,
  generateTestProfiles,
  getTestProfileFields,
  testProfilesToCsv,
} from '../../utils/testProfileGenerator';

const STORAGE_KEY = 'automyai.test-profiles.preferences.v1';

function readPreferences() {
  try {
    const value = JSON.parse(window.localStorage.getItem(STORAGE_KEY) || '{}');
    const country = TEST_PROFILE_COUNTRY_REGISTRY[value.country] ? value.country : 'JP';
    const count = Math.max(1, Math.min(MAX_TEST_PROFILE_BATCH, Math.trunc(Number(value.count) || 1)));
    const seed = String(value.seed || '').trim() || createTestProfileSeed();
    return { country, count, seed };
  } catch (_) {
    return { country: 'JP', count: 1, seed: createTestProfileSeed() };
  }
}

function savePreferences(value) {
  try { window.localStorage.setItem(STORAGE_KEY, JSON.stringify(value)); } catch (_) {}
}

async function copyText(text) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(String(text || ''));
    return;
  }
  const textarea = document.createElement('textarea');
  textarea.value = String(text || '');
  textarea.style.position = 'fixed';
  textarea.style.opacity = '0';
  document.body.appendChild(textarea);
  textarea.select();
  document.execCommand('copy');
  textarea.remove();
}

function downloadText(text, filename, type) {
  const blob = new Blob([text], { type });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

function safeFilePart(value) {
  return String(value || 'local').replace(/[^a-z0-9_-]+/gi, '-').replace(/^-|-$/g, '').slice(0, 48) || 'local';
}

export default function TestProfileGenerator() {
  const { notify } = useToast();
  const [preferences, setPreferences] = useState(readPreferences);
  const [profiles, setProfiles] = useState(() => generateTestProfiles(preferences));
  const [selectedIndex, setSelectedIndex] = useState(0);

  const selected = profiles[selectedIndex] || profiles[0] || null;
  const selectedFields = useMemo(() => selected ? getTestProfileFields(selected) : [], [selected]);
  const countryDefinition = TEST_PROFILE_COUNTRY_REGISTRY[preferences.country] || TEST_PROFILE_COUNTRY_REGISTRY.JP;

  const updatePreferences = (patch) => {
    setPreferences((current) => {
      const next = { ...current, ...patch };
      savePreferences(next);
      return next;
    });
  };

  const generate = (overrides = {}) => {
    const next = { ...preferences, ...overrides };
    if (!String(next.seed || '').trim()) next.seed = createTestProfileSeed();
    next.count = Math.max(1, Math.min(MAX_TEST_PROFILE_BATCH, Math.trunc(Number(next.count) || 1)));
    savePreferences(next);
    setPreferences(next);
    setProfiles(generateTestProfiles(next));
    setSelectedIndex(0);
    notify(`已在浏览器本地生成 ${next.count} 条 ${TEST_PROFILE_COUNTRY_REGISTRY[next.country].label}测试资料`, 'success');
  };

  const chooseCountry = (country) => {
    if (country === preferences.country) return;
    generate({ country });
  };

  const useNewSeed = () => generate({ seed: createTestProfileSeed() });

  const copy = async (value, label) => {
    try {
      await copyText(value);
      notify(`${label}已复制`, 'success');
    } catch (error) {
      notify(error?.message || '浏览器拒绝了剪贴板访问', 'error');
    }
  };

  const copyBatch = () => {
    const text = profiles.map((profile) => `${profile.profileId}\n${formatTestProfile(profile)}`).join('\n\n---\n\n');
    copy(text, `${profiles.length} 条资料`);
  };

  const exportJson = () => {
    const payload = {
      schema: 'automyai.test-profile-batch.v2',
      synthetic: true,
      testOnly: true,
      country: preferences.country,
      seed: preferences.seed,
      count: profiles.length,
      profiles,
    };
    downloadText(`${JSON.stringify(payload, null, 2)}\n`, `test-profiles-${preferences.country.toLowerCase()}-${safeFilePart(preferences.seed)}.json`, 'application/json;charset=utf-8');
  };

  const exportCsv = () => {
    downloadText(testProfilesToCsv(profiles), `test-profiles-${preferences.country.toLowerCase()}-${safeFilePart(preferences.seed)}.csv`, 'text/csv;charset=utf-8');
  };

  return (
    <div className="test-profile-workbench">
      <GlassPanel className="test-profile-hero">
        <div>
          <span className="test-profile-eyebrow"><ShieldCheck size={15} /> REMOTE ADDRESS + LOCAL FIXTURES</span>
          <h2>多国地址与测试资料</h2>
          <p>上方可从已审计的固定地址源获取并本地保存资料；下方保留完全离线、可复现的表单测试资料。</p>
        </div>
        <div className="test-profile-hero-badges">
          <span>远端地址源</span>
          <span>本地保存</span>
          <span>可复现</span>
          <span>JSON / CSV</span>
        </div>
      </GlassPanel>

      <RemoteAddressProfiles />

      <div className="test-profile-safety" role="note">
        <ShieldCheck size={18} />
        <span><b>下方是离线测试模式：</b>姓名、地址和日期是本地合成数据；邮箱不可投递，电话、CPF 与 T.C. Kimlik No 不可用。支付字段仅为支付服务商 Sandbox 测试向量。</span>
      </div>

      <GlassPanel className="test-profile-controls">
        <div className="test-profile-section-heading">
          <div><b>选择国家</b><small>现有 JP / BR / US / GB / TR；新增国家只需注册一份独立规则</small></div>
          <span>{countryDefinition.flag} {countryDefinition.badge}</span>
        </div>

        <div className="test-profile-country-grid" role="tablist" aria-label="测试资料国家">
          {TEST_PROFILE_COUNTRIES.map((country) => (
            <button
              type="button"
              role="tab"
              aria-selected={country.code === preferences.country}
              className={country.code === preferences.country ? 'active' : ''}
              onClick={() => chooseCountry(country.code)}
              key={country.code}
            >
              <span>{country.flag}</span>
              <b>{country.label}</b>
              <small>{country.code}</small>
            </button>
          ))}
        </div>

        <div className="test-profile-generator-row">
          <label className="console-field test-profile-seed-field">
            <span>固定种子</span>
            <input
              className="input-glass"
              value={preferences.seed}
              onChange={(event) => updatePreferences({ seed: event.target.value })}
              placeholder="输入相同种子可复现同一批结果"
              autoComplete="off"
              spellCheck="false"
            />
            <small>国家、种子与序号相同，输出就相同；便于回归测试。</small>
          </label>
          <label className="console-field test-profile-count-field">
            <span>生成数量</span>
            <CompactNumberInput
              value={preferences.count}
              min={1}
              max={MAX_TEST_PROFILE_BATCH}
              onChange={(count) => updatePreferences({ count })}
              ariaLabel="测试资料数量"
            />
            <small>单次最多 {MAX_TEST_PROFILE_BATCH} 条。</small>
          </label>
          <div className="test-profile-generate-actions">
            <GlassButton variant="glass" icon={RefreshCw} onClick={useNewSeed}>换种子</GlassButton>
            <GlassButton variant="primary" icon={Dice5} onClick={() => generate()}>一键生成{countryDefinition.label}资料</GlassButton>
          </div>
        </div>
      </GlassPanel>

      <div className="test-profile-results-layout">
        <GlassPanel className="test-profile-batch-panel">
          <div className="test-profile-section-heading compact">
            <div><b>本批结果</b><small>{profiles.length} 条 · seed: {preferences.seed}</small></div>
            <span>{profiles.length}</span>
          </div>
          <div className="test-profile-batch-list">
            {profiles.map((profile, index) => (
              <button
                type="button"
                className={index === selectedIndex ? 'active' : ''}
                onClick={() => setSelectedIndex(index)}
                aria-current={index === selectedIndex ? 'true' : undefined}
                key={profile.profileId}
              >
                <span><b>{profile.fullName}</b><small>{profile.email}</small></span>
                <em>{String(index + 1).padStart(3, '0')}</em>
              </button>
            ))}
          </div>
          <div className="test-profile-batch-actions">
            <GlassButton variant="glass" icon={Copy} onClick={copyBatch}>复制整批</GlassButton>
            <GlassButton variant="glass" icon={FileJson2} onClick={exportJson}>JSON</GlassButton>
            <GlassButton variant="glass" icon={Table2} onClick={exportCsv}>CSV</GlassButton>
          </div>
        </GlassPanel>

        <GlassPanel className="test-profile-detail-panel">
          {selected ? (
            <>
              <div className="test-profile-detail-head">
                <div>
                  <span>{countryDefinition.flag} {selected.countryName} · SYNTHETIC + BRAINTREE SANDBOX</span>
                  <h3>{selected.fullName}</h3>
                  <code>{selected.profileId}</code>
                </div>
                <div className="test-profile-detail-actions">
                  <GlassButton variant="glass" icon={Copy} onClick={() => copy(formatTestProfile(selected), '当前资料')}>复制全部</GlassButton>
                  <GlassButton variant="glass" icon={Download} onClick={() => downloadText(`${JSON.stringify(selected, null, 2)}\n`, `${selected.profileId.toLowerCase()}.json`, 'application/json;charset=utf-8')}>下载</GlassButton>
                </div>
              </div>

              <div className="test-profile-field-grid">
                {selectedFields.map((field) => (
                  <div className={`${field.wide ? 'wide' : ''} ${field.warning ? 'test-placeholder' : ''} ${field.sandbox ? 'sandbox-card' : ''}`} key={field.key}>
                    <div><span>{field.label}</span><button type="button" onClick={() => copy(field.value, field.label)} aria-label={`复制${field.label}`}><Copy size={13} />复制</button></div>
                    <strong>{field.value}</strong>
                  </div>
                ))}
              </div>

              <div className="test-profile-safety-flags">
                <span>邮箱：不可投递</span>
                <span>电话：不可拨通</span>
                <span>证件：无效</span>
                <span>支付：Braintree Sandbox</span>
              </div>
            </>
          ) : <div className="console-empty">点击“生成资料”开始</div>}
        </GlassPanel>
      </div>
    </div>
  );
}
