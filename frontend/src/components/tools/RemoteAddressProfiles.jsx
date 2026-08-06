import React, { useMemo, useState } from 'react';
import { Copy, Download, Globe2, RefreshCw } from 'lucide-react';

import GlassPanel from '../../ui/GlassPanel';
import GlassButton from '../../ui/GlassButton';
import CustomSelect from '../../ui/CustomSelect';
import { CollapsiblePanel } from '../../ui/ConsolePrimitives';
import apiClient from '../../api/client';
import { useToast } from '../../contexts/ToastContext';
import {
  REMOTE_ADDRESS_COUNTRIES,
  formatRemoteAddressProfile,
  remoteAddressFields,
} from '../../utils/addressProfileSource';

const STORAGE_KEY = 'automyai.remote-address-profile.v1';
const PRIMARY_FIELD_KEYS = new Set([
  'Temporary_mail', 'Full_Name', 'Full_Name_Tran', 'Zip_Code', 'State', 'State_Full',
  'City', 'Address', 'Trans_Address', 'Trans_Cn_Address', 'Full_Address_Combined', 'Telephone', 'Password', 'Birthday',
]);
const EMPLOYMENT_FIELD_KEYS = new Set([
  'Educational_Background', 'Occupation', 'Employment_Status', 'Monthly_Salary',
  'Company_Size', 'Company_Name', 'Industry',
]);

function readSavedProfile() {
  try {
    const value = JSON.parse(window.localStorage.getItem(STORAGE_KEY) || 'null');
    return value?.schema === 'automyai.remote-address-profile.v1' ? value : null;
  } catch (_) {
    return null;
  }
}

function saveProfile(profile) {
  try { window.localStorage.setItem(STORAGE_KEY, JSON.stringify(profile)); } catch (_) {}
}

async function copyText(value) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(String(value || ''));
    return;
  }
  const textarea = document.createElement('textarea');
  textarea.value = String(value || '');
  textarea.style.position = 'fixed';
  textarea.style.opacity = '0';
  document.body.appendChild(textarea);
  textarea.select();
  document.execCommand('copy');
  textarea.remove();
}

function downloadJson(profile) {
  const blob = new Blob([`${JSON.stringify(profile, null, 2)}\n`], { type: 'application/json;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = `remote-address-${String(profile?.country?.code || 'profile').toLowerCase()}.json`;
  anchor.click();
  URL.revokeObjectURL(url);
}

export default function RemoteAddressProfiles() {
  const { notify } = useToast();
  const [country, setCountry] = useState('RANDOM');
  const [city, setCity] = useState('');
  const [profile, setProfile] = useState(readSavedProfile);
  const [busy, setBusy] = useState(false);
  const fields = useMemo(() => remoteAddressFields(profile), [profile]);
  const primaryFields = useMemo(() => fields.filter((field) => PRIMARY_FIELD_KEYS.has(field.key)), [fields]);
  const employmentFields = useMemo(() => fields.filter((field) => EMPLOYMENT_FIELD_KEYS.has(field.key)), [fields]);
  const additionalFields = useMemo(
    () => fields.filter((field) => !PRIMARY_FIELD_KEYS.has(field.key) && !EMPLOYMENT_FIELD_KEYS.has(field.key)),
    [fields],
  );

  const chooseCountry = (nextCountry) => {
    setCountry(nextCountry);
    if (nextCountry === 'BR') setCity('');
  };

  const fetchProfile = async () => {
    setBusy(true);
    try {
      const result = await apiClient.post('/address-profiles/random', { country, city: city.trim() });
      if (!result?.profile) throw new Error('地址站点没有返回资料');
      setProfile(result.profile);
      saveProfile(result.profile);
      notify(`已获取 ${result.profile.country?.label || '随机国家'} 地址资料，并保存到本地`, 'success');
    } catch (error) {
      notify(error?.message || '获取远端地址资料失败', 'error');
    } finally {
      setBusy(false);
    }
  };

  const copy = async (value, label) => {
    try {
      await copyText(value);
      notify(`${label}已复制`, 'success');
    } catch (error) {
      notify(error?.message || '浏览器拒绝了剪贴板访问', 'error');
    }
  };

  return (
    <GlassPanel className="remote-address-panel">
      <div className="test-profile-section-heading">
        <div>
          <b><Globe2 size={16} /> 远端地址资料（本地保存）</b>
          <small>日本/美/英/土耳其等使用 meiguodizhi.com，巴西使用 cn.americaaddress.com；可指定国家与城市或随机获取。</small>
        </div>
        <span className="remote-address-source-badge">地址字段原样转发</span>
      </div>

      <div className="remote-address-controls">
        <label className="console-field">
          <span>国家</span>
          <CustomSelect
            value={country}
            onChange={chooseCountry}
            options={[
              { value: 'RANDOM', label: '🎲 随机国家' },
              ...REMOTE_ADDRESS_COUNTRIES.map((item) => ({ value: item.code, label: `${item.flag} ${item.label}（${item.code}）` })),
            ]}
            ariaLabel="远端地址国家"
          />
        </label>
        <label className="console-field remote-address-city-field">
          <span>城市（可留空随机）</span>
          <input
            className="input-glass"
            value={city}
            onChange={(event) => setCity(event.target.value.slice(0, 80))}
            placeholder={country === 'BR' ? '巴西源站仅支持随机城市' : '例如 London；留空由源站随机'}
            disabled={country === 'BR'}
            autoComplete="off"
          />
        </label>
        <GlassButton variant="primary" icon={busy ? RefreshCw : Globe2} loading={busy} onClick={fetchProfile}>
          获取远端资料
        </GlassButton>
      </div>


      {profile ? (
        <div className="remote-address-result">
          <div className="remote-address-result-head">
            <div>
              <span>{profile.country?.label || profile.country?.code} · {profile.source?.provider || 'remote'}</span>
              <h3>{profile.fields?.Full_Name || profile.fields?.Full_Name_Tran || '地址资料'}</h3>
              <small>{profile.source?.fetchedAt || ''}{profile.query?.city ? ` · 城市查询：${profile.query.city}` : ' · 随机城市'}</small>
            </div>
            <div className="test-profile-detail-actions">
              <GlassButton variant="glass" icon={Copy} onClick={() => copy(formatRemoteAddressProfile(profile), '远端资料')}>复制全部</GlassButton>
              <GlassButton variant="glass" icon={Download} onClick={() => downloadJson(profile)}>下载 JSON</GlassButton>
            </div>
          </div>

          <div className="test-profile-field-grid remote-address-field-grid remote-address-priority-grid">
            {primaryFields.map((field) => (
              <div className={field.key.includes('Address') ? 'wide' : ''} key={field.key}>
                <div><span>{field.label}</span><button type="button" onClick={() => copy(field.value, field.label)} aria-label={`复制${field.label}`}><Copy size={13} />复制</button></div>
                <strong>{field.value}</strong>
              </div>
            ))}
          </div>

          {(employmentFields.length || additionalFields.length) ? <div className="remote-address-secondary">
            {employmentFields.length ? <CollapsiblePanel
              title="就业与公司资料"
              summary={`${employmentFields.length} 项 · 默认折叠`}
              storageKey="remote-address-employment"
            >
              <div className="test-profile-field-grid remote-address-field-grid">
                {employmentFields.map((field) => <div key={field.key}>
                  <div><span>{field.label}</span><button type="button" onClick={() => copy(field.value, field.label)}><Copy size={13} />复制</button></div>
                  <strong>{field.value}</strong>
                </div>)}
              </div>
            </CollapsiblePanel> : null}
            {additionalFields.length ? <CollapsiblePanel
              title="其他身份、设备与附加资料"
              summary={`${additionalFields.length} 项 · 默认折叠`}
              storageKey="remote-address-additional"
            >
              <div className="test-profile-field-grid remote-address-field-grid">
                {additionalFields.map((field) => <div className={field.key === 'Browser_User_Agent' ? 'wide' : ''} key={field.key}>
                  <div><span>{field.label}</span><button type="button" onClick={() => copy(field.value, field.label)}><Copy size={13} />复制</button></div>
                  <strong>{field.value}</strong>
                </div>)}
              </div>
            </CollapsiblePanel> : null}
          </div> : null}

        </div>
      ) : (
        <div className="console-empty remote-address-empty">点击“获取远端资料”开始；当前没有本地缓存。</div>
      )}
    </GlassPanel>
  );
}
