import React, { useState } from 'react';
import { ArrowRightLeft, Copy, Check, FileCode, Play, Sparkles } from 'lucide-react';
import GlassPanel from '../../ui/GlassPanel';
import GlassButton from '../../ui/GlassButton';
import CustomSelect from '../../ui/CustomSelect';
import apiClient from '../../api/client';

export default function ConverterPipelineNode({ title = '多格式凭证转换流水线', defaultType = 'openai' }) {
  const [inputType, setInputType] = useState('sso_json');
  const [outputType, setOutputType] = useState('cpa_oauth');
  const [inputText, setInputText] = useState('');
  const [outputText, setOutputText] = useState('');
  const [converting, setConverting] = useState(false);
  const [copied, setCopied] = useState(false);

  const handleConvert = async () => {
    if (!inputText.trim()) return;
    setConverting(true);
    try {
      let endpoint = '/converters/openai';
      if (defaultType === 'grok') {
        endpoint = '/grok/convert';
      }
      const res = await apiClient.post(endpoint, {
        input_format: inputType,
        output_format: outputType,
        data: inputText,
      });

      setOutputText(typeof res === 'object' ? JSON.stringify(res, null, 2) : String(res));
    } catch (err) {
      setOutputText(`[转换失败] ${err.message}`);
    } finally {
      setConverting(false);
    }
  };

  const handleCopy = () => {
    navigator.clipboard.writeText(outputText);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <GlassPanel style={{ padding: '1.5rem', display: 'flex', flexDirection: 'column', gap: '1.25rem' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <h3 style={{ fontSize: '1.05rem', fontWeight: 700, display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <ArrowRightLeft size={18} style={{ color: 'var(--accent-color)' }} />
          {title}
        </h3>
        <span className="status-badge bg-accent">Pipeline Converter</span>
      </div>

      {/* Format Selector Bar */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr auto 1fr', gap: '1rem', alignItems: 'center' }}>
        <div>
          <label className="label-glass">输入源格式 (Input Format)</label>
          <CustomSelect value={inputType} onChange={setInputType} options={[{ value: 'sso_json', label: 'Grok / OAI SSO Token (JSON / Cookie)' }, { value: 'refresh_token', label: 'OAuth Refresh Token (str)' }, { value: 'access_token', label: 'Direct Access Token' }, { value: 'auth_json', label: 'Standard Auth JSON (xAI / Sub2API)' }]} ariaLabel="输入源格式" />
        </div>

        <div style={{ paddingTop: '1.2rem', color: 'var(--accent-color)' }}>
          <Sparkles size={20} />
        </div>

        <div>
          <label className="label-glass">输出目标格式 (Target Format)</label>
          <CustomSelect value={outputType} onChange={setOutputType} options={[{ value: 'cpa_oauth', label: 'CLIProxyAPI (CPA) Config Payload' }, { value: 'sub2api', label: 'Sub2API Account JSON' }, { value: 'grok2api', label: 'Grok2API Account Credentials' }, { value: 'header_bearer', label: 'Standard Authorization Bearer' }]} ariaLabel="输出目标格式" />
        </div>
      </div>

      {/* Input / Output Editors */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: '1rem' }}>
        <div>
          <label className="label-glass">原始凭证内容 / 批量输入:</label>
          <textarea
            className="input-glass"
            rows={8}
            style={{ fontFamily: 'var(--font-mono)', fontSize: '0.8rem', resize: 'vertical' }}
            placeholder="粘贴待转换的凭证、Cookies 或 JSON 内容..."
            value={inputText}
            onChange={(e) => setInputText(e.target.value)}
          />
        </div>

        <div>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <label className="label-glass">转换结果产出 (Pipeline Output):</label>
            {outputText && (
              <button
                onClick={handleCopy}
                style={{ background: 'transparent', border: 'none', color: 'var(--accent-color)', fontSize: '0.75rem', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '0.2rem', marginBottom: '0.4rem' }}
              >
                {copied ? <Check size={12} /> : <Copy size={12} />}
                {copied ? '已复制' : '复制结果'}
              </button>
            )}
          </div>
          <textarea
            className="input-glass"
            rows={8}
            readOnly
            style={{ fontFamily: 'var(--font-mono)', fontSize: '0.8rem', resize: 'vertical', background: 'var(--bg-secondary)' }}
            placeholder="点击下方转换按钮后显示计算出的新格式数据..."
            value={outputText}
          />
        </div>
      </div>

      {/* Convert Action */}
      <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
        <GlassButton variant="primary" onClick={handleConvert} loading={converting} icon={Play}>
          执行转换流水线 (Run Conversion)
        </GlassButton>
      </div>
    </GlassPanel>
  );
}
