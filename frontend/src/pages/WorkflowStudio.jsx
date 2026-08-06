import React, { useState } from 'react';
import { Plus, Play, Sliders } from 'lucide-react';
import GlassPanel from '../ui/GlassPanel';
import GlassButton from '../ui/GlassButton';
import FlowNodeCard from '../components/workflow/FlowNodeCard';
import WorkflowCanvas from '../components/workflow/WorkflowCanvas';
import { useNavigate } from 'react-router-dom';
import { useToast } from '../contexts/ToastContext';

const PRESET_PIPELINES = [
  {
    id: 'openai_sub2api_flow',
    name: 'OpenAI 协议注册 -> Sub2API OAuth 导入流水线',
    category: 'Account Registration',
    description: '纯协议/无头浏览器创建 OpenAI 账号，通过 FlareSolverr 获取 CF clearance，注册完成后提取 Refresh Token 并导入 Sub2API。',
    nodes: [
      { id: 'n1', name: '邮箱库存/DuckMail 分配', provider: 'Mail Provider', status: 'completed', type: 'mail' },
      { id: 'n2', name: 'Cloudflare 过盾 (FlareSolverr)', provider: 'CF Clearance Worker', status: 'completed', type: 'proxy' },
      { id: 'n3', name: 'OpenAI 注册协议步进', provider: 'uc_signup.py Engine', status: 'running', type: 'bot' },
      { id: 'n4', name: 'Sub2API OAuth 凭证写入', provider: 'sub2api_client', status: 'idle', type: 'token' },
    ]
  },
  {
    id: 'grok_device_cpa_flow',
    name: 'Grok SSO / Auth JSON -> CPA Device-Flow 转换流水线',
    category: 'Credential Conversion',
    description: '读取 Grok 已有 SSO / Cookie 凭证，模拟 Device-Flow OAuth 换取 Refresh Token，绑定 CLIProxyAPI 并自动轮换代理。',
    nodes: [
      { id: 'n1', name: 'Grok 凭证与 Token 校验', provider: 'grok_signup.py', status: 'completed', type: 'token' },
      { id: 'n2', name: 'Device-Flow OAuth 触发', provider: 'CLIProxyAPI', status: 'running', type: 'bot' },
      { id: 'n3', name: 'Grok2API / CPA 接入写入', provider: 'Grok2API Sync', status: 'idle', type: 'proxy' },
    ]
  },
  {
    id: 'outlook_hot_reg_flow',
    name: 'Outlook / Hotmail 纯协议自动化注册流水线',
    category: 'Identity Pool',
    description: '微软纯协议批量注册 4 段格式邮箱 (User:Pass:RefreshToken:ClientId)，自动存入本地可消耗邮箱池。',
    nodes: [
      { id: 'n1', name: '验证码 / SMS 平台接收', provider: 'HeroSMS / TeleAuto', status: 'completed', type: 'mail' },
      { id: 'n2', name: '微软注册协议交互', provider: 'outlook_register', status: 'running', type: 'bot' },
      { id: 'n3', name: '四段格式校验与存库', provider: 'Mail Pool Storage', status: 'idle', type: 'token' },
    ]
  }
];

export default function WorkflowStudio() {
  const [selectedPipeline, setSelectedPipeline] = useState(PRESET_PIPELINES[0]);
  const navigate = useNavigate();
  const { notify } = useToast();

  return (
    <div className="page-container operations-page workflow-studio-page">
      <div className="page-header">
        <div className="page-title-group">
          <h1>流程设计器 (Workflow Studio)</h1>
        </div>

        <GlassButton variant="primary" icon={Plus}>
          创建自定义工作流
        </GlassButton>
      </div>

      {/* Pipeline Selector Tabs */}
      <div className="segmented-tabs workflow-preset-tabs" role="tablist">
        {PRESET_PIPELINES.map((p) => (
          <button
            type="button"
            role="tab"
            aria-selected={selectedPipeline.id === p.id}
            key={p.id}
            onClick={() => setSelectedPipeline(p)}
            className={`segmented-tab ${selectedPipeline.id === p.id ? 'active' : ''}`}
          >
            {p.name}
          </button>
        ))}
      </div>

      {/* Selected Workflow Visualizer */}
      <GlassPanel variant="strong" className="workflow-preview view-transition" key={selectedPipeline.id}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div>
            <h2 style={{ fontSize: '1.2rem', fontWeight: 700 }}>{selectedPipeline.name}</h2>
            <p style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', marginTop: '0.2rem' }}>
              {selectedPipeline.description}
            </p>
          </div>

          <GlassButton
            variant="primary"
            icon={Play}
            onClick={() => {
              if (selectedPipeline.id.includes('openai')) navigate('/openai');
              else if (selectedPipeline.id.includes('grok')) navigate('/grok');
              else navigate('/infrastructure');
            }}
          >
            直接调起执行 (Run Workflow)
          </GlassButton>
        </div>

        <WorkflowCanvas nodes={selectedPipeline.nodes} activeNodeId="n3" />
      </GlassPanel>

      {/* Pipeline Step Node List */}
      <div className="workflow-node-section">
        <h3 style={{ fontSize: '1.1rem', fontWeight: 600, marginBottom: '1rem', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <Sliders size={18} style={{ color: 'var(--accent-color)' }} />
          工作流节点清单与配置 (Pipeline Node Modules)
        </h3>

        <div className="card-grid workflow-node-cards">
          {selectedPipeline.nodes.map((node) => (
            <FlowNodeCard
              key={node.id}
              node={{
                name: node.name,
                category: selectedPipeline.category,
                description: `服务提供者: ${node.provider}。负责流程中的数据流转与逻辑步进。`,
                enabled: true,
                successRate: '98.8%',
                avgDuration: node.duration || '0.8s'
              }}
              onExecute={() => notify(`单节点测试已触发：${node.name}`, 'info')}
              onConfigure={() => notify(`参数配置入口：${node.name}`, 'info')}
            />
          ))}
        </div>
      </div>
    </div>
  );
}
