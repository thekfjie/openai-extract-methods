import React, { useState } from 'react';
import { ArrowLeft, Play, Square, Activity, RefreshCw } from 'lucide-react';
import GlassPanel from '../ui/GlassPanel';
import GlassButton from '../ui/GlassButton';
import WorkflowCanvas from '../components/workflow/WorkflowCanvas';
import AutomationFlowRunner from '../components/workflow/AutomationFlowRunner';
import CheckpointPanel from '../components/workflow/CheckpointPanel';
import BatchExecutionMonitor from '../components/workflow/BatchExecutionMonitor';
import { useNavigate } from 'react-router-dom';
import { CollapsiblePanel } from '../ui/ConsolePrimitives';
import { useToast } from '../contexts/ToastContext';

export default function WorkflowDetail() {
  const navigate = useNavigate();
  const [running, setRunning] = useState(true);
  const [showCheckpoint, setShowCheckpoint] = useState(true);
  const [concurrency, setConcurrency] = useState(8);
  const { notify } = useToast();

  const nodes = [
    { id: '1', name: '临时/协议邮箱生成', provider: 'DuckMail & Outlook', status: 'completed', type: 'mail', duration: '0.3s' },
    { id: '2', name: 'FlareSolverr 过盾', provider: 'CF Clearance Worker', status: 'completed', type: 'proxy', duration: '1.8s' },
    { id: '3', name: '验证码/人工决策节点', provider: 'Human Checkpoint', status: 'running', type: 'captcha' },
    { id: '4', name: 'CPA / Sub2API 导入', provider: 'API Sync Node', status: 'idle', type: 'token' },
  ];

  const logs = [
    { time: '12:00:15', level: 'info', message: '[FlowInit] 工作流 #WF-8891 启动，准备加载参数与代理池...' },
    { time: '12:00:16', level: 'info', message: '[MailService] 已分配测试邮箱 account_temp_92@duck.com' },
    { time: '12:00:18', level: 'info', message: '[FlareSolverr] CF Token clearance 成功获取: cf_clearance=x8a...' },
    { time: '12:00:20', level: 'warn', message: '[Checkpoint] 检测到目标注册节点触发 6 位邮箱验证码，等待人工或协议回传...' },
  ];

  const handleCheckpointSubmit = async (val) => {
    notify(`已提交验证码：${val}`, 'success');
    setShowCheckpoint(false);
  };

  return (
    <div className="page-container operations-page">
      <div className="page-header">
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
          <GlassButton variant="icon" onClick={() => navigate(-1)}>
            <ArrowLeft size={18} />
          </GlassButton>
          <div>
            <h1>工作流运行与调试 #WF-8891</h1>
            <p style={{ color: 'var(--text-secondary)', fontSize: '0.85rem' }}>OpenAI / Grok 自动化流程实时追踪</p>
          </div>
        </div>

        <div style={{ display: 'flex', gap: '0.75rem' }}>
          <GlassButton variant="danger" icon={Square} onClick={() => setRunning(false)}>
            紧急终止工作流
          </GlassButton>
        </div>
      </div>

      <AutomationFlowRunner
        title="实时控制台与步骤追踪"
        running={running}
        progress={{ current: 3, total: 4, step: '验证码 / 人工决策节点' }}
        logs={logs}
        onStart={() => setRunning(true)}
        onStop={() => setRunning(false)}
      />

      {showCheckpoint && (
        <CheckpointPanel
          checkpoint={{
            title: '需要输入 6 位邮箱验证码',
            prompt: '已向 account_temp_92@duck.com 发送验证码，请输入收到的 6 位纯数字验证码继续流程。',
            placeholder: '输入 6 位验证码 (如 849201)...'
          }}
          onSubmit={handleCheckpointSubmit}
          onCancel={() => setShowCheckpoint(false)}
        />
      )}

      <CollapsiblePanel title="流程图与批处理状态" summary="当前日志保持在首屏；节点图和并发详情按需展开">
        <div className="operations-stack"><WorkflowCanvas nodes={nodes} activeNodeId="3" /><BatchExecutionMonitor concurrency={concurrency} onConcurrencyChange={setConcurrency} stats={{ total: 50, success: 38, failed: 2, running: concurrency }} proxyStats={{ total: 15, active: 14 }} /></div>
      </CollapsiblePanel>
    </div>
  );
}
