(()=>{
  const base=document.documentElement.dataset.apiBase||'';
  const panel=document.getElementById('cdkGate');
  const input=document.getElementById('cdkInput');
  const button=document.getElementById('cdkActivateButton');
  const switchButton=document.getElementById('cdkSwitchButton');
  const entryForm=document.getElementById('cdkEntryForm');
  const message=document.getElementById('cdkMessage');
  const usageBadge=document.getElementById('cdkUsageBadge');
  const currentRecord=document.getElementById('cdkCurrentRecord');
  const releaseTasksButton=document.getElementById('cdkReleaseTasksButton');
  const taskQueryButton=document.getElementById('cdkTaskQueryButton');
  const mergeHistoryButton=document.getElementById('cdkMergeHistoryButton');
  const toolsDialog=document.getElementById('cdkToolsDialog');
  const toolsClose=document.getElementById('cdkToolsClose');
  const queryCurrentTasks=document.getElementById('cdkQueryCurrentTasks');
  const mergeLookupInput=document.getElementById('cdkMergeLookupInput');
  const mergeLookupSubmit=document.getElementById('cdkMergeLookupSubmit');
  const toolsMessage=document.getElementById('cdkToolsMessage');
  const toolsResult=document.getElementById('cdkToolsResult');
  const mergeToggle=document.getElementById('cdkMergeToggleButton');
  const mergeDialog=document.getElementById('cdkMergeDialog');
  const mergeClose=document.getElementById('cdkMergeClose');
  const mergeCodes=document.getElementById('cdkMergeCodes');
  const mergeSubmit=document.getElementById('cdkMergeSubmit');
  const mergeCopy=document.getElementById('cdkMergeCopy');
  const mergeMessage=document.getElementById('cdkMergeMessage');
  const mergedCode=document.getElementById('cdkMergedCode');
  if(!panel)return;
  const labels={
    CDK_INVALID:'CDK \u4e0d\u6b63\u786e',CDK_FORMAT_INVALID:'CDK \u683c\u5f0f\u4e0d\u6b63\u786e',
    CDK_DISABLED:'CDK \u5df2\u505c\u7528',CDK_EXPIRED:'CDK \u5df2\u8fc7\u671f',
    CDK_ACTIVATION_LIMIT:'CDK \u652f\u4ed8\u6b21\u6570\u5df2\u7528\u5b8c',CDK_USAGE_LIMIT:'CDK \u652f\u4ed8\u6b21\u6570\u5df2\u7528\u5b8c'
  };
  const showMessage=(text,kind='')=>{message.textContent=text;message.className=('cdk-inline-message '+kind).trim()};
  const fullCodeStorageKey='cardLinkFullCdkById';
  const readFullCodes=()=>{try{return JSON.parse(localStorage.getItem(fullCodeStorageKey)||'{}')||{}}catch{return {}}};
  const rememberFullCode=(session,code)=>{const value=String(code||'').trim();const id=Number(session?.id||0);if(!value||!id)return;const map=readFullCodes();map[String(id)]=value;localStorage.setItem(fullCodeStorageKey,JSON.stringify(map));session.full_code=value};
  const resolveFullCode=session=>String(session?.full_code||readFullCodes()[String(Number(session?.id||0))]||session?.code_hint||('CDK ID '+String(session?.id||'-')));
  const renderUsage=session=>{
    if(!session)return;
    const fullCode=resolveFullCode(session);session.full_code=fullCode;
    window.cdkUsageState={...session};
    window.dispatchEvent(new CustomEvent('cdk-usage-updated',{detail:window.cdkUsageState}));
    const used=Number(session.usage_count??session.activation_count??0);
    const maximum=Number(session.max_uses??session.max_activations??0);
    if(maximum<=0)return;
    const remaining=Math.max(0,Number(session.remaining_uses??(maximum-used)));
    usageBadge.innerHTML='<span>CDK \u6210\u529f\u652f\u4ed8</span><b>'+used+' / '+maximum+'</b><small>\u5269\u4f59 '+remaining+' \u6b21</small>';
    usageBadge.hidden=false;
    const hint=fullCode;
    const historyKey='cardLinkCdkUsageHistory';
    let history=[];try{history=JSON.parse(localStorage.getItem(historyKey)||'[]')}catch{}
    history=[{id:Number(session.id||0),hint,used_at:Date.now()},...history.filter(item=>Number(item.id||0)!==Number(session.id||0))].slice(0,8);
    localStorage.setItem(historyKey,JSON.stringify(history));
    currentRecord.innerHTML='<span>\u5f53\u524d CDK</span><b>'+hint+'</b><small>\u6700\u8fd1\u4f7f\u7528 '+new Date().toLocaleString()+'</small>';
    currentRecord.hidden=false;
    releaseTasksButton.hidden=false;
    taskQueryButton.hidden=false;mergeHistoryButton.hidden=false;
  };
  const showEntry=(text='\u8f93\u5165 CDK \u540e\u5373\u53ef\u63d0\u4ea4\u4efb\u52a1')=>{
    entryForm.hidden=false;switchButton.hidden=true;showMessage(text);setTimeout(()=>input.focus(),50);
  };
  const showConnected=session=>{
    renderUsage(session);entryForm.hidden=true;switchButton.hidden=false;
    showMessage(Number(session?.remaining_uses||0)>0?'\u5df2\u9a8c\u8bc1':'\u6b21\u6570\u5df2\u7528\u5b8c',Number(session?.remaining_uses||0)>0?'ok':'error');
  };
  async function check(){
    try{
      const r=await fetch(base+'/api/cdk/status',{credentials:'include',cache:'no-store'});const d=await r.json();
      if(d.session){showConnected(d.session);return}
      window.cdkUsageState=null;window.dispatchEvent(new CustomEvent('cdk-usage-updated',{detail:null}));showEntry();
    }catch{showEntry('\u51ed\u8bc1\u68c0\u67e5\u5931\u8d25\uff0c\u8bf7\u91cd\u8bd5')}
  }
  async function activate(){
    const code=input.value.trim();if(!code){showMessage('\u8bf7\u8f93\u5165 CDK','error');return}
    button.disabled=true;showMessage('\u6b63\u5728\u9a8c\u8bc1\u2026');
    try{
      const r=await fetch(base+'/api/cdk/activate',{method:'POST',credentials:'include',headers:{'Content-Type':'application/json'},body:JSON.stringify({code})});
      const d=await r.json().catch(()=>({}));if(!r.ok||!d.ok)throw new Error(labels[d.error]||d.error||'CDK \u9a8c\u8bc1\u5931\u8d25');
      rememberFullCode(d.session,code);input.value='';showConnected(d.session);
    }catch(error){showMessage(error.message,'error');input.select()}finally{button.disabled=false}
  }
  const mergeLabels={CDK_MERGE_REQUIRES_TWO:'\u81f3\u5c11\u8f93\u5165 2 \u4e2a CDK',CDK_MERGE_CODE_INVALID:'\u5305\u542b\u4e0d\u6b63\u786e\u7684 CDK',CDK_MERGE_CODE_DISABLED:'\u5305\u542b\u5df2\u505c\u7528\u7684 CDK',CDK_MERGE_CODE_EXPIRED:'\u5305\u542b\u5df2\u8fc7\u671f\u7684 CDK',CDK_MERGE_CODE_IN_USE:'\u67d0\u4e2a CDK \u5b58\u5728\u6b63\u5728\u652f\u4ed8\u7684\u4efb\u52a1'};
  const showMergeMessage=(text,kind='')=>{mergeMessage.textContent=text;mergeMessage.className=('cdk-merge-message '+kind).trim()};
  async function merge(){
    const codes=mergeCodes.value.replace(/\r/g,'').split('\n').map(value=>value.trim()).filter(Boolean);
    if(codes.length<2){showMergeMessage('\u81f3\u5c11\u8f93\u5165 2 \u4e2a\u5b8c\u6574 CDK','error');return}
    mergeSubmit.disabled=true;showMergeMessage('\u6b63\u5728\u878d\u5408\u2026');
    try{
      const r=await fetch(base+'/api/cdk/merge',{method:'POST',credentials:'include',headers:{'Content-Type':'application/json'},body:JSON.stringify({codes})});
      const d=await r.json().catch(()=>({}));if(!r.ok||!d.ok)throw new Error(mergeLabels[d.error]||d.error||'CDK \u878d\u5408\u5931\u8d25');
      const item=d.item||{};mergedCode.value=item.code||'';mergedCode.hidden=false;mergeCopy.hidden=false;
      showMergeMessage('\u878d\u5408\u6210\u529f\uff1a\u603b\u6b21\u6570 '+Number(item.max_activations||0)+'\uff0c\u5df2\u4f7f\u7528 '+Number(item.activation_count||0)+'\uff0c\u5269\u4f59 '+Number(item.remaining_uses||0),'ok');
      rememberFullCode(d.session||{},item.code||'');mergeCodes.value='';showConnected(d.session||{});
    }catch(error){showMergeMessage(error.message,'error')}finally{mergeSubmit.disabled=false}
  }
  switchButton.addEventListener('click',()=>showEntry('\u8f93\u5165\u65b0 CDK \u4f1a\u66ff\u6362\u5f53\u524d\u51ed\u8bc1'));
  mergeToggle.addEventListener('click',()=>{mergeDialog.hidden=false;mergedCode.hidden=true;mergeCopy.hidden=true;showMergeMessage('');setTimeout(()=>mergeCodes.focus(),50)});
  mergeClose.addEventListener('click',()=>{mergeDialog.hidden=true});
  mergeDialog.addEventListener('click',event=>{if(event.target===mergeDialog)mergeDialog.hidden=true});
  mergeSubmit.addEventListener('click',merge);
  mergeCopy.addEventListener('click',async()=>{if(!mergedCode.value)return;await navigator.clipboard.writeText(mergedCode.value);showMergeMessage('\u65b0 CDK \u5df2\u590d\u5236','ok')});
  releaseTasksButton.addEventListener('click',async()=>{
    if(!confirm('\u786e\u5b9a\u91ca\u653e\u5f53\u524d CDK \u7684\u5168\u90e8\u63d0\u94fe\u4efb\u52a1\u5360\u4f4d\uff1f\u6b63\u5728\u8fd0\u884c\u7684\u63d0\u94fe\u4efb\u52a1\u4e5f\u4f1a\u505c\u6b62\u3002'))return;
    releaseTasksButton.disabled=true;showMessage('\u6b63\u5728\u91ca\u653e\u5f53\u524d CDK \u4efb\u52a1\u2026');
    try{
      const r=await fetch(base+'/api/card-flow/tasks/clear',{method:'POST',credentials:'include',headers:{'Content-Type':'application/json'},body:'{}'});
      const d=await r.json().catch(()=>({}));if(!r.ok||!d.ok)throw new Error(d.error||'\u91ca\u653e\u5931\u8d25');
      showMessage('\u5df2\u91ca\u653e '+Number(d.released||0)+' \u4e2a\u4efb\u52a1\u5360\u4f4d','ok');
      window.dispatchEvent(new CustomEvent('cdk-tasks-released',{detail:d}));
    }catch(error){showMessage(error.message,'error')}finally{releaseTasksButton.disabled=false}
  });
  const showTools=(mode='tasks')=>{toolsDialog.hidden=false;toolsMessage.textContent='';toolsResult.innerHTML='';if(mode==='merge')setTimeout(()=>mergeLookupInput.focus(),50)};
  const renderTaskQuery=data=>{
    const summary=data.summary||{},items=Array.isArray(data.items)?data.items:[];
    toolsResult.innerHTML='<div class="cdk-task-summary"><b>'+String(data.cdk?.code_hint||'CDK')+'</b><span>\u603b\u4efb\u52a1 '+Number(data.total||0)+'</span><span>\u8fd0\u884c '+Number(summary.running||0)+'</span><span>\u6210\u529f '+Number(summary.done||0)+'</span><span>\u5931\u8d25 '+Number(summary.error||0)+'</span><span>\u5df2\u91ca\u653e '+Number(summary.cancelled||0)+'</span></div><div class="cdk-task-list">'+items.slice(0,80).map(item=>'<div><code>'+String(item.task_id||'')+'</code><b>'+String(item.status||'')+'</b><small>'+String(item.message||item.error||'')+'</small></div>').join('')+'</div>';
  };
  taskQueryButton.addEventListener('click',()=>{showTools('tasks');queryCurrentTasks.click()});
  mergeHistoryButton.addEventListener('click',()=>showTools('merge'));
  toolsClose.addEventListener('click',()=>{toolsDialog.hidden=true});
  toolsDialog.addEventListener('click',event=>{if(event.target===toolsDialog)toolsDialog.hidden=true});
  queryCurrentTasks.addEventListener('click',async()=>{
    toolsMessage.textContent='\u6b63\u5728\u67e5\u8be2\u4efb\u52a1\u2026';toolsResult.innerHTML='';
    try{const r=await fetch(base+'/api/cdk/tasks/query',{method:'POST',credentials:'include',headers:{'Content-Type':'application/json'},body:'{}'});const d=await r.json();if(!r.ok||!d.ok)throw new Error(d.error||'\u67e5\u8be2\u5931\u8d25');renderTaskQuery(d);toolsMessage.textContent='\u67e5\u8be2\u5b8c\u6210'}catch(error){toolsMessage.textContent=error.message}
  });
  mergeLookupSubmit.addEventListener('click',async()=>{
    const code=mergeLookupInput.value.trim();if(!code){toolsMessage.textContent='\u8bf7\u8f93\u5165\u5b50 CDK';return}
    toolsMessage.textContent='\u6b63\u5728\u67e5\u627e\u878d\u5408\u8bb0\u5f55\u2026';toolsResult.innerHTML='';
    try{const r=await fetch(base+'/api/cdk/merge-lookup',{method:'POST',credentials:'include',headers:{'Content-Type':'application/json'},body:JSON.stringify({code})});const d=await r.json();if(!r.ok||!d.ok)throw new Error(d.error||'\u67e5\u8be2\u5931\u8d25');if(!d.found){toolsResult.innerHTML='<div class="cdk-empty-result">\u672a\u627e\u5230\u8be5\u5b50 CDK \u7684\u878d\u5408\u8bb0\u5f55</div>'}else{toolsResult.innerHTML='<div class="cdk-merge-chain">'+d.chain.map((item,index)=>'<div><small>\u7b2c '+(index+1)+' \u6b21\u878d\u5408</small><b>'+String(item.merged_code_hint||'')+'</b><code>'+String(item.merged_code||'')+'</code></div>').join('')+'</div><button id="copyFinalMergedCode" type="button">\u590d\u5236\u6700\u7ec8\u878d\u5408 CDK</button>';document.getElementById('copyFinalMergedCode')?.addEventListener('click',()=>navigator.clipboard.writeText(String(d.final_code||''))) }toolsMessage.textContent='\u67e5\u8be2\u5b8c\u6210'}catch(error){toolsMessage.textContent=error.message}
  });
  button.addEventListener('click',activate);
  input.addEventListener('keydown',event=>{if(event.key==='Enter')activate()});
  window.refreshCdkUsage=check;
  check();
})();
