const apiBase=document.documentElement.dataset.apiBase||'';
const cardReady=document.getElementById('cardReady');
const loadCardButton=document.getElementById('loadCardButton');
const atList=document.getElementById('atList');
const addAtButton=document.getElementById('addAtButton');
const atCount=document.getElementById('atCount');
const startButton=document.getElementById('startButton');
const stopButton=document.getElementById('stopButton');
const copyButton=document.getElementById('copyButton');
const clearButton=document.getElementById('clearButton');
const batchPayButton=document.getElementById('batchPayButton');
const selectAllPay=document.getElementById('selectAllPay');
const taskRows=document.getElementById('taskRows');
const statusBox=document.getElementById('status');
const progressText=document.getElementById('progressText');
const progressPercent=document.getElementById('progressPercent');
const progressBar=document.getElementById('progressBar');
const bindProxyPoolInput=document.getElementById('bindProxyPool');
const promoProxyPoolInput=document.getElementById('promoProxyPool');
const proxyProtocolInput=document.getElementById('proxyProtocol');
const bindProxyCount=document.getElementById('bindProxyCount');
const promoProxyCount=document.getElementById('promoProxyCount');
function selectedProxyProtocol(){return String(proxyProtocolInput?.value||'socks5h').toLowerCase()}
if(proxyProtocolInput){proxyProtocolInput.value=localStorage.getItem('cardLinkProxyProtocol')||'socks5h';proxyProtocolInput.addEventListener('change',()=>localStorage.setItem('cardLinkProxyProtocol',selectedProxyProtocol()))}
const batchConcurrencyInput=document.getElementById('batchConcurrency');
const steps=[document.getElementById('stepCard'),document.getElementById('stepAt'),document.getElementById('stepBind'),document.getElementById('stepLink')];
const cardSetupSection=document.querySelector('.card-setup-section');
const linkOnlyControl=document.createElement('label');
linkOnlyControl.className='link-only-control';
linkOnlyControl.innerHTML='<input id="linkOnlyMode" type="checkbox"><span><b>\u53ea\u63d0\u94fe\u5e76\u652f\u4ed8</b><small>\u8df3\u8fc7\u5361\u7247\u52a0\u8f7d\u548c\u7ed1\u5361\uff0c\u4f7f\u7528\u8d26\u53f7\u5df2\u6709\u652f\u4ed8\u65b9\u5f0f</small></span>';
const linkOnlyModeInput=linkOnlyControl.querySelector('input');
const mainActionRow=startButton.closest('.action-row');
mainActionRow?.parentNode?.insertBefore(linkOnlyControl,mainActionRow);
const batchImportAtButton=document.createElement('button');
batchImportAtButton.id='batchImportAtButton';batchImportAtButton.type='button';batchImportAtButton.className='secondary small';batchImportAtButton.textContent='\u6279\u91cf\u5bfc\u5165 AT';
addAtButton.parentNode?.insertBefore(batchImportAtButton,addAtButton);
const atImportDialog=document.createElement('section');
atImportDialog.className='at-import-dialog';atImportDialog.hidden=true;
atImportDialog.innerHTML='<div class="at-import-card"><div class="at-import-title"><div><b>\u6279\u91cf\u667a\u80fd\u5bfc\u5165 AT</b><small>\u652f\u6301 JWT\u3001Session JSON\u3001\u6df7\u5408\u6587\u672c\u548c\u8d26\u53f7\u5bfc\u51fa\u683c\u5f0f</small></div><button class="at-import-close" type="button">\u5173\u95ed</button></div><textarea class="at-import-input" spellcheck="false" placeholder="\u7c98\u8d34\u5305\u542b AT \u7684\u6587\u672c\u6216 JSON\u2026"></textarea><div class="at-import-actions"><button class="at-import-submit" type="button">\u8bc6\u522b\u5e76\u5bfc\u5165</button><button class="at-import-clear secondary" type="button">\u6e05\u7a7a</button></div><div class="at-import-message"></div></div>';
document.body.appendChild(atImportDialog);
const atImportInput=atImportDialog.querySelector('.at-import-input'),atImportSubmit=atImportDialog.querySelector('.at-import-submit'),atImportMessage=atImportDialog.querySelector('.at-import-message');
const exportCsvButton=document.createElement('button');
exportCsvButton.id='exportCsvButton';exportCsvButton.type='button';exportCsvButton.className='secondary small';exportCsvButton.textContent='\u5bfc\u51fa CSV';exportCsvButton.disabled=true;
copyButton.parentNode?.insertBefore(exportCsvButton,copyButton);

let stripe=null;
let stripeKey='';
let cardFieldsMounted=false;
const prefetchedSessions=new Map();
let cardNumberElement=null,cardExpiryElement=null,cardCvcElement=null;
const cardComplete={number:false,expiry:false,cvc:false};
let cardIsReady=false;
let stopped=false;
let running=false;
let proxyCursor=0;
const results=[];
const checkoutTaskStorageKey='cardLinkCheckoutTaskIds';
function storedCheckoutTaskIds(){
  try{return JSON.parse(localStorage.getItem(checkoutTaskStorageKey)||'[]').filter(value=>/^[a-f0-9]{12}$/.test(String(value||'')))}catch{return[]}
}
function rememberCheckoutTaskId(taskId){
  const value=String(taskId||'').trim().toLowerCase();if(!/^[a-f0-9]{12}$/.test(value))return;
  const items=[value,...storedCheckoutTaskIds().filter(item=>item!==value)].slice(0,300);
  localStorage.setItem(checkoutTaskStorageKey,JSON.stringify(items));
}
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));

function status(text,kind=''){statusBox.textContent=text;statusBox.className=('status '+kind).trim()}
function setStep(index){steps.forEach((el,i)=>{el.classList.toggle('active',i===index);el.classList.toggle('done',i<index)})}
function setProgress(done,total,text){const percent=total?Math.round(done*100/total):0;progressText.textContent=text||'';progressPercent.textContent=percent+'%';progressBar.style.width=percent+'%'}
function tokenLabel(token,index){return 'AT '+(index+1)+' - '+token.slice(0,12)+'...'+token.slice(-8)}
function tokenAccountLabel(token,index){
  try{
    const raw=token.split('.')[1].replace(/-/g,'+').replace(/_/g,'/');
    const claims=JSON.parse(decodeURIComponent(Array.from(atob(raw+'='.repeat((4-raw.length%4)%4))).map(ch=>'%'+ch.charCodeAt(0).toString(16).padStart(2,'0')).join('')));
    const profile=claims['https://api.openai.com/profile']||{};
    return String(profile.email||claims.email||tokenLabel(token,index));
  }catch{return tokenLabel(token,index)}
}
function findJsonAccessToken(value){
  if(!value||typeof value!=='object')return'';
  if(Array.isArray(value)){
    for(const item of value){const found=findJsonAccessToken(item);if(found)return found}
    return'';
  }
  const preferred=['accessToken','access_token','access-token','at'];
  for(const key of preferred){
    const token=value[key];
    if(typeof token==='string'&&token.split('.').length===3&&token.length>300)return token.trim();
  }
  for(const [key,item] of Object.entries(value)){
    if(String(key).toLowerCase().includes('sessiontoken'))continue;
    const found=findJsonAccessToken(item);
    if(found)return found;
  }
  return'';
}
function extractToken(raw){
  const text=String(raw||'').trim();
  if(!text)return'';
  if(text.startsWith('{')||text.startsWith('[')){
    try{
      const token=findJsonAccessToken(JSON.parse(text));
      if(token)return token;
    }catch{}
  }
  const named=text.match(/["']access_?token["']\s*:\s*["']([^"']+)["']/i);
  if(named&&named[1])return named[1].trim();
  const jwtMatches=text.match(/eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/g)||[];
  if(jwtMatches.length)return jwtMatches.sort((a,b)=>b.length-a.length)[0];
  const candidates=text.split(/[\s|,;]+/).filter(value=>value.length>300&&value.split('.').length===3);
  return(candidates.sort((a,b)=>b.length-a.length)[0]||text).trim();
}
function extractTokensSmart(raw){
  const text=String(raw||'').trim(),found=[],seen=new Set();
  const add=value=>{const token=String(value||'').trim();if(token.startsWith('eyJ')&&token.split('.').length===3&&token.length>300&&!seen.has(token)){seen.add(token);found.push(token)}};
  const walk=value=>{
    if(typeof value==='string'){for(const match of value.match(/eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/g)||[])add(match);return}
    if(Array.isArray(value)){value.forEach(walk);return}
    if(value&&typeof value==='object')Object.values(value).forEach(walk);
  };
  if(text.startsWith('{')||text.startsWith('[')){try{walk(JSON.parse(text))}catch{}}
  walk(text);
  for(const line of text.replace(/\r/g,'').split('\n')){
    const token=extractToken(line);if(token.startsWith('eyJ'))add(token);
  }
  return found;
}
function renumberAtCards(){
  const cards=[...atList.querySelectorAll('.at-entry')];
  cards.forEach((card,index)=>{
    card.querySelector('.at-entry-index').textContent='AT '+String(index+1).padStart(2,'0');
    card.querySelector('.remove-at-button').disabled=cards.length===1||running;
  });
  atCount.textContent=cards.length+' AT';
}
function cdkAtLimit(){
  const value=Number(window.cdkUsageState?.remaining_uses);
  return Number.isFinite(value)&&value>=0?value:0;
}
function addAtField(value=''){
  const limit=cdkAtLimit();
  if(atList.children.length>=limit){status('\u5f53\u524d CDK \u6700\u591a\u53ef\u6dfb\u52a0 '+limit+' \u4e2a AT\u3002','error');return null}
  const card=document.createElement('article');
  card.className='at-entry';
  card.innerHTML='<span class="at-entry-index"></span><input class="at-item-input" type="text" spellcheck="false" autocomplete="off" placeholder="&#31896;&#36148;&#19968;&#26465; AT &#25110; accessToken JSON"><button class="remove-at-button" type="button">&#21024;&#38500;</button>';
  const input=card.querySelector('.at-item-input');
  input.value=value;
  input.addEventListener('input',updateCount);
  input.addEventListener('paste',event=>{
    const pasted=event.clipboardData?.getData('text')||'';
    const trimmed=pasted.trim();
    if(!trimmed)return;
    if(trimmed.startsWith('{')||trimmed.startsWith('[')){
      const token=extractToken(trimmed);
      if(token.length>300&&token.includes('.')){event.preventDefault();input.value=token;updateCount();return}
    }
    const tokens=trimmed.replace(/\r/g,'').split('\n').map(line=>extractToken(line)).filter(token=>token.length>300&&token.includes('.'));
    if(tokens.length>1){
      event.preventDefault();
      const available=Math.max(0,cdkAtLimit()-parseTokens().length);
      const accepted=tokens.slice(0,available);
      if(accepted.length){input.value=accepted[0];accepted.slice(1).forEach(token=>addAtField(token))}
      if(accepted.length<tokens.length)status('\u5df2\u6309 CDK \u5269\u4f59\u6b21\u6570\u622a\u53d6\uff0c\u672c\u6b21\u4ec5\u4fdd\u7559 '+accepted.length+' \u4e2a AT\u3002','error');
      updateCount();
    }
  });
  card.querySelector('.remove-at-button').addEventListener('click',()=>{if(running)return;card.remove();if(!atList.children.length)addAtField();renumberAtCards();updateCount()});
  atList.appendChild(card);renumberAtCards();updateCount();input.focus();return card;
}
function parseTokens(){
  const seen=new Set(),items=[];
  for(const input of atList.querySelectorAll('.at-item-input')){
    const token=extractToken(input.value);
    if(token.length<300||!token.includes('.')||seen.has(token))continue;
    seen.add(token);items.push(token);
  }
  return items;
}
function parseProxyInput(input){
  const seen=new Set(),items=[];
  for(const line of String(input?.value||'').replace(/\r/g,'').split('\n')){
    const value=line.trim();if(!value||seen.has(value))continue;
    seen.add(value);items.push(value);
    if(items.length>=500)break;
  }
  return items;
}
function parseBindProxyPool(){return parseProxyInput(bindProxyPoolInput)}
function parsePromoProxyPool(){return parseProxyInput(promoProxyPoolInput)}
function nextProxy(){
  const pool=parseBindProxyPool();
  if(!pool.length)throw new Error('Please provide at least one US binding proxy');
  const value=pool[proxyCursor%pool.length];proxyCursor=(proxyCursor+1)%Math.max(1,pool.length);return value;
}
function updateCount(){
  const valid=parseTokens().length;
  const total=atList.querySelectorAll('.at-entry').length;
  const bindProxies=parseBindProxyPool().length;
  const promoProxies=parsePromoProxyPool().length;
  atCount.textContent=valid+' / '+total+' AT';
  bindProxyCount.textContent=bindProxies+' PROXY';
  promoProxyCount.textContent=promoProxies+' PROXY';
  if(!running){
    const linkOnly=Boolean(linkOnlyModeInput?.checked);
    const atLimit=cdkAtLimit();
    addAtButton.disabled=total>=atLimit;
    batchImportAtButton.disabled=atLimit<=0||valid>=atLimit;
    loadCardButton.disabled=linkOnly||cardFieldsMounted||!(valid&&bindProxies);
    loadCardButton.textContent=cardFieldsMounted?'\u5361\u7247\u8f93\u5165\u6846\u5df2\u52a0\u8f7d':'\u52a0\u8f7d\u5b89\u5168\u5361\u7247\u8f93\u5165\u6846';
    const overLimit=valid>atLimit;
    startButton.disabled=overLimit||(linkOnly?!(valid&&bindProxies&&promoProxies):!(cardFieldsMounted&&cardIsReady&&valid&&bindProxies&&promoProxies));
    startButton.textContent=linkOnly?'\u5f00\u59cb\u53ea\u63d0\u94fe\u5e76\u652f\u4ed8':'\u5f00\u59cb\u6279\u91cf\u7ed1\u5361\u5e76\u63d0\u94fe';
  }
}

async function api(url,options={}){
  let response;
  try{
    response=await fetch(url,{credentials:'include',...options});
  }catch(cause){
    const error=new Error(cause?.message||'Network request failed');
    error.httpStatus=0;error.networkError=true;throw error;
  }
  const data=await response.json().catch(()=>({}));
  if(!response.ok||data.error||data.ok===false){
    const message=['AT_INVALID_OR_EXPIRED','AT_INVALIDATED_OR_EXPIRED'].includes(data.error)?'AT was invalidated by the server or rotated; use the latest AT and retry':(data.error==='CDK_REQUIRED'?'请先在页面左上角输入有效 CDK':(data.error==='CDK_AT_LIMIT'?(data.message||'AT 数量已达到 CDK 剩余次数上限'):(data.error||('HTTP '+response.status))));
    const error=new Error(message);
    error.httpStatus=response.status;
    error.upstreamStatus=Number(data.status||0);
    throw error;
  }
  return data;
}
function retryableNetworkError(error){
  const http=Number(error?.httpStatus||0);
  const upstream=Number(error?.upstreamStatus||0);
  const message=String(error?.message||'').toLowerCase();
  if(error?.networkError)return true;
  if([502,503,504].includes(http)&&(!upstream||upstream>=500||upstream===403))return true;
  return /network|timeout|timed out|connection|transport|tls|ssl|empty reply|temporar|upstream/.test(message);
}
async function apiWithRetry(url,options={},attempts=3,onRetry=null){
  let lastError=null;
  for(let attempt=1;attempt<=attempts;attempt++){
    try{return await api(url,options)}
    catch(error){
      lastError=error;
      if(!retryableNetworkError(error)||attempt>=attempts)throw error;
      if(onRetry)onRetry(attempt,attempts,error);
      await sleep(1000*attempt);
    }
  }
  throw lastError||new Error('Network request failed');
}
async function apiWithProxyRetry(url,baseBody,attempts=3,onRetry=null){
  let lastError=null;
  for(let attempt=1;attempt<=attempts;attempt++){
    const proxy=nextProxy();
    const body={...baseBody,proxy,proxy_pool:parseBindProxyPool()};
    try{return await api(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})}
    catch(error){
      lastError=error;
      if(!retryableNetworkError(error)||attempt>=attempts)throw error;
      if(onRetry)onRetry(attempt,attempts,error);
      await sleep(1000*attempt);
    }
  }
  throw lastError||new Error('Proxy request failed');
}
function findLink(value){
  if(!value)return'';
  if(typeof value==='string'&&/^https:\/\/chatgpt\.com\/checkout\//.test(value))return value;
  if(Array.isArray(value)){for(const item of value){const found=findLink(item);if(found)return found}}
  else if(typeof value==='object'){for(const item of Object.values(value)){const found=findLink(item);if(found)return found}}
  return'';
}
function addRow(id,label){
  const row=document.createElement('div');row.className='task-row';row.id=id;
  row._createdAt=Date.now();
  row.innerHTML='<div class="row-account-wrap"><small>&#36134;&#21495;</small><span class="row-account"></span></div><div class="row-stage">Waiting</div><div class="row-link-wrap"><small>&#38142;&#25509;</small><a class="row-link" target="_blank" rel="noopener"></a><span class="row-message"></span></div><div class="row-actions"><label class="select-pay-label" hidden><input class="select-pay-checkbox" type="checkbox"><span>选择支付</span></label><button class="copy-one-button" type="button" hidden>&#22797;&#21046;&#38142;&#25509;</button><button class="pay-one-button" type="button" hidden>直接协议支付</button><button class="retry-one-button" type="button" hidden disabled>&#37325;&#35797;&#35813; AT</button></div><div class="row-payment" hidden></div>';
  row.querySelector('.row-account').textContent=label;
  const copy=row.querySelector('.copy-one-button');
  copy.addEventListener('click',async()=>{
    const link=row.querySelector('.row-link').href;
    if(!link)return;
    await navigator.clipboard.writeText(link);
    copy.textContent='Copied';
    setTimeout(()=>copy.innerHTML='&#22797;&#21046;&#38142;&#25509;',1200);
  });
  const selectPay=row.querySelector('.select-pay-checkbox');
  selectPay.addEventListener('change',()=>{row.classList.toggle('selected-for-pay',selectPay.checked);updateBatchPayState()});
  const pay=row.querySelector('.pay-one-button');
  pay.addEventListener('click',()=>startProtocolPayment(row).catch(()=>{}));
  const retry=row.querySelector('.retry-one-button');
  retry.addEventListener('click',()=>retrySingleAt(row));
  taskRows.appendChild(row);return row;
}
function updateRow(row,stage,kind='',result=''){
  row.className=('task-row '+kind).trim();
  if(kind==='success'||kind==='failed')row._completedAt=Date.now();
  row.querySelector('.row-stage').textContent=stage;
  const link=row.querySelector('.row-link');
  const message=row.querySelector('.row-message');
  const copy=row.querySelector('.copy-one-button');
  const retry=row.querySelector('.retry-one-button');
  const pay=row.querySelector('.pay-one-button');
  const selectLabel=row.querySelector('.select-pay-label');
  const selectPay=row.querySelector('.select-pay-checkbox');
  if(/^https:\/\//.test(result||'')){
    row._link=result;link.href=result;link.textContent=result;link.hidden=false;
    message.textContent='';copy.hidden=false;pay.hidden=false;pay.disabled=Boolean(row._paymentDone||row._paymentRunning);retry.hidden=true;
    selectLabel.hidden=false;selectPay.disabled=Boolean(row._paymentDone||row._paymentRunning);
  }else{
    link.removeAttribute('href');link.textContent='';link.hidden=true;
    message.textContent=result||'';copy.hidden=true;if(pay)pay.hidden=true;if(selectLabel)selectLabel.hidden=true;if(selectPay){selectPay.checked=false;selectPay.disabled=false}row.classList.remove('selected-for-pay');retry.hidden=kind!=='failed'||!row._token;retry.disabled=Boolean(row._retrying);updateBatchPayState();
  }
}
function updateBatchPayState(){
  const eligible=[...taskRows.querySelectorAll('.select-pay-checkbox:not(:disabled)')];
  const selected=eligible.filter(item=>item.checked);
  batchPayButton.disabled=!selected.length;
  batchPayButton.textContent='支付已选（'+selected.length+'）';
  selectAllPay.checked=Boolean(eligible.length)&&selected.length===eligible.length;
  selectAllPay.indeterminate=selected.length>0&&selected.length<eligible.length;
  exportCsvButton.disabled=!taskRows.querySelector('.task-row');
}
function protocolFailureText(value){
  const raw=String(value||'\u534f\u8bae\u652f\u4ed8\u5931\u8d25').trim();
  const lower=raw.toLowerCase();
  if(/openai_confirm_blocked|result\s*[:=]\s*['\"]?blocked|\bblock(?:ed)?\b/.test(lower)){
    return '\u652f\u4ed8\u5931\u8d25 \u00b7 \u539f\u56e0\uff1aBlock \u00b7 \u8bf4\u660e\uff1a\u8d26\u53f7\u95ee\u9898';
  }
  return '\u534f\u8bae\u652f\u4ed8\u5931\u8d25\uff1a'+raw;
}
function setPaymentState(row,text,kind=''){
  const box=row?.querySelector('.row-payment');if(!box)return;
  box.hidden=false;box.textContent=text;box.className=('row-payment '+kind).trim();
  if(kind==='success'||kind==='failed'||kind==='warning')row._completedAt=Date.now();
}
async function pollProtocolPayment(jobId,row){
  for(let i=0;i<600;i++){
    const data=await apiWithRetry(apiBase+'/api/protocol-pay/jobs/'+jobId,{cache:'no-store'},3);
    const job=data.job||{};
    setPaymentState(row,(job.stage||'协议支付中')+' · '+Number(job.progress||0)+'%','running');
    if(job.status==='ready'){row._paymentDone=true;setPaymentState(row,'协议支付完成','success');return job}
    if(job.status==='verification_required'){row._paymentDone=true;setPaymentState(row,'支付需要额外验证','warning');return job}
    if(['error','cancelled'].includes(job.status)){throw new Error(job.error||job.message||'协议支付失败')}
    await sleep(1200);
  }
  throw new Error('协议支付状态查询超时');
}
async function startProtocolPayment(row){
  if(!row?._token||!row?._link||row._paymentRunning||row._paymentDone)return;
  const button=row.querySelector('.pay-one-button');
  const checkbox=row.querySelector('.select-pay-checkbox');
  row._paymentRunning=true;button.disabled=true;checkbox.disabled=true;
  try{
    setPaymentState(row,'\u6b63\u5728\u4f7f\u7528\u4ee3\u7406\u6c60 1 \u63d0\u4ea4\u534f\u8bae\u652f\u4ed8','running');
    const data=await apiWithRetry(apiBase+'/api/protocol-pay/jobs',{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({access_token:row._token,checkout_url:row._link,defer_confirm:false,proxy_pool:parseBindProxyPool(),proxy_protocol:selectedProxyProtocol()})
    },3);
    row._payJobId=data.job?.id||'';
    if(!row._payJobId)throw new Error('协议支付任务未返回 ID');
    const result=await pollProtocolPayment(row._payJobId,row);
    if(typeof window.refreshCdkUsage==='function')setTimeout(window.refreshCdkUsage,600);
    checkbox.checked=false;row.classList.remove('selected-for-pay');
    button.textContent=row._paymentDone?'支付已提交':'直接协议支付';
    return result;
  }catch(error){
    setPaymentState(row,protocolFailureText(error.message),'failed');button.disabled=false;checkbox.disabled=false;throw error;
  }finally{
    row._paymentRunning=false;
    if(row._paymentDone){button.disabled=true;checkbox.disabled=true;checkbox.checked=false;row.classList.remove('selected-for-pay')}
    updateBatchPayState();
  }
}
async function createPreparedProtocolPayment(row){
  if(!row?._token||!row?._link||row._paymentRunning||row._paymentDone)throw new Error('\u4efb\u52a1\u4e0d\u53ef\u7528');
  const button=row.querySelector('.pay-one-button');
  const checkbox=row.querySelector('.select-pay-checkbox');
  row._paymentRunning=true;button.disabled=true;checkbox.disabled=true;
  setPaymentState(row,'\u6b63\u5728\u5e76\u53d1\u51c6\u5907\u652f\u4ed8\u4e0a\u4e0b\u6587','running');
  try{
    const data=await apiWithRetry(apiBase+'/api/protocol-pay/jobs',{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({access_token:row._token,checkout_url:row._link,defer_confirm:true,proxy_pool:parseBindProxyPool(),proxy_protocol:selectedProxyProtocol()})
    },3);
    const jobId=data.job?.id||'';
    if(!jobId)throw new Error('\u534f\u8bae\u652f\u4ed8\u4efb\u52a1\u672a\u8fd4\u56de ID');
    row._payJobId=jobId;
    return jobId;
  }catch(error){
    row._paymentRunning=false;button.disabled=false;checkbox.disabled=false;
    setPaymentState(row,protocolFailureText(error.message),'failed');throw error;
  }
}
async function waitProtocolPrepared(jobId,row){
  for(let i=0;i<600;i++){
    const data=await apiWithRetry(apiBase+'/api/protocol-pay/jobs/'+jobId,{cache:'no-store'},3);
    const job=data.job||{};
    if(job.status==='prepared'){setPaymentState(row,'\u51c6\u5907\u5b8c\u6210 \u00b7 \u7b49\u5f85\u5168\u90e8\u4efb\u52a1\u7edf\u4e00\u8ba2\u9605','warning');return job}
    if(['error','cancelled'].includes(job.status))throw new Error(job.error||job.message||'\u652f\u4ed8\u51c6\u5907\u5931\u8d25');
    setPaymentState(row,(job.stage||'\u6b63\u5728\u51c6\u5907')+' \u00b7 '+Number(job.progress||0)+'%','running');
    await sleep(1000);
  }
  throw new Error('\u652f\u4ed8\u51c6\u5907\u8d85\u65f6');
}
function finishBatchPaymentRow(row,success){
  const button=row.querySelector('.pay-one-button');
  const checkbox=row.querySelector('.select-pay-checkbox');
  row._paymentRunning=false;checkbox.checked=false;row.classList.remove('selected-for-pay');
  if(success||row._paymentDone){button.disabled=true;checkbox.disabled=true;button.textContent='\u652f\u4ed8\u5df2\u63d0\u4ea4'}
  else{button.disabled=false;checkbox.disabled=false}
}
async function paySelectedRows(){
  const rows=[...taskRows.querySelectorAll('.select-pay-checkbox:checked')].map(item=>item.closest('.task-row')).filter(Boolean);
  if(!rows.length)return;
  if(rows.length>50){status('\u4e00\u6b21\u6700\u591a\u9009\u62e9 50 \u4e2a\u652f\u4ed8\u4efb\u52a1\u3002','error');return}
  batchPayButton.disabled=true;selectAllPay.disabled=true;
  status('\u6b63\u5728\u540c\u65f6\u51c6\u5907 '+rows.length+' \u4e2a\u652f\u4ed8\u4efb\u52a1\u2026');
  let prepareFailed=0,success=0,finalFailed=0;
  const preparedResults=await Promise.all(rows.map(async row=>{
    try{
      const jobId=await createPreparedProtocolPayment(row);
      await waitProtocolPrepared(jobId,row);
      return{row,jobId};
    }catch(error){
      prepareFailed++;setPaymentState(row,protocolFailureText(error.message),'failed');finishBatchPaymentRow(row,false);return null;
    }
  }));
  const prepared=preparedResults.filter(Boolean);
  if(!prepared.length){
    selectAllPay.disabled=false;updateBatchPayState();status('\u6279\u91cf\u652f\u4ed8\u51c6\u5907\u5168\u90e8\u5931\u8d25\u3002','error');return;
  }
  status(prepared.length+' \u4e2a\u4efb\u52a1\u5df2\u5168\u90e8\u51c6\u5907\u5b8c\u6210\uff0c\u6b63\u5728\u7edf\u4e00\u653e\u884c\u8ba2\u9605\u2026');
  prepared.forEach(item=>setPaymentState(item.row,'\u5df2\u7edf\u4e00\u653e\u884c \u00b7 \u6b63\u5728\u540c\u65f6\u8ba2\u9605','running'));
  try{
    await apiWithRetry(apiBase+'/api/protocol-pay/batch-confirm',{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({job_ids:prepared.map(item=>item.jobId),burst_count:1})
    },3);
  }catch(error){
    prepared.forEach(item=>{setPaymentState(item.row,protocolFailureText(error.message),'failed');finishBatchPaymentRow(item.row,false)});
    selectAllPay.disabled=false;updateBatchPayState();status('\u7edf\u4e00\u8ba2\u9605\u653e\u884c\u5931\u8d25\uff1a'+error.message,'error');return;
  }
  await Promise.all(prepared.map(async item=>{
    try{await pollProtocolPayment(item.jobId,item.row);success++;finishBatchPaymentRow(item.row,true)}
    catch(error){finalFailed++;setPaymentState(item.row,protocolFailureText(error.message),'failed');finishBatchPaymentRow(item.row,false)}
  }));
  if(typeof window.refreshCdkUsage==='function')setTimeout(window.refreshCdkUsage,800);
  selectAllPay.disabled=false;updateBatchPayState();
  const totalFailed=prepareFailed+finalFailed;
  status('\u6279\u91cf\u652f\u4ed8\u5b8c\u6210\uff1a'+success+' \u4e2a\u6210\u529f\uff0c'+totalFailed+' \u4e2a\u5931\u8d25\u3002',totalFailed?'error':'ok');
}

function refreshOutput(){}

function csvCell(value){
  let text=String(value??'').replace(/\r?\n/g,' ');
  if(/^[=+\-@]/.test(text))text="'"+text;
  return '"'+text.replace(/"/g,'""')+'"';
}
function csvTime(timestamp){
  const date=new Date(Number(timestamp||Date.now()));
  const pad=value=>String(value).padStart(2,'0');
  return date.getFullYear()+'-'+pad(date.getMonth()+1)+'-'+pad(date.getDate())+' '+pad(date.getHours())+':'+pad(date.getMinutes())+':'+pad(date.getSeconds());
}
function exportTaskCsv(){
  const rows=[...taskRows.querySelectorAll('.task-row')];
  if(!rows.length){status('\u6682\u65e0\u53ef\u5bfc\u51fa\u7684\u4efb\u52a1\u3002','error');return}
  const records=rows.map((row,index)=>{
    const email=row.querySelector('.row-account')?.textContent?.trim()||'';
    const stage=row.querySelector('.row-stage')?.textContent?.trim()||'\u672a\u77e5';
    const payment=row.querySelector('.row-payment');
    const paymentText=payment&&!payment.hidden?payment.textContent.trim():'';
    const link=row._link||row.querySelector('.row-link')?.href||'';
    const result=[stage,paymentText,link].filter(Boolean).join(' | ');
    return[index+1,csvTime(row._completedAt||row._createdAt),email,row._token||'',result];
  });
  const csv='\ufeff'+[['\u5e8f\u53f7','\u65f6\u95f4','\u90ae\u7bb1','AT','\u4efb\u52a1\u5b8c\u6210\u7ed3\u679c'],...records].map(record=>record.map(csvCell).join(',')).join('\r\n');
  const blob=new Blob([csv],{type:'text/csv;charset=utf-8'}),url=URL.createObjectURL(blob),anchor=document.createElement('a');
  anchor.href=url;anchor.download='card-link-results-'+new Date().toISOString().replace(/[:T]/g,'-').slice(0,19)+'.csv';document.body.appendChild(anchor);anchor.click();anchor.remove();URL.revokeObjectURL(url);
  status('\u5df2\u5bfc\u51fa '+records.length+' \u6761\u4efb\u52a1\u8bb0\u5f55\u3002','ok');
}

async function pollCheckout(taskId,onProgress=null){
  for(let i=0;i<180;i++){
    if(stopped)throw new Error('任务已停止');
    const task=await apiWithRetry(apiBase+'/api/card-flow/task/'+taskId,{},3);
    if(onProgress)onProgress(task);
    if(task.status==='done'){
      const link=findLink(task.result||task);
      if(!link)throw new Error('Checkout task completed without a link');
      return link;
    }
    if(['error','failed','cancelled'].includes(task.status))throw new Error(task.error||task.message||'提链失败');
    await sleep(2000);
  }
  throw new Error('提链超时');
}
async function mountCardFields(publishableKey){
  if(typeof window.Stripe!=='function')throw new Error('Stripe \u5b89\u5168\u7ec4\u4ef6\u52a0\u8f7d\u5931\u8d25\uff0c\u8bf7\u5237\u65b0\u9875\u9762\u540e\u91cd\u8bd5');
  if(cardNumberElement){try{cardNumberElement.destroy()}catch{}}
  if(cardExpiryElement){try{cardExpiryElement.destroy()}catch{}}
  if(cardCvcElement){try{cardCvcElement.destroy()}catch{}}
  for(const key of Object.keys(cardComplete))cardComplete[key]=false;
  cardIsReady=false;updateCount();
  stripeKey=publishableKey;
  cardFieldsMounted=true;
  stripe=Stripe(publishableKey);
  const elements=stripe.elements();
  const style={
    base:{fontSize:'17px',fontFamily:'"Microsoft YaHei","PingFang SC","Segoe UI",system-ui,sans-serif',color:'#f4f8ff',iconColor:'#58d9b2','::placeholder':{color:'#6f8299'}},
    invalid:{color:'#ff7388',iconColor:'#ff7388'}
  };
  cardNumberElement=elements.create('cardNumber',{showIcon:true,style});
  cardExpiryElement=elements.create('cardExpiry',{style});
  cardCvcElement=elements.create('cardCvc',{style});
  const entries=[['number',cardNumberElement,'#cardNumberElement'],['expiry',cardExpiryElement,'#cardExpiryElement'],['cvc',cardCvcElement,'#cardCvcElement']];
  let readyCount=0;
  const refreshCardState=()=>{
    cardIsReady=cardComplete.number&&cardComplete.expiry&&cardComplete.cvc;
    cardReady.textContent=cardIsReady?'Card information is complete':'Please complete card number, expiry and CVC';
    cardReady.className='inline-state '+(cardIsReady?'ok':'');updateCount();
  };
  for(const [name,element,selector] of entries){
    document.querySelector(selector).innerHTML='';
    element.on('ready',()=>{readyCount++;if(readyCount===3){cardReady.textContent='Card fields are ready';cardReady.className='inline-state ok'}});
    element.on('change',event=>{cardComplete[name]=Boolean(event.complete);const host=document.querySelector(selector);host.classList.toggle('field-complete',Boolean(event.complete));host.classList.toggle('field-invalid',Boolean(event.error));if(event.error){cardReady.textContent=event.error.message;cardReady.className='inline-state error'}else refreshCardState()});
    element.mount(selector);
  }
}
async function init(){
  cardReady.textContent='\u8bf7\u5148\u586b\u5199 AT \u548c\u4ee3\u7406\u6c60\uff0c\u518d\u70b9\u51fb\u201c\u52a0\u8f7d\u5b89\u5168\u5361\u7247\u8f93\u5165\u6846\u201d';
  cardReady.className='inline-state';
  status('\u586b\u5199 AT \u4e0e\u4ee3\u7406\u6c60\u540e\uff0c\u5148\u52a0\u8f7d\u5361\u7247\u8f93\u5165\u6846\uff0c\u518d\u8f93\u5165\u5361\u53f7\u3001\u6709\u6548\u671f\u548c CVC\u3002');
  updateCount();
}
async function resolvePendingKey(session){
  if(!session||!session.pending)return session;
  const probeId=String(session.key_probe_id||'');
  if(!probeId)throw new Error('Stripe key probe ID is missing');
  for(let i=0;i<360;i++){
    const data=await apiWithRetry(apiBase+'/api/card-bind/key-probe/'+probeId,{},3);
    const probe=data.probe||{};
    status('Resolving Stripe key via temporary Checkout: '+Number(probe.progress||0)+'%');
    if(probe.status==='done'){
      const refreshed=(probe.session&&typeof probe.session==='object')?probe.session:{};
      const key=String(refreshed.publishable_key||probe.publishable_key||'');
      if(!key.startsWith('pk_'))throw new Error('Checkout initialization completed without a Stripe key');
      if(!String(refreshed.client_secret||'').startsWith('seti_'))throw new Error('Checkout initialized, but the refreshed SetupIntent is missing');
      return {...session,...refreshed,pending:false,publishable_key:key,publishable_key_source:'checkout_then_refetch'};
    }
    if(probe.status==='error')throw new Error(probe.error||probe.message||'Stripe key resolution failed');
    await sleep(2000);
  }
  throw new Error('Stripe key resolution timed out');
}
async function loadCardFieldsFromAt(token){
  status('Reading AT and resolving the Stripe account...');
  let session=await apiWithProxyRetry(apiBase+'/api/card-bind/session',{access_token:token,proxy_protocol:selectedProxyProtocol()},3,(attempt,total)=>status('Resolving Stripe account, proxy retry '+attempt+' / '+total));
  session=await resolvePendingKey(session);
  const key=String(session.publishable_key||'');
  if(!key.startsWith('pk_'))throw new Error('The AT/Checkout response did not contain a Stripe publishable key');
  prefetchedSessions.set(token,session);
  await mountCardFields(key);
  status(session.publishable_key_source==='checkout_protocol_fallback'?'AT response had no key; one Checkout was created and the card fields are now ready.':'Stripe key was resolved directly from the AT response.','ok');
}
async function prepareOne(token,index,total,existingRow=null){
  const row=existingRow||addRow('task-'+Date.now()+'-'+index,tokenLabel(token,index));
  row._token=token;row.dataset.atIndex=String(index);
  row.querySelector('.row-account').textContent=tokenLabel(token,index);
  updateRow(row,'Reading AT and creating SetupIntent','running');
  let session=prefetchedSessions.get(token)||null;
  if(session)prefetchedSessions.delete(token);
  if(!session){
    session=await apiWithProxyRetry(apiBase+'/api/card-bind/session',{access_token:token,proxy_protocol:selectedProxyProtocol()},3,(attempt,total)=>updateRow(row,'Proxy retry '+attempt+' / '+total,'running'));
    session=await resolvePendingKey(session);
  }
  const sessionKey=String(session.publishable_key||'');
  if(sessionKey&&sessionKey!==stripeKey){
    prefetchedSessions.set(token,session);
    await mountCardFields(sessionKey);
    stopped=true;
    const error=new Error('Stripe account changed. Card fields were refreshed with the Checkout key; re-enter the card and retry this AT.');
    error.cardReentryRequired=true;
    throw error;
  }
  const account=session.account_email||('account-'+(index+1));
  row.querySelector('.row-account').textContent=account;
  if(!session.billing_details)throw new Error('Automatic US billing details are missing');

  updateRow(row,'Binding the card','running');
  const confirmation=await stripe.confirmCardSetup(
    session.client_secret,
    {payment_method:{card:cardNumberElement,billing_details:session.billing_details}}
  );
  if(confirmation.error)throw new Error(confirmation.error.message||'绑卡失败');
  if(!confirmation.setupIntent||confirmation.setupIntent.status!=='succeeded')throw new Error('绑卡状态：'+(confirmation.setupIntent?.status||'unknown'));
  const pm=typeof confirmation.setupIntent.payment_method==='string'?confirmation.setupIntent.payment_method:confirmation.setupIntent.payment_method?.id;
  if(!pm)throw new Error('Stripe 未返回 PaymentMethod');

  updateRow(row,'Attaching the card to the account','running');
  await apiWithProxyRetry(apiBase+'/api/card-bind/default',{payment_method_id:pm,record_id:session.record_id,access_token:token,proxy_protocol:selectedProxyProtocol()},3,(attempt,total)=>updateRow(row,'Attach proxy retry '+attempt+' / '+total,'running'));

  updateRow(row,'Checkout queued','running');
  const created=await apiWithRetry(apiBase+'/api/card-flow/quick-checkout',{
    method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({record_id:session.record_id,access_token:token,entry_proxy_pool:parseBindProxyPool(),exit_proxy_pool:parsePromoProxyPool(),proxy_protocol:selectedProxyProtocol()})
  },3,(attempt,total)=>updateRow(row,'Checkout retry '+attempt+' / '+total,'running'));
  row._taskId=created.task_id;rememberCheckoutTaskId(created.task_id);
  return{account,row,taskId:created.task_id};
}
async function prepareLinkOnly(token,index,total,existingRow=null){
  const row=existingRow||addRow('task-'+Date.now()+'-'+index,tokenLabel(token,index));
  const account=tokenAccountLabel(token,index);
  row._token=token;row._linkOnly=true;row.dataset.atIndex=String(index);
  row.querySelector('.row-account').textContent=account;
  updateRow(row,'\u6b63\u5728\u76f4\u63a5\u521b\u5efa Checkout','running');
  const created=await apiWithRetry(apiBase+'/api/card-flow/quick-checkout',{
    method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({access_token:token,entry_proxy_pool:parseBindProxyPool(),exit_proxy_pool:parsePromoProxyPool(),proxy_protocol:selectedProxyProtocol()})
  },3,(attempt,count)=>updateRow(row,'Checkout \u91cd\u8bd5 '+attempt+' / '+count,'running'));
  row._taskId=created.task_id;rememberCheckoutTaskId(created.task_id);
  return{account,row,taskId:created.task_id};
}
async function retrySingleAt(row){
  if(!row?._token||row._retrying)return;
  if(!row._linkOnly&&!cardIsReady){status('Please complete the card fields before retrying this AT.','error');return}
  const batchWasRunning=running;
  row._retrying=true;
  const retry=row.querySelector('.retry-one-button');
  retry.disabled=true;
  if(!batchWasRunning){startButton.disabled=true;addAtButton.disabled=true}
  try{
    status(row._linkOnly?'正在重试该 AT 的直接提链任务。':'正在重试该 AT，并重新创建绑卡与提链任务。');
    if(row._taskId){
      try{
        await api(apiBase+'/api/card-flow/task/'+row._taskId+'/cancel',{method:'POST'});
        await sleep(300);
      }catch{}
      row._taskId='';
    }
    const index=Number(row.dataset.atIndex||0);
    const prepared=row._linkOnly?await prepareLinkOnly(row._token,index,1,row):await prepareOne(row._token,index,1,row);
    const link=await pollCheckout(prepared.taskId,task=>updateRow(prepared.row,task.message||'正在提链','running'));
    const previous=results.findIndex(item=>item.account===prepared.account);
    if(previous>=0)results.splice(previous,1);
    results.push({account:prepared.account,link});
    refreshOutput();
    updateRow(row,'提链成功','success',link);
    status('该 AT 重试已完成。','ok');
  }catch(error){
    updateRow(row,'重试失败','failed',error.message);
    status('该 AT 重试失败：'+error.message,'error');
  }finally{
    row._retrying=false;retry.disabled=false;
    if(!batchWasRunning){
      const linkOnly=Boolean(linkOnlyModeInput?.checked);
      startButton.disabled=linkOnly?!(parseTokens().length&&parseBindProxyPool().length&&parsePromoProxyPool().length):!(cardFieldsMounted&&cardIsReady&&parseTokens().length&&parseBindProxyPool().length&&parsePromoProxyPool().length);
      addAtButton.disabled=false;
    }
  }
}

linkOnlyModeInput.checked=localStorage.getItem('cardLinkOnlyMode')==='1';
function syncLinkOnlyMode(){
  const enabled=Boolean(linkOnlyModeInput.checked);
  localStorage.setItem('cardLinkOnlyMode',enabled?'1':'0');
  cardSetupSection?.classList.toggle('mode-disabled',enabled);
  linkOnlyControl.classList.toggle('active',enabled);
  if(enabled){
    cardReady.textContent='\u53ea\u63d0\u94fe\u5e76\u652f\u4ed8\u6a21\u5f0f\u5df2\u5f00\u542f\uff0c\u5c06\u8df3\u8fc7\u5361\u7247\u52a0\u8f7d\u4e0e\u7ed1\u5361\u3002';
    cardReady.className='inline-state ok';
  }
  updateCount();
}
linkOnlyModeInput.addEventListener('change',syncLinkOnlyMode);
syncLinkOnlyMode();

loadCardButton.addEventListener('click',async()=>{
  if(linkOnlyModeInput.checked)return;
  if(running||cardFieldsMounted)return;
  const tokens=parseTokens();
  if(!tokens.length){status('\u8bf7\u5148\u586b\u5199\u81f3\u5c11\u4e00\u4e2a\u6709\u6548 AT\u3002','error');return}
  if(!parseBindProxyPool().length){status('\u8bf7\u5148\u5728\u4ee3\u7406\u6c60 1 \u586b\u5199 US \u7ed1\u5361\u8282\u70b9\u3002','error');return}
  running=true;loadCardButton.disabled=true;startButton.disabled=true;addAtButton.disabled=true;
  cardReady.textContent='\u6b63\u5728\u521b\u5efa\u5b89\u5168\u5361\u7247\u8f93\u5165\u4f1a\u8bdd\u2026';cardReady.className='inline-state';
  try{
    await loadCardFieldsFromAt(tokens[0]);
    cardReady.textContent='\u5361\u7247\u8f93\u5165\u6846\u5df2\u5c31\u7eea\uff0c\u8bf7\u8f93\u5165\u5361\u53f7\u3001\u6709\u6548\u671f\u548c CVC';
    cardReady.className='inline-state ok';
  }catch(error){
    cardFieldsMounted=false;
    cardReady.textContent='\u5361\u7247\u8f93\u5165\u6846\u52a0\u8f7d\u5931\u8d25\uff1a'+error.message;
    cardReady.className='inline-state error';
    status('\u5361\u7247\u8f93\u5165\u6846\u52a0\u8f7d\u5931\u8d25\uff1a'+error.message,'error');
  }finally{
    running=false;addAtButton.disabled=false;renumberAtCards();updateCount();
  }
});

startButton.addEventListener('click',async()=>{
  if(running)return;
  const tokens=parseTokens();
  if(!tokens.length){status('请先填写至少一个有效 AT。','error');return}
  const atLimit=cdkAtLimit();
  if(tokens.length>atLimit){status('当前 CDK 剩余 '+atLimit+' 次，最多只能提交 '+atLimit+' 个 AT。','error');return}
  if(!parseBindProxyPool().length){status('请先在代理池 1 填写 US 绑卡节点。','error');return}
  if(!parsePromoProxyPool().length){status('请先在代理池 2 填写 TR 优惠节点。','error');return}
  const linkOnly=Boolean(linkOnlyModeInput.checked);
  if(!linkOnly&&!cardFieldsMounted){status('请先加载安全卡片输入框。','error');return}
  if(!linkOnly&&!cardIsReady){status('请完整输入卡号、有效期和 CVC。','error');return}
  const concurrency=Math.max(1,Math.min(10,tokens.length,Number(batchConcurrencyInput?.value||3)||3));
  localStorage.setItem('cardLinkConcurrency',String(concurrency));
  running=true;stopped=false;results.length=0;refreshOutput();taskRows.innerHTML='';
  startButton.disabled=true;stopButton.disabled=false;addAtButton.disabled=true;atList.querySelectorAll('input,button').forEach(el=>el.disabled=true);
  setStep(linkOnly?1:2);setProgress(0,tokens.length*2,linkOnly?'\u76f4\u63a5\u63d0\u94fe\u5e76\u53d1 '+concurrency:'\u7ed1\u5361\u4e32\u884c\uff0c\u63d0\u94fe\u5e76\u53d1 '+concurrency);
  let success=0,failed=0,finishedUnits=0;
  const preparedJobs=[];
  const progressUpdate=text=>setProgress(Math.min(finishedUnits,tokens.length*2),tokens.length*2,text);

  // Card binding is intentionally serial. Each completed binding immediately
  // starts its checkout job, while checkout polling runs concurrently below.
  for(let i=0;i<tokens.length&&!stopped;i++){
    status(linkOnly?'\u6b63\u5728\u63d0\u4ea4\u76f4\u63a5\u63d0\u94fe\uff1a'+(i+1)+' / '+tokens.length:'\u6b63\u5728\u4e32\u884c\u7ed1\u5361\uff1a'+(i+1)+' / '+tokens.length+'\uff1b\u63d0\u94fe\u5e76\u53d1 '+concurrency);
    try{
      const prepared=linkOnly?await prepareLinkOnly(tokens[i],i,tokens.length):await prepareOne(tokens[i],i,tokens.length);
      preparedJobs.push(prepared);
      finishedUnits++;
      progressUpdate(linkOnly?'\u63d0\u94fe\u4efb\u52a1\u5df2\u63d0\u4ea4 '+(i+1)+' / '+tokens.length:'\u7ed1\u5361\u5b8c\u6210 '+(i+1)+' / '+tokens.length+'\uff0c\u63d0\u94fe\u4efb\u52a1\u5df2\u63d0\u4ea4');
    }catch(error){
      failed++;finishedUnits+=2;
      const row=document.querySelector('[data-at-index="'+i+'"]')||taskRows.querySelectorAll('.task-row')[i];
      if(row)updateRow(row,linkOnly?'\u63d0\u94fe\u63d0\u4ea4\u5931\u8d25':'\u7ed1\u5361\u5931\u8d25','failed',error.message);
      progressUpdate('\u4efb\u52a1\u5b8c\u6210 '+(success+failed)+' / '+tokens.length);
    }
  }

  setStep(3);
  let nextCheckout=0;
  const checkoutWorker=async workerId=>{
    await sleep(workerId*100);
    while(!stopped){
      const index=nextCheckout++;
      if(index>=preparedJobs.length)return;
      const prepared=preparedJobs[index];
      try{
        const link=await pollCheckout(prepared.taskId,task=>updateRow(prepared.row,task.message||'\u6b63\u5728\u63d0\u94fe','running'));
        results.push({account:prepared.account,link});refreshOutput();
        updateRow(prepared.row,'\u63d0\u94fe\u6210\u529f','success',link);success++;
      }catch(error){
        if(prepared.row._link){setPaymentState(prepared.row,'\u534f\u8bae\u652f\u4ed8\u5931\u8d25\uff1a'+error.message,'failed')}
        else if(!prepared.row._retrying){failed++;updateRow(prepared.row,'\u63d0\u94fe\u5931\u8d25','failed',error.message)}
      }finally{
        finishedUnits++;progressUpdate('\u4efb\u52a1\u5b8c\u6210 '+(success+failed)+' / '+tokens.length);
      }
    }
  };
  await Promise.all(Array.from({length:Math.min(concurrency,preparedJobs.length)},(_,index)=>checkoutWorker(index)));
  setStep(3);setProgress(tokens.length*2,tokens.length*2,'批量任务完成');
  status('批量任务完成：'+success+' 个成功，'+failed+' 个失败'+(stopped?' (stopped)':''),failed?'error':'ok');
  if(linkOnly&&success&&!stopped){
    taskRows.querySelectorAll('.select-pay-checkbox:not(:disabled)').forEach(item=>{item.checked=true;item.closest('.task-row')?.classList.add('selected-for-pay')});
    updateBatchPayState();
    await paySelectedRows();
  }
  running=false;stopButton.disabled=true;addAtButton.disabled=false;atList.querySelectorAll('input,button').forEach(el=>el.disabled=false);taskRows.querySelectorAll('.retry-one-button:not([hidden])').forEach(button=>button.disabled=false);renumberAtCards();updateCount();
});
stopButton.addEventListener('click',()=>{stopped=true;stopButton.disabled=true;status('Stopping after the current account...')});
batchPayButton.addEventListener('click',paySelectedRows);
selectAllPay.addEventListener('change',()=>{taskRows.querySelectorAll('.select-pay-checkbox:not(:disabled)').forEach(item=>{item.checked=selectAllPay.checked;item.closest('.task-row')?.classList.toggle('selected-for-pay',item.checked)});updateBatchPayState()});
copyButton.addEventListener('click',async()=>{const links=results.map(item=>item.link).filter(Boolean);if(!links.length)return;await navigator.clipboard.writeText(links.join('\n'));status('全部链接已复制。','ok')});
exportCsvButton.addEventListener('click',exportTaskCsv);
clearButton.addEventListener('click',async()=>{
  clearButton.disabled=true;
  const taskIds=[...new Set([...taskRows.querySelectorAll('.task-row')].map(row=>row._taskId).filter(Boolean).concat(storedCheckoutTaskIds()))];
  let released=0;
  try{
    const cleared=await api(apiBase+'/api/card-flow/tasks/clear',{method:'POST'});
    released=Number(cleared.released||0);
  }catch{}
  await Promise.all(taskIds.map(taskId=>api(apiBase+'/api/card-flow/task/'+taskId+'/cancel',{method:'POST'}).catch(()=>null)));
  localStorage.removeItem(checkoutTaskStorageKey);
  results.length=0;refreshOutput();taskRows.innerHTML='';setProgress(0,0,'\u7b49\u5f85\u5f00\u59cb');status('\u7ed3\u679c\u5df2\u6e05\u7a7a\uff0c\u670d\u52a1\u5668\u5df2\u91ca\u653e '+released+' \u4e2a\u63d0\u94fe\u5360\u4f4d\u3002','ok');updateBatchPayState();
  clearButton.disabled=false;
});
batchImportAtButton.addEventListener('click',()=>{
  if(cdkAtLimit()<=0){status('\u8bf7\u5148\u5728\u9875\u9762\u5de6\u4e0a\u89d2\u8f93\u5165\u6709\u6548 CDK\u3002','error');return}
  atImportDialog.hidden=false;atImportMessage.textContent='';setTimeout(()=>atImportInput.focus(),50);
});
atImportDialog.querySelector('.at-import-close').addEventListener('click',()=>{atImportDialog.hidden=true});
atImportDialog.addEventListener('click',event=>{if(event.target===atImportDialog)atImportDialog.hidden=true});
atImportDialog.querySelector('.at-import-clear').addEventListener('click',()=>{atImportInput.value='';atImportMessage.textContent='';atImportInput.focus()});
atImportSubmit.addEventListener('click',()=>{
  const candidates=extractTokensSmart(atImportInput.value);
  if(!candidates.length){atImportMessage.textContent='\u672a\u8bc6\u522b\u5230\u6709\u6548 AT';atImportMessage.className='at-import-message error';return}
  const existing=new Set(parseTokens()),fresh=candidates.filter(token=>!existing.has(token));
  const capacity=Math.max(0,cdkAtLimit()-existing.size),accepted=fresh.slice(0,capacity);
  const blanks=[...atList.querySelectorAll('.at-item-input')].filter(input=>!extractToken(input.value).startsWith('eyJ'));
  let cursor=0;
  for(const input of blanks){if(cursor>=accepted.length)break;input.value=accepted[cursor++]}
  while(cursor<accepted.length){if(!addAtField(accepted[cursor]))break;cursor++}
  updateCount();
  const skippedDuplicate=candidates.length-fresh.length,skippedLimit=fresh.length-accepted.length;
  atImportMessage.textContent='\u5df2\u5bfc\u5165 '+accepted.length+' \u4e2a AT'+(skippedDuplicate?'\uff0c\u91cd\u590d '+skippedDuplicate+' \u4e2a\u5df2\u5ffd\u7565':'')+(skippedLimit?'\uff0c\u8d85\u51fa CDK \u6b21\u6570 '+skippedLimit+' \u4e2a\u5df2\u622a\u65ad':'');
  atImportMessage.className='at-import-message '+(skippedLimit?'warning':'ok');
  if(accepted.length){atImportInput.value='';setTimeout(()=>{atImportDialog.hidden=true},700)}
});
addAtButton.addEventListener('click',()=>addAtField());
bindProxyPoolInput.value=localStorage.getItem('cardLinkBindProxyPool')||localStorage.getItem('cardLinkProxyPool')||'';
promoProxyPoolInput.value=localStorage.getItem('cardLinkPromoProxyPool')||'';
if(batchConcurrencyInput)batchConcurrencyInput.value=localStorage.getItem('cardLinkConcurrency')||'3';
bindProxyPoolInput.addEventListener('input',()=>{localStorage.setItem('cardLinkBindProxyPool',bindProxyPoolInput.value);updateCount()});
promoProxyPoolInput.addEventListener('input',()=>{localStorage.setItem('cardLinkPromoProxyPool',promoProxyPoolInput.value);updateCount()});
batchConcurrencyInput?.addEventListener('change',()=>{const value=Math.max(1,Math.min(10,Number(batchConcurrencyInput.value||3)||3));batchConcurrencyInput.value=String(value);localStorage.setItem('cardLinkConcurrency',String(value))});
window.addEventListener('cdk-usage-updated',()=>{if(cdkAtLimit()>0&&!atList.children.length)addAtField();updateCount()});
addAtField();
init();
syncLinkOnlyMode();
