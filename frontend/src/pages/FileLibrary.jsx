import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Copy,
  Download,
  FilePlus2,
  FileText,
  FolderOpen,
  RefreshCw,
  Save,
  Search,
  Trash2,
  Upload,
} from 'lucide-react';
import apiClient from '../api/client';
import { useToast } from '../contexts/ToastContext';
import { ErrorBanner, MetricCard } from '../ui/ConsolePrimitives';
import GlassButton from '../ui/GlassButton';
import GlassPanel from '../ui/GlassPanel';

const ACCEPTED_EXTENSIONS = [
  '.txt', '.md', '.markdown', '.csv', '.json', '.jsonl', '.yaml', '.yml', '.xml', '.html',
  '.css', '.js', '.jsx', '.ts', '.tsx', '.py', '.sh', '.ini', '.conf', '.log', '.sql',
];
const MAX_FILE_BYTES = 1024 * 1024;

function bytesLabel(bytes) {
  const value = Number(bytes || 0);
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(value < 10 * 1024 ? 1 : 0)} KiB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MiB`;
}

function timeLabel(value) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  }).format(date);
}

async function copyText(text) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const helper = document.createElement('textarea');
  helper.value = text;
  helper.setAttribute('readonly', '');
  helper.style.position = 'fixed';
  helper.style.opacity = '0';
  document.body.appendChild(helper);
  helper.select();
  const copied = document.execCommand('copy');
  helper.remove();
  if (!copied) throw new Error('浏览器未授权复制，请手动选择内容复制');
}

function downloadText(item) {
  const blob = new Blob([item.content || ''], { type: 'text/plain;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = item.name || 'file.txt';
  anchor.click();
  URL.revokeObjectURL(url);
}

export default function FileLibrary() {
  const { notify } = useToast();
  const fileInput = useRef(null);
  const [items, setItems] = useState([]);
  const [selectedId, setSelectedId] = useState('');
  const [selected, setSelected] = useState(null);
  const [draftName, setDraftName] = useState('');
  const [draftContent, setDraftContent] = useState('');
  const [query, setQuery] = useState('');
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const loadItem = useCallback(async (itemId) => {
    if (!itemId) {
      setSelected(null);
      setDraftName('');
      setDraftContent('');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const result = await apiClient.get(`/file-library/${itemId}`);
      const item = result.item;
      setSelected(item);
      setDraftName(item.name || '');
      setDraftContent(item.content || '');
    } catch (reason) {
      setError(reason);
    } finally {
      setBusy(false);
    }
  }, []);

  const loadLibrary = useCallback(async (preferredId = '') => {
    setLoading(true);
    setError(null);
    try {
      const result = await apiClient.get('/file-library');
      const nextItems = result.items || [];
      setItems(nextItems);
      const candidate = preferredId || selectedId;
      const nextId = nextItems.some((item) => item.id === candidate) ? candidate : (nextItems[0]?.id || '');
      setSelectedId(nextId);
      await loadItem(nextId);
    } catch (reason) {
      setError(reason);
    } finally {
      setLoading(false);
    }
  }, [loadItem, selectedId]);

  useEffect(() => {
    loadLibrary();
  // The initial request intentionally owns selection setup.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const filteredItems = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    if (!needle) return items;
    return items.filter((item) => String(item.name || '').toLocaleLowerCase().includes(needle));
  }, [items, query]);

  const dirty = !!selected && (draftName !== selected.name || draftContent !== selected.content);
  const totalBytes = items.reduce((sum, item) => sum + Number(item.sizeBytes || 0), 0);

  const chooseItem = async (itemId) => {
    if (itemId === selectedId) return;
    if (dirty && !window.confirm('当前文件有未保存的修改，确定切换文件吗？')) return;
    setSelectedId(itemId);
    await loadItem(itemId);
  };

  const uploadFiles = async (event) => {
    const files = Array.from(event.target.files || []);
    event.target.value = '';
    if (!files.length) return;
    setBusy(true);
    setError(null);
    let latestId = '';
    let successCount = 0;
    try {
      for (const file of files) {
        const extension = `.${file.name.split('.').pop() || ''}`.toLowerCase();
        if (!ACCEPTED_EXTENSIONS.includes(extension)) throw new Error(`${file.name} 不是支持的文本文件类型`);
        if (file.size > MAX_FILE_BYTES) throw new Error(`${file.name} 超过 1 MiB 限制`);
        const content = await file.text();
        const result = await apiClient.post('/file-library', { name: file.name, content });
        latestId = result.item.id;
        successCount += 1;
      }
      notify(`已保存 ${successCount} 个文本文件`, 'success');
      await loadLibrary(latestId);
    } catch (reason) {
      setError(reason);
      if (successCount) await loadLibrary(latestId);
    } finally {
      setBusy(false);
    }
  };

  const createBlank = async () => {
    const name = window.prompt('新文件名（例如 notes.txt）', 'notes.txt');
    if (!name) return;
    setBusy(true);
    setError(null);
    try {
      const result = await apiClient.post('/file-library', { name, content: '' });
      notify(`${name} 已创建`, 'success');
      await loadLibrary(result.item.id);
    } catch (reason) {
      setError(reason);
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    if (!selected || !dirty) return;
    setBusy(true);
    setError(null);
    try {
      const result = await apiClient.post(`/file-library/${selected.id}`, {
        name: draftName,
        content: draftContent,
      });
      setSelected(result.item);
      setDraftName(result.item.name || '');
      setDraftContent(result.item.content || '');
      notify(`${result.item.name} 已保存`, 'success');
      await loadLibrary(result.item.id);
    } catch (reason) {
      setError(reason);
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (!selected || !window.confirm(`确定从文件库删除“${selected.name}”吗？`)) return;
    setBusy(true);
    setError(null);
    try {
      await apiClient.delete(`/file-library/${selected.id}`);
      notify(`${selected.name} 已删除`, 'success');
      setSelected(null);
      setSelectedId('');
      await loadLibrary();
    } catch (reason) {
      setError(reason);
    } finally {
      setBusy(false);
    }
  };

  const copy = async () => {
    if (!selected) return;
    try {
      await copyText(draftContent);
      notify(`${draftName || selected.name} 内容已复制`, 'success');
    } catch (reason) {
      setError(reason);
    }
  };

  return (
    <div className="page-container file-library-page">
      <div className="page-header">
        <div className="page-title-group">
          <h1>文件库 / 素材库</h1>
          <p>集中保存常用文本素材，随时查看、编辑、下载或一键复制。</p>
        </div>
        <div className="console-actions">
          <input
            ref={fileInput}
            className="file-library-input"
            type="file"
            multiple
            accept={ACCEPTED_EXTENSIONS.join(',')}
            onChange={uploadFiles}
          />
          <GlassButton variant="glass" icon={FilePlus2} onClick={createBlank} disabled={busy}>新建文本</GlassButton>
          <GlassButton variant="primary" icon={Upload} onClick={() => fileInput.current?.click()} disabled={busy}>上传文本</GlassButton>
        </div>
      </div>

      <ErrorBanner error={error} onRetry={() => loadLibrary(selectedId)} />

      <div className="file-library-metrics">
        <MetricCard label="已保存文件" value={items.length} />
        <MetricCard label="占用空间" value={bytesLabel(totalBytes)} />
        <MetricCard label="单文件上限" value="1 MiB" />
      </div>

      <div className="file-library-layout">
        <GlassPanel className="file-library-browser">
          <div className="file-library-panel-head">
            <span><FolderOpen size={18} /><b>素材列表</b></span>
            <GlassButton variant="icon" title="刷新文件库" onClick={() => loadLibrary(selectedId)} loading={loading}>
              <RefreshCw size={16} />
            </GlassButton>
          </div>
          <label className="file-library-search">
            <Search size={15} />
            <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索文件名" />
          </label>
          <div className="file-library-list" aria-busy={loading}>
            {filteredItems.map((item) => (
              <button
                type="button"
                className={`file-library-item ${selectedId === item.id ? 'active' : ''}`}
                key={item.id}
                onClick={() => chooseItem(item.id)}
              >
                <FileText size={18} />
                <span><b title={item.name}>{item.name}</b><small>{bytesLabel(item.sizeBytes)} · {item.lineCount} 行</small></span>
                <time>{timeLabel(item.updatedAt)}</time>
              </button>
            ))}
            {!loading && !filteredItems.length ? (
              <div className="file-library-empty"><FolderOpen size={30} /><b>{query ? '没有匹配文件' : '文件库还是空的'}</b><small>{query ? '换个关键词试试' : '上传文本文件后会显示在这里'}</small></div>
            ) : null}
          </div>
        </GlassPanel>

        <GlassPanel className="file-library-editor">
          {selected ? (
            <>
              <div className="file-library-panel-head file-library-editor-head">
                <div>
                  <span><FileText size={18} /><b>内容查看与编辑</b></span>
                  <small>{bytesLabel(selected.sizeBytes)} · {selected.lineCount} 行 · 更新于 {timeLabel(selected.updatedAt)}</small>
                </div>
                <div className="console-actions">
                  <GlassButton variant="glass" icon={Copy} onClick={copy}>复制内容</GlassButton>
                  <GlassButton variant="glass" icon={Download} onClick={() => downloadText({ ...selected, name: draftName, content: draftContent })}>下载</GlassButton>
                  <GlassButton variant="primary" icon={Save} onClick={save} disabled={!dirty} loading={busy}>保存修改</GlassButton>
                  <GlassButton variant="danger" icon={Trash2} onClick={remove} disabled={busy}>删除</GlassButton>
                </div>
              </div>
              <label className="console-field file-library-name-field">
                <span>文件名</span>
                <input className="input-glass" value={draftName} onChange={(event) => setDraftName(event.target.value)} maxLength={180} />
              </label>
              <textarea
                className="input-glass file-library-content"
                value={draftContent}
                onChange={(event) => setDraftContent(event.target.value)}
                spellCheck="false"
                aria-label={`${selected.name} 内容`}
              />
              <div className="file-library-editor-foot">
                <span>{new Blob([draftContent]).size.toLocaleString()} / {MAX_FILE_BYTES.toLocaleString()} 字节</span>
                <span>{draftContent.length.toLocaleString()} 字符</span>
                {dirty ? <b>有未保存修改</b> : <span>内容已保存</span>}
              </div>
            </>
          ) : (
            <div className="file-library-empty file-library-editor-empty">
              <FileText size={38} />
              <b>{loading ? '正在加载文件库' : '选择一个文件查看内容'}</b>
              <small>也可以上传现有文本，或新建一个空白文本。</small>
            </div>
          )}
        </GlassPanel>
      </div>
    </div>
  );
}
