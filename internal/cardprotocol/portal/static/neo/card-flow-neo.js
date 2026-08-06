(()=>{
  'use strict';
  const reduce=window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const currentCdk=document.getElementById('neoCurrentCdk');
  function renderCurrentCdk(session){
    if(!currentCdk)return;
    const hint=String(session?.full_code||session?.code_hint||'').trim();
    const remaining=Number(session?.remaining_uses??0);
    currentCdk.classList.toggle('connected',Boolean(hint));
    currentCdk.querySelector('b').textContent=hint||'未连接';
    currentCdk.querySelector('small').textContent=hint?('剩余 '+remaining+' 次'):'';
    currentCdk.title=hint?('当前 CDK：'+hint+'，剩余 '+remaining+' 次'):'当前未连接 CDK';
  }
  window.addEventListener('cdk-usage-updated',event=>renderCurrentCdk(event.detail));
  renderCurrentCdk(window.cdkUsageState||null);
  const iconMap=[
    ['.remove-at-button','trash-2'],['.copy-one-button','copy'],['.pay-one-button','send'],
    ['.retry-one-button','refresh-cw'],['.at-import-close','x'],['.at-import-submit','scan-search'],
    ['.at-import-clear','trash-2'],['#copyFinalMergedCode','copy'],['.select-pay-label','circle-check'],
    ['.link-only-control','zap'],['.status','info']
  ];
  let iconQueued=false;
  function iconize(){
    iconQueued=false;
    if(!window.lucide)return;
    for(const [selector,name] of iconMap){
      document.querySelectorAll(selector).forEach(el=>{
        if(el.querySelector('svg,[data-lucide]'))return;
        const i=document.createElement('i');i.setAttribute('data-lucide',name);el.prepend(i);
      });
    }
    window.lucide.createIcons({attrs:{'aria-hidden':'true','stroke-width':'1.7'}});
  }
  function queueIcons(){if(iconQueued)return;iconQueued=true;requestAnimationFrame(iconize)}
  queueIcons();
  new MutationObserver(queueIcons).observe(document.body,{subtree:true,childList:true});
  const themeButton=document.getElementById('neoThemeButton');
  const themeMedia=window.matchMedia('(prefers-color-scheme: light)');
  const themeKey='cardLinkNeoTheme';
  function applyTheme(value,persist=false){
    const theme=value==='light'?'light':'dark';
    document.documentElement.dataset.theme=theme;
    if(persist)localStorage.setItem(themeKey,theme);
    window.dispatchEvent(new CustomEvent('neo-theme-changed',{detail:{theme}}));
    if(themeButton){
      themeButton.innerHTML='<i data-lucide="'+(theme==='light'?'moon':'sun')+'"></i>';
      themeButton.setAttribute('aria-label',theme==='light'?'切换到深色模式':'切换到浅色模式');
      themeButton.title=theme==='light'?'切换到深色模式':'切换到浅色模式';
      queueIcons();
    }
  }
  const savedTheme=localStorage.getItem(themeKey);
  applyTheme(savedTheme||(themeMedia.matches?'light':'dark'));
  themeButton?.addEventListener('click',()=>applyTheme(document.documentElement.dataset.theme==='light'?'dark':'light',true));
  themeMedia.addEventListener?.('change',event=>{if(!localStorage.getItem(themeKey))applyTheme(event.matches?'light':'dark')});
  const cdkPanel=document.getElementById('cdkGate');
  const cdkCollapseKey='cardLinkNeoCdkCollapsed';
  const cdkCollapseButton=document.createElement('button');
  cdkCollapseButton.id='neoCdkCollapseButton';cdkCollapseButton.type='button';cdkCollapseButton.className='neo-cdk-toggle';
  cdkPanel?.appendChild(cdkCollapseButton);
  function setCdkCollapsed(collapsed){
    cdkPanel?.classList.toggle('is-collapsed',collapsed);
    cdkCollapseButton.innerHTML='<i data-lucide="'+(collapsed?'panel-left-open':'panel-left-close')+'"></i><span>'+(collapsed?'展开':'隐藏')+'</span>';
    cdkCollapseButton.title=collapsed?'展开 CDK 面板':'隐藏 CDK 面板';
    localStorage.setItem(cdkCollapseKey,collapsed?'1':'0');queueIcons();
  }
  cdkCollapseButton.addEventListener('click',()=>setCdkCollapsed(!cdkPanel.classList.contains('is-collapsed')));
  setCdkCollapsed(localStorage.getItem(cdkCollapseKey)==='1');

  const cardPreview=document.getElementById('neoCardPreviewNumber');
  const cardBrand=document.getElementById('neoCardBrand');
  const cardExpiry=document.getElementById('neoCardExpiry');
  const brandNames={visa:'VISA',mastercard:'MASTERCARD',amex:'AMEX',discover:'DISCOVER',diners:'DINERS CLUB',jcb:'JCB',unionpay:'UNIONPAY',unknown:'CARD'};
  window.addEventListener('neo-card-confirmed',event=>{
    const detail=event.detail||{};
    const groups=cardPreview?.querySelectorAll('span')||[];
    if(detail.last4&&groups.length===4)groups[3].textContent=detail.last4;
    if(detail.brand)cardBrand.textContent=brandNames[String(detail.brand).toLowerCase()]||String(detail.brand).toUpperCase();
    if(detail.expMonth&&detail.expYear)cardExpiry.textContent=String(detail.expMonth).padStart(2,'0')+' / '+String(detail.expYear).slice(-2);
    cardPreview?.classList.add('is-complete','has-last4');
  });
  window.addEventListener('neo-card-field-change',event=>{
    const detail=event.detail||{};
    if(detail.field==='number'){
      cardBrand.textContent=brandNames[String(detail.brand||'unknown').toLowerCase()]||String(detail.brand||'CARD').toUpperCase();
      cardPreview.classList.toggle('is-active',!detail.empty);
      cardPreview.classList.toggle('is-complete',Boolean(detail.complete));
    }
    if(detail.field==='expiry')cardExpiry.textContent=detail.complete?'READY':'MM / YY';
    if(detail.field==='cvc')document.querySelector('.card-visual')?.classList.toggle('security-ready',Boolean(detail.complete));
  });

  document.addEventListener('pointermove',event=>{
    document.documentElement.style.setProperty('--mx',event.clientX+'px');
    document.documentElement.style.setProperty('--my',event.clientY+'px');
  },{passive:true});

  const visual=document.querySelector('.card-visual');
  if(visual&&!reduce){
    visual.addEventListener('pointermove',event=>{
      const r=visual.getBoundingClientRect();
      visual.style.setProperty('--card-rx',((event.clientY-r.top)/r.height-.5)*-7+'deg');
      visual.style.setProperty('--card-ry',((event.clientX-r.left)/r.width-.5)*9+'deg');
    });
    visual.addEventListener('pointerleave',()=>{visual.style.setProperty('--card-rx','0deg');visual.style.setProperty('--card-ry','0deg')});
  }

  const command=document.getElementById('neoCommand');
  const commandButton=document.getElementById('neoCommandButton');
  const setCommand=open=>{command.hidden=!open;if(open)setTimeout(()=>command.querySelector('button[data-scroll-to]')?.focus(),30)};
  commandButton?.addEventListener('click',()=>setCommand(true));
  command?.querySelector('[data-command-close]')?.addEventListener('click',()=>setCommand(false));
  command?.addEventListener('click',event=>{if(event.target===command)setCommand(false)});
  command?.querySelectorAll('[data-scroll-to]').forEach(button=>button.addEventListener('click',()=>{
    document.querySelector('[data-neo-section="'+button.dataset.scrollTo+'"]')?.scrollIntoView({behavior:reduce?'auto':'smooth',block:'start'});setCommand(false);
  }));
  document.addEventListener('keydown',event=>{
    if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='k'){event.preventDefault();setCommand(command.hidden)}
    if(event.key==='Escape'&&!command.hidden)setCommand(false);
  });

  const canvas=document.getElementById('neoField');
  if(!canvas||reduce)return;
  const ctx=canvas.getContext('2d',{alpha:true});
  let w=0,h=0,dpr=1,raf=0;
  const pointer={x:-9999,y:-9999};
  const nodes=Array.from({length:34},()=>({x:Math.random(),y:Math.random(),vx:(Math.random()-.5)*.00007,vy:(Math.random()-.5)*.00007,r:Math.random()*1.3+.4}));
  function resize(){dpr=Math.min(2,window.devicePixelRatio||1);w=innerWidth;h=innerHeight;canvas.width=w*dpr;canvas.height=h*dpr;canvas.style.width=w+'px';canvas.style.height=h+'px';ctx.setTransform(dpr,0,0,dpr,0,0)}
  function draw(){
    ctx.clearRect(0,0,w,h);
    for(const n of nodes){n.x+=n.vx;n.y+=n.vy;if(n.x<0||n.x>1)n.vx*=-1;if(n.y<0||n.y>1)n.vy*=-1}
    for(let i=0;i<nodes.length;i++){
      const a=nodes[i],ax=a.x*w,ay=a.y*h;
      for(let j=i+1;j<nodes.length;j++){
        const b=nodes[j],bx=b.x*w,by=b.y*h,dx=ax-bx,dy=ay-by,dist=Math.hypot(dx,dy);
        if(dist<180){ctx.strokeStyle='rgba(117,147,175,'+(1-dist/180)*.10+')';ctx.lineWidth=.6;ctx.beginPath();ctx.moveTo(ax,ay);ctx.lineTo(bx,by);ctx.stroke()}
      }
      const pd=Math.hypot(ax-pointer.x,ay-pointer.y);if(pd<150){ctx.strokeStyle='rgba(199,255,61,'+(1-pd/150)*.15+')';ctx.beginPath();ctx.moveTo(ax,ay);ctx.lineTo(pointer.x,pointer.y);ctx.stroke()}
      ctx.fillStyle=i%7===0?'rgba(199,255,61,.38)':'rgba(142,165,190,.23)';ctx.beginPath();ctx.arc(ax,ay,a.r,0,Math.PI*2);ctx.fill();
    }
    raf=requestAnimationFrame(draw);
  }
  window.addEventListener('resize',resize,{passive:true});
  window.addEventListener('pointermove',event=>{pointer.x=event.clientX;pointer.y=event.clientY},{passive:true});
  document.addEventListener('visibilitychange',()=>{if(document.hidden){cancelAnimationFrame(raf)}else{raf=requestAnimationFrame(draw)}});
  resize();draw();
})();






