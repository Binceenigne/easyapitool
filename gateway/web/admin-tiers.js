'use strict';
(() => {
  const modes = [['instant','Instant · 直接生图'],['flash','Flash'],['medium','Medium'],['high','High'],['extra','Extra'],['max','Max']];
  const efforts = [['none','不启用'],['minimal','最少'],['low','低'],['medium','中'],['high','高'],['xhigh','超高'],['max','最高']];
  const qualities = [['low','低'],['medium','中'],['high','高'],['xhigh','超高'],['max','最高']];
  const formats = [['png','PNG'],['jpeg','JPEG'],['webp','WebP'],['svg','SVG 矢量图'],['gif','GIF 动图']];
  const switches = [['advanced','高级模式','允许自由组合已开放的思维模型与思考深度。'],['transparency','透明背景','PNG 和 WebP 可以保留透明通道。'],['webSearch','联网搜索','思维链可以使用上游联网搜索工具。'],['promptPolish','提示词润色','允许一键优化当前提示词。'],['imageEditing','参考图与图片编辑','允许上传参考图和继续编辑图片。']];
  let editing = null, revision = 0, deleting = null;
  const tiers = () => state?.appModels?.tiers || [];
  const all = (values, value) => (values || []).includes('*') || (values || []).includes(value);
  const labels = values => values.map(([v,label]) => [v,label]);
  const defaultTierId = () => state?.appModels?.defaultTierId || '';
  const tierFor = key => tiers().find(t => t.id === (key?.policy?.tierId || defaultTierId()));
  const bindingCount = id => (state?.keys || []).filter(k => (k.policy?.tierId || defaultTierId()) === id).length;
  const fullTierBaseline = () => ({id:'',name:'',description:'',imageModels:['*'],reasoningModels:['*'],reasoningModes:['*'],reasoningEfforts:['*'],qualities:['*'],complexities:['*'],formats:['*'],advanced:true,transparency:true,webSearch:true,promptPolish:true,imageEditing:true,rpmLimit:0,maxResolution:3840,maxImages:9,maxReferences:16,maxGifFrames:16,defaultQuota:{expiresInDays:0,totalMicroUSD:0,fiveHourMicroUSD:0,dayMicroUSD:0,weekMicroUSD:0}});
  const rpmName = t => Number(t.rpmLimit)>0 ? number(t.rpmLimit)+' 次/分钟' : '不限';
  const limitName = t => ({1536:'标清',2560:'高清',3840:'超清'}[t.maxResolution] || '—');
  const allowedText = (list, catalog) => (list || []).includes('*') ? '全部' : (list || []).length ? list.map(v => catalog?.find(([id]) => id === v)?.[1] || v).join('、') : '未开放';
  const quotaFields = [['totalUsd','totalMicroUSD'],['fiveHourUsd','fiveHourMicroUSD'],['dayUsd','dayMicroUSD'],['weekUsd','weekMicroUSD']];
  const defaultFields = [['defaultTotalUsd','totalMicroUSD'],['defaultFiveHourUsd','fiveHourMicroUSD'],['defaultDayUsd','dayMicroUSD'],['defaultWeekUsd','weekMicroUSD']];
  const quotaDefaults = tier => ({expiresInDays:0,totalMicroUSD:0,fiveHourMicroUSD:0,dayMicroUSD:0,weekMicroUSD:0,...tier?.defaultQuota});
  const quotaAmount = micro => Number(micro) ? usd(Number(micro)/1e6) : '不限';
  const validity = days => Number(days) ? '签发后 '+number(days)+' 天' : '长期有效';
  let keyManualDraft = null;

  function quotaPreview(tier) {
    const quota = quotaDefaults(tier), preview = $('key-tier-default-preview');
    const grid = node('dl','key-default-quota-grid');
    for (const [label,value] of [['有效期',validity(quota.expiresInDays)],['总创作额度',quotaAmount(quota.totalMicroUSD)],['5 小时额度',quotaAmount(quota.fiveHourMicroUSD)],['每日额度',quotaAmount(quota.dayMicroUSD)],['7 天额度',quotaAmount(quota.weekMicroUSD)]]) {
      const item=node('div'); item.append(node('dt','caption',label),node('dd','number',value)); grid.append(item);
    }
    preview.replaceChildren(node('strong','',tier.name+' · 默认限制'),grid,node('p','hint','由服务端在保存时套用；已有用量记录和已用费用保持不变。'));
  }

  window.syncKeyTierDefaults = () => {
    const f = $('key-form').elements, useDefaults = f.useTierDefaults.checked;
    const tier = tiers().find(t => t.id === f.tierId.value);
    $('key-manual-quota').disabled = useDefaults;
    $('key-tier-default-preview').hidden = !useDefaults;
    if (useDefaults && tier) {
      if (!keyManualDraft) {
        keyManualDraft = {usdOn:f.usdOn.checked,expiryOn:f.expiryOn.checked,expiresAt:f.expiresAt.value,
          amounts:Object.fromEntries(quotaFields.map(([field]) => [field,f[field].value])),
          expiryPresets:[...$('expiry-presets').children].map(button => button.getAttribute('aria-pressed'))};
      }
      const quota = quotaDefaults(tier);
      f.usdOn.checked = quotaFields.some(([,prop]) => quota[prop] > 0);
      for (const [field,prop] of quotaFields) f[field].value = quota[prop]/1e6;
      f.expiryOn.checked = quota.expiresInDays > 0;
      f.expiresAt.value = quota.expiresInDays ? localDate(new Date(Date.now()+quota.expiresInDays*86400000)) : '';
      for (const button of $('expiry-presets').children) button.setAttribute('aria-pressed',String(Number(button.dataset.days) === quota.expiresInDays && quota.expiresInDays > 0));
      quotaPreview(tier);
    } else if (keyManualDraft) {
      const draft=keyManualDraft; keyManualDraft=null;
      f.usdOn.checked=draft.usdOn;f.expiryOn.checked=draft.expiryOn;f.expiresAt.value=draft.expiresAt;
      for (const [field] of quotaFields) f[field].value=draft.amounts[field];
      [...$('expiry-presets').children].forEach((button,index) => button.setAttribute('aria-pressed',draft.expiryPresets[index] || 'false'));
    }
  };


  function summary(t, target) {
    target.replaceChildren();
    if (!t) return;
    target.append(node('strong','',t.name),node('p','hint',t.description || '独立管理模型与生成功能权限'));
    const tags = node('div','tier-tags');
    tags.append(status(limitName(t)),status('最多 '+t.maxImages+' 张'),status(all(t.formats,'svg')?'SVG':'静态位图'));
    if (all(t.formats,'gif')) tags.append(status('GIF · '+t.maxGifFrames+' 帧'));
    if (t.transparency) tags.append(status('透明背景'));
    if (t.advanced) tags.append(status('高级思维'));
    tags.append(status('RPM · '+rpmName(t)));
    target.append(tags);
  }

  window.keyTierBadge = key => status(tierFor(key)?.name || '档位不可用','tier-badge');
  window.prepareKeyTier = key => {
    const select = $('key-tier-select');
    select.replaceChildren(...tiers().map(t => new Option(t.name,t.id)));
    const requested=key?.policy?.tierId || defaultTierId();
    select.value = tiers().some(t=>t.id===requested) ? requested : defaultTierId();
    keyManualDraft = null; $('key-use-tier-defaults').checked = !key;
    summary(tiers().find(t => t.id === select.value),$('key-tier-summary'));
  };
  $('key-tier-select').addEventListener('change', e => { summary(tiers().find(t => t.id === e.target.value),$('key-tier-summary')); syncKey(); });

  function permissionGroup(name,title,choices,selected,help) {
    const group = node('fieldset','tier-permission-group'); group.dataset.permissionGroup = name;
    const legend = node('legend','',title), head = node('div','tier-group-head');
    const selectAll = node('label','inline-check'), check = node('input');
    check.type = 'checkbox'; check.value = '*'; check.checked = selected.includes('*');
    selectAll.append(check,'全部'); head.append(selectAll); group.append(legend,head);
    if (help) group.append(node('p','hint',help));
    const options = node('div','tier-options');
    for (const [value,label] of choices) {
      const row = node('label','tier-option'), input = node('input');
      input.type = 'checkbox'; input.value = value; input.checked = check.checked || selected.includes(value);
      row.append(input,node('span','',label)); options.append(row);
      input.addEventListener('change', () => { check.checked = false; updateSummary(); });
    }
    check.addEventListener('change', () => { options.querySelectorAll('input').forEach(input => {input.checked = check.checked;}); updateSummary(); });
    group.append(options);
    return group;
  }

  function readTier() {
    const form = $('tier-form'), f = form.elements;
    const t = { id: editing?.id || '', name: f.name.value.trim(), description: f.description.value.trim() };
    for (const group of $('tier-permissions').children) {
      const checked = [...group.querySelectorAll('input:checked')].map(input => input.value);
      t[group.dataset.permissionGroup] = checked.includes('*') ? ['*'] : checked;
    }
    for (const name of ['maxResolution','maxImages','maxReferences','maxGifFrames','rpmLimit']) t[name] = Number(f[name].value);
    for (const [name] of switches) t[name] = f[name].checked;
    t.defaultQuota = {expiresInDays:Number(f.defaultExpiresInDays.value)};
    for (const [field,prop] of defaultFields) t.defaultQuota[prop] = Math.round(Number(f[field].value || 0)*1e6);
    return t;
  }
  function updateSummary() {
    const t = readTier(); t.name ||= '新档位'; summary(t,$('tier-live-summary'));
    const quota=quotaDefaults(t);
    $('tier-live-summary').append(node('p','hint','默认限制：'+validity(quota.expiresInDays)+' · 总创作额度 '+quotaAmount(quota.totalMicroUSD)+' · 5 小时 '+quotaAmount(quota.fiveHourMicroUSD)+' · 每日 '+quotaAmount(quota.dayMicroUSD)+' · 7 天 '+quotaAmount(quota.weekMicroUSD)));
    const days=Number($('tier-form').elements.defaultExpiresInDays.value);
    const custom=![0,7,30,90].includes(days);
    for (const button of $('tier-expiry-presets').children) button.setAttribute('aria-pressed',String(button.dataset.expiryDays === (custom?'custom':String(days))));
  }

  function openTier(t = null, duplicate = false) {
    const value = structuredClone(t || fullTierBaseline());
    editing = t && !duplicate ? t : null; revision = state.appModels.revision;
    if (!t) { value.name = ''; value.description = ''; }
    if (duplicate) value.name += ' · 副本';
    const f = $('tier-form').elements;
    f.name.value = value.name; f.description.value = value.description;
    const quota=quotaDefaults(value); f.defaultExpiresInDays.value=quota.expiresInDays;
    for (const [field,prop] of defaultFields) f[field].value=quota[prop]/1e6;
    $('tier-title').textContent = editing ? '编辑档位 · '+value.name : '新建功能档位';
    const imageModels = [...new Set([...state.appModels.imageModels,...value.imageModels.filter(v => v !== '*')])];
    const reasoningModels = [...new Set([...Object.values(state.appModels.reasoning).map(p => p.model),...Object.values(state.appModels.reasoningModes).map(p => p.model),...value.reasoningModels.filter(v => v !== '*')])].filter(Boolean);
    const groups = [
      ['imageModels','生图模型',imageModels.map(v => [v,v]),'从网关已开放的生图模型中选择。'],
      ['reasoningModels','思维模型',reasoningModels.map(v => [v,v]),'约束六档预设和高级模式可以调用的模型。'],
      ['reasoningModes','思维深度预设',modes,'Instant 始终可用于直接生图；其余预设同时受模型和思考深度限制。'],
      ['reasoningEfforts','模型思考深度',efforts,'只展示所选模型实际支持的深度，勾选全部可随模型能力更新。'],
      ['qualities','图片质量',qualities,'控制渲染质量；客户端默认选择中质量。'],
      ['complexities','构图复杂度',[['low','低'],['medium','中'],['high','高']],'适用于支持内部构图规划的模型。'],
      ['formats','输出格式',formats,'SVG 与 GIF 同时需要网关及所选生图模型支持。']
    ];
    $('tier-permissions').replaceChildren(...groups.map(([name,title,choices,help]) => permissionGroup(name,title,labels(choices),value[name] || [],help)));
    $('tier-switches').replaceChildren(...switches.map(([name,label,help]) => {
      const row = node('label','tier-toggle'), words = node('span'); words.append(node('strong','',label),node('small','hint',help));
      const input = node('input'); input.name = name; input.type = 'checkbox'; input.setAttribute('role','switch'); input.checked = !!value[name];
      row.append(words,input); return row;
    }));
    for (const name of ['maxResolution','maxImages','maxReferences','maxGifFrames','rpmLimit']) f[name].value = value[name] ?? 0;
    updateSummary(); openDialog('tier-dialog');
  }

  function copiedTierName(name) {
    const suffix='/副本', encoder=new TextEncoder(), maximum=120-encoder.encode(suffix).length;
    let prefix='', bytes=0;
    for (const character of String(name)) {
      const width=encoder.encode(character).length;if(bytes+width>maximum)break;
      prefix+=character;bytes+=width;
    }
    return prefix+suffix;
  }

  function tierCopyAction(tier) {
    const button=action('一键复制',async()=>{
      button.setAttribute('aria-busy','true');button.replaceChildren(icon('Copy'),'正在复制…');
      try {
        const copy=structuredClone(tier);copy.id='';copy.name=copiedTierName(tier.name);
        await api('tiers',{tier:copy,revision:state.appModels.revision});
        await afterSave('已创建“'+copy.name+'”，可从列表继续编辑。');
      } catch(error) { throw new Error(error.message+'；复制未完成，请刷新配置后重试。'); }
      finally { button.removeAttribute('aria-busy');button.replaceChildren(icon('Copy'),'一键复制'); }
    },'Copy');
    button.setAttribute('aria-label','一键复制档位 '+tier.name);return button;
  }

  let previewTier=null, previewProfile=null, previewPanel='settings', previewSequence=0, previewLoaded=false, previewTimer=0;
  const previewDialog=$('tier-preview-dialog'), previewFrame=$('tier-app-preview');
  const previewThemeObserver=new MutationObserver(()=>sendTierPreview());

  function previewLoading(loading) {
    $('tier-preview-loading').hidden=!loading;$('tier-preview-stage').setAttribute('aria-busy',String(loading));
    for(const id of ['tier-preview-settings','tier-preview-reasoning'])$(id).disabled=loading;
  }
  function sendTierPreview() {
    if(!previewDialog.open || !previewProfile || !previewLoaded)return;
    previewFrame.contentWindow?.postMessage({type:'mirra-app-models',profile:previewProfile,view:'bottom',panel:previewPanel,theme:document.documentElement.dataset.theme==='dark'?'dark':'light'},'*');
  }
  function previewFailure(message) {
    clearTimeout(previewTimer);previewLoading(false);$('tier-preview-stage').hidden=true;
    $('tier-preview-error').hidden=false;$('tier-preview-error-text').textContent=message;$('tier-preview-retry').disabled=false;
    $('tier-preview-state').textContent='预览加载失败';
  }
  async function loadTierPreview() {
    if(!previewTier || !previewDialog.open)return;
    const sequence=++previewSequence;
    clearTimeout(previewTimer);previewFrame.onload=null;previewFrame.removeAttribute('src');previewProfile=null;previewLoaded=false;
    $('tier-preview-error').hidden=true;$('tier-preview-stage').hidden=false;$('tier-preview-retry').disabled=true;
    $('tier-preview-state').textContent='正在读取档位权限…';previewLoading(true);
    previewTimer=setTimeout(()=>{if(sequence===previewSequence&&previewDialog.open)previewFailure('客户端预览加载超时，请重新加载。');},15000);
    try {
      const profile=await api('tiers/'+encodeURIComponent(previewTier.id)+'/preview');
      if(sequence!==previewSequence || !previewDialog.open)return;
      if(!profile || profile.tier?.id!==previewTier.id)throw new Error('返回的档位配置不匹配，请刷新后重试。');
      previewProfile=profile;$('tier-preview-state').textContent='正在加载实际客户端…';
      previewFrame.onload=()=>{
        if(sequence!==previewSequence || !previewDialog.open || !previewProfile)return;
        previewLoaded=true;sendTierPreview();
      };
      previewFrame.src='/app-preview/';
    }catch(error){if(sequence===previewSequence&&previewDialog.open)previewFailure(error.message);}
  }
  function openTierPreview(tier) {
    previewTier={id:tier.id,name:tier.name};previewPanel='settings';
    $('tier-preview-title').textContent=tier.name+' · 客户端底部预览';previewFrame.title=tier.name+' · 客户端底部预览';
    $('tier-preview-settings').setAttribute('aria-pressed','true');$('tier-preview-reasoning').setAttribute('aria-pressed','false');
    openDialog('tier-preview-dialog');previewThemeObserver.observe(document.documentElement,{attributes:true,attributeFilter:['data-theme']});
    void loadTierPreview();
  }
  function tierPreviewAction(tier) {
    const button=action('预览',()=>openTierPreview(tier),'PanelsTopLeft');
    button.setAttribute('aria-label','预览档位 '+tier.name);return button;
  }
  for(const [id,panel] of [['tier-preview-settings','settings'],['tier-preview-reasoning','reasoning']])$(id).addEventListener('click',()=>{
    previewPanel=panel;$('tier-preview-settings').setAttribute('aria-pressed',String(panel==='settings'));$('tier-preview-reasoning').setAttribute('aria-pressed',String(panel==='reasoning'));sendTierPreview();
  });
  $('tier-preview-retry').addEventListener('click',()=>{void loadTierPreview();});
  window.addEventListener('message',event=>{
    if(event.source!==previewFrame.contentWindow || event.data?.type!=='mirra-app-preview-ready' || !previewDialog.open || !previewProfile || !previewLoaded || event.data.tierId!==previewTier?.id)return;
    clearTimeout(previewTimer);previewLoading(false);$('tier-preview-state').textContent=previewPanel==='settings'?'生图设置 · 已保存权限':'思维深度 · 已保存权限';
  });
  previewFrame.addEventListener('error',()=>{if(previewDialog.open)previewFailure('无法加载实际客户端页面，请重试。');});
  previewDialog.addEventListener('close',()=>{
    previewSequence++;clearTimeout(previewTimer);previewThemeObserver.disconnect();previewFrame.onload=null;previewFrame.removeAttribute('src');
    previewTier=null;previewProfile=null;previewLoaded=false;previewPanel='settings';$('tier-preview-title').textContent='客户端底部预览';previewFrame.title='档位客户端底部预览';
    $('tier-preview-error').hidden=true;$('tier-preview-state').textContent='正在准备预览…';
  });

  let tierOrderPending=false, tierOrderMessage='', tierOrderTone='';
  function orderHandle(tier,index,axis) {
    return window.MirraSortable.handle({id:tier.id,label:tier.name,index,total:tiers().length,axis,disabled:tierOrderPending});
  }
  function orderFeedback() {
    const automatic=!(state.appModels.tierOrder || []).length;
    for(const suffix of ['list','compare']) {
      $('tier-order-mode-'+suffix).textContent=tierOrderPending?'正在保存顺序…':automatic?'当前：按权限排序':'当前：手动顺序';
      const reset=$('tier-order-auto-'+suffix);reset.disabled=tierOrderPending || automatic;
      reset.title=automatic?'当前已按权限范围排列':'恢复按权限范围排列；已发布快照需另行同步';
      feedback($('tier-order-feedback-'+suffix),tierOrderMessage,tierOrderTone);
    }
    for(const id of ['new-tier','new-tier-compare'])$(id).disabled=tierOrderPending;
    for(const id of ['tier-list','tier-comparison'])$(id).setAttribute('aria-busy',String(tierOrderPending));
    if(tierOrderPending)for(const selector of ['#tier-list .row-actions button','#tier-comparison .row-actions button'])document.querySelectorAll(selector).forEach(button=>{button.disabled=true;});
  }
  async function saveTierOrder(ids,automatic=false) {
    if(tierOrderPending || !state)return false;
    const previousRevision=state.appModels.revision;
    tierOrderPending=true;tierOrderMessage='正在保存档位顺序…';tierOrderTone='';window.renderTiers();
    try {
      const profile=await api('tiers/order',{ids:automatic?[]:ids,automatic,revision:previousRevision});
      if(!state)return false;
      state.appModels=profile;window.acceptTierOrderRevision?.(previousRevision,profile.revision);
      tierOrderMessage=automatic?'已恢复按权限排序。已发布的落地页快照保持不变，手动同步后更新。':'档位顺序已保存。已发布的落地页快照保持不变，手动同步后更新。';tierOrderTone='good';
      await afterSave(tierOrderMessage);return true;
    }catch(error){
      try{await refresh();tierOrderMessage='保存排序失败，已恢复服务端顺序。'+error.message;}catch{tierOrderMessage='保存排序失败，本地显示未改动，请刷新后重试。'+error.message;}
      tierOrderTone='error';tell(tierOrderMessage,'error');window.MirraSortable.announce(tierOrderMessage);return false;
    }finally{tierOrderPending=false;if(state)window.renderTiers();}
  }
  for(const id of ['tier-order-auto-list','tier-order-auto-compare'])$(id).addEventListener('click',()=>{void saveTierOrder([],true);});

  function comparison() {
    const plans = tiers(), query = $('tier-feature-search').value.trim().toLowerCase();
    if (!plans.length) { $('tier-comparison').replaceChildren(empty('Layers3','尚无功能档位','创建功能档位后，可在这里横向对比额度与权限。',action('新建档位',()=>openTier(),'Plus'))); return; }
    const definitions = [
      ['说明',t => t.description || '—'],['默认有效期',t => validity(quotaDefaults(t).expiresInDays)],
      ['默认总创作额度',t => quotaAmount(quotaDefaults(t).totalMicroUSD)],['默认 5 小时额度',t => quotaAmount(quotaDefaults(t).fiveHourMicroUSD)],
      ['默认每日额度',t => quotaAmount(quotaDefaults(t).dayMicroUSD)],['默认 7 天额度',t => quotaAmount(quotaDefaults(t).weekMicroUSD)],['RPM（语言 + 图像）',rpmName],['生图模型',t => allowedText(t.imageModels)],['思维模型',t => allowedText(t.reasoningModels)],
      ['思维深度预设',t => allowedText(t.reasoningModes,modes)],['模型思考深度',t => allowedText(t.reasoningEfforts,efforts)],
      ['高级模式',t => t.advanced],['SVG 矢量输出',t => all(t.formats,'svg')],['GIF 动图',t => all(t.formats,'gif')],
      ['透明背景',t => t.transparency],['最大分辨率',t => limitName(t)+' · '+t.maxResolution+' px'],
      ['图片质量',t => allowedText(t.qualities,qualities)],['构图复杂度',t => allowedText(t.complexities,[['low','低'],['medium','中'],['high','高']])],
      ['输出格式',t => allowedText(t.formats,formats)],['最多生成张数',t => t.maxImages+' 张'],['最多参考图',t => t.imageEditing?t.maxReferences+' 张':'未开放'],
      ['GIF 最大帧数',t => all(t.formats,'gif')?t.maxGifFrames+' 帧':'—'],['图片编辑',t => t.imageEditing],['联网搜索',t => t.webSearch],['提示词润色',t => t.promptPolish],['绑定密钥',t => bindingCount(t.id)+' 把']
    ];
    const wrap = node('div','tier-table-scroll'), grid = node('table','tier-comparison-table');
    const header = grid.createTHead().insertRow(); header.append(node('th','','功能权限'));
    plans.forEach((t,index) => {
      const cell = node('th'), box = node('div','tier-column-title'); cell.scope = 'col';cell.dataset.orderId=t.id;cell.dataset.tierId=t.id;
      const orderLabel=node('div','tier-column-order');orderLabel.append(orderHandle(t,index,'x'),node('small','caption','档位 '+(index+1)));
      box.append(orderLabel,node('strong','',t.name),actions(tierPreviewAction(t),tierCopyAction(t),action('编辑',()=>openTier(t)),tierDeleteAction(t)));
      cell.append(box); header.append(cell);
    });
    const body = grid.createTBody(); let count = 0;
    for (const [label,get] of definitions) {
      const values = plans.map(get);
      if (query && ![label,...values].join(' ').toLowerCase().includes(query)) continue;
      if ($('tier-differences').checked && new Set(values.map(String)).size < 2) continue;
      const row = body.insertRow(), name = node('th','',label); name.scope = 'row'; row.append(name);
      values.forEach(value => { const cell = row.insertCell(); if (typeof value === 'boolean') cell.append(status(value?'允许':'未开放',value?'good':'tier-unavailable')); else cell.append(node('span',label.endsWith('模型')?'tier-model-ids':'',value)); });
      count++;
    }
    window.MirraSortable.bind(header,{axis:'x',disabled:()=>tierOrderPending,focusRoot:()=>$('tier-comparison'),onReorder:ids=>saveTierOrder(ids)});
    wrap.append(grid); $('tier-comparison').replaceChildren(wrap);
    if (!count) $('tier-comparison').append(empty('Search','没有匹配的差异','可关闭“只看不同项”或更换搜索词。'));
  }

  function resetTierDeletion() {
    $('delete-tier-replacement').hidden=true;
    $('delete-tier-snapshot-note').hidden=true;
    $('delete-tier-replacement-summary').hidden=true;
    const select=$('delete-tier-replacement-select');select.replaceChildren();select.required=false;select.disabled=true;
    $('delete-key-confirm').disabled=false;
  }

  window.syncTierDeleteButton = () => {
    if (!deleting?.tier) return;
    const count=bindingCount(deleting.tier.id), select=$('delete-tier-replacement-select');
    const valid=tiers().some(t=>t.id===select.value && t.id!==deleting.tier.id);
    $('delete-key-confirm').disabled=$('delete-key-form').dataset.pending==='true' || count>0&&!valid;
  };

  function confirmDeleteTier(tier) {
    resetTierDeletion();deleting={tier,revision:state.appModels.revision};
    const count=bindingCount(tier.id), replacements=tiers().filter(t=>t.id!==tier.id);
    $('delete-key-title').textContent='删除功能档位';$('delete-key-name').textContent='删除“'+tier.name+'”？';
    $('delete-key-note').textContent=count ? replacements.length
      ? '此档位绑定 '+count+' 把密钥。请选择替代档位，迁移这些密钥后再删除。'
      : '此档位绑定 '+count+' 把密钥，当前没有其它档位可承接。请先创建替代档位，或先处理绑定的密钥，再重试删除。'
      : '此档位未绑定密钥，删除后可重新创建功能档位。';
    $('delete-tier-snapshot-note').hidden=false;
    $('delete-key-confirm').textContent=count?'迁移并删除档位':'删除档位';
    if (count) {
      $('delete-tier-replacement').hidden=false;
      const select=$('delete-tier-replacement-select');
      select.replaceChildren(new Option(replacements.length?'请选择替代档位':'暂无可迁移的档位',''),...replacements.map(t=>new Option(t.name,t.id)));
      select.disabled=!replacements.length;select.required=!!replacements.length;
    }
    window.syncTierDeleteButton();openDialog('delete-key-dialog');
    if(count&&replacements.length)$('delete-tier-replacement-select').focus({preventScroll:true});
  }

  function tierDeleteAction(tier) {
    const button=action('删除',()=>confirmDeleteTier(tier),'Trash2');
    button.classList.add('danger-text');button.setAttribute('aria-label','删除档位 '+tier.name);return button;
  }

  $('delete-tier-replacement-select').addEventListener('change',event=>{
    const tier=tiers().find(t=>t.id===event.target.value);
    $('delete-tier-replacement-summary').hidden=!tier;
    summary(tier,$('delete-tier-replacement-summary'));
    window.syncTierDeleteButton();
  });

  window.createTierForKey=()=>openTier();
  window.renderTiers = () => {
    if (!state) return;
    if (tiers().length) {
      const list=table(['档位','能力范围','绑定密钥','操作'],tiers().map((t,index) => {
        const capabilities=node('div','tier-tags');capabilities.append(status(limitName(t)),status(t.advanced?'高级思维':'预设思维'),status(all(t.formats,'gif')?'含 GIF':'静态输出'),status('RPM · '+rpmName(t)));
        const name=node('div','tier-list-name');name.append(orderHandle(t,index,'y'),stacked(t.name,t.description));
        const buttons=actions(tierPreviewAction(t),tierCopyAction(t),action('编辑',()=>openTier(t)),tierDeleteAction(t));
        return [name,capabilities,bindingCount(t.id)+' 把',buttons];
      }));
      const body=list.querySelector('tbody');[...body.rows].forEach((row,index)=>{row.dataset.orderId=tiers()[index].id;row.dataset.tierId=tiers()[index].id;});
      window.MirraSortable.bind(body,{disabled:()=>tierOrderPending,focusRoot:()=>$('tier-list'),onReorder:ids=>saveTierOrder(ids)});
      $('tier-list').replaceChildren(list);
    }
    else $('tier-list').replaceChildren(empty('Layers3','尚无功能档位','先创建档位，再为分发密钥分配额度与功能权限。',action('新建档位',()=>openTier(),'Plus')));
    comparison();
    orderFeedback();
    window.renderLandingTierSettings?.();
  };

  window.viewDistributedKey = async key => {
    const result = await api('keys/'+encodeURIComponent(key.id)+'/reveal',{});
    $('secret-title').textContent = key.name+' · 完整密钥'; $('secret').value = result.secret;
    $('secret-note').textContent = '仅管理员可查看此值，服务端加密保存。'; openDialog('secret-dialog');
  };
  window.confirmDeleteDistributedKey = key => { resetTierDeletion();deleting = {key,revision:state.usage[key.id].quotaRevision};$('delete-key-title').textContent='删除分发密钥';$('delete-key-confirm').textContent='删除密钥';$('delete-key-note').textContent='删除后立即停止该密钥的新请求，历史用量回执保留。';$('delete-key-name').textContent = '删除“'+key.name+'”（'+key.prefix+'…）？';openDialog('delete-key-dialog'); };
  submit('delete-key-form',async()=>{
    const target=deleting;
    if(target.tier){
      const replacement=$('delete-tier-replacement-select').value;
      if(bindingCount(target.tier.id)>0&&!tiers().some(t=>t.id===replacement&&t.id!==target.tier.id)) throw new Error('请选择其它功能档位承接绑定的密钥；没有可用档位时，请先新建替代档位或处理绑定。');
      await api('tiers',{tier:{id:target.tier.id},delete:true,revision:target.revision,replacementTierId:replacement});
    }else await api('keys/'+encodeURIComponent(target.key.id)+'/delete',{revision:target.revision});
    $('delete-key-dialog').close();await afterSave(target.tier?'档位已删除；绑定密钥仅迁移权限，已发布快照保持不变。':'密钥已删除');
  });
  submit('tier-form',async()=>{await api('tiers',{tier:readTier(),revision});$('tier-dialog').close();await afterSave('档位已保存。权限立即生效，默认额度不会修改现有密钥。');});
  $('tier-expiry-presets').addEventListener('click',event=>{
    const button=event.target.closest('[data-expiry-days]'); if(!button)return;
    const field=$('tier-form').elements.defaultExpiresInDays;
    if(button.dataset.expiryDays==='custom'){field.focus();field.select();return;}
    field.value=button.dataset.expiryDays;updateSummary();
  });
  $('tier-form').addEventListener('input',updateSummary);
  $('tier-form').addEventListener('change',updateSummary);
  $('new-tier').onclick = $('new-tier-compare').onclick = () => openTier();
  $('tier-feature-search').addEventListener('input',comparison);
  $('tier-differences').addEventListener('change',comparison);
  if (state) window.renderTiers();
})();
