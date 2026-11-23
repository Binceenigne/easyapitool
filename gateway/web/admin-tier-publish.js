"use strict";
(() => {
  let draftIds = null, snapshotSignature = '', optionsSignature = '', pending = false, pendingVisibility = null, previewMode = 'draft';
  const plans = () => state?.appModels?.tiers || [];
  const publication = () => ({enabled:false,tierIds:[],revision:0,publishedAt:null,sourceRevision:0,tiers:[],...state?.landingTiers});
  const amount = micro => Number(micro) ? usd(Number(micro)/1e6) : '不限';
  const quota = tier => ({expiresInDays:0,totalMicroUSD:0,fiveHourMicroUSD:0,dayMicroUSD:0,weekMicroUSD:0,...tier.defaultQuota});
  const validity = days => Number(days) ? '签发后 '+number(days)+' 天' : '长期有效';
  const hasFormat = (tier, format) => (tier.formats || []).includes('*') || (tier.formats || []).includes(format);
  const displayResolution = tier => ({1536:'标清',2560:'高清',3840:'超清'}[tier.maxResolution] || tier.maxResolution+' px');
  const sameIds = (left,right) => JSON.stringify([...left].sort()) === JSON.stringify([...right].sort());

  function renderPreview(current) {
    const snapshot = previewMode === 'snapshot';
    const selected = snapshot ? current.tiers : plans().filter(tier => draftIds.has(tier.id));
    $('landing-preview-draft').setAttribute('aria-pressed',String(!snapshot));
    $('landing-preview-snapshot').setAttribute('aria-pressed',String(snapshot));
    $('landing-preview-snapshot').disabled = !current.publishedAt;
    $('landing-preview-snapshot').title = current.publishedAt ? '查看上次同步保存的内容' : '同步后可查看已发布快照';
    $('landing-preview-hint').textContent = snapshot
      ? current.enabled ? '当前落地页展示的已同步内容。可横向滚动对比档位，纵向查看更多功能。' : '已保存的公开快照；落地页当前已隐藏。可横向滚动对比档位。'
      : '同步后将展示以下 '+selected.length+' 个档位，未同步的修改不会影响当前落地页。可横向滚动对比档位，纵向查看更多功能。';
    const preview = $('landing-tier-preview');
    preview.className = 'landing-preview-table-wrap';
    preview.replaceChildren();
    if (!selected.length) {
      preview.append(empty('Layers3',snapshot?'尚无已同步档位':'选择要展示的档位',snapshot?'先同步所选档位，再核对公开快照。':'勾选左侧档位即可预览。',null,false));
      return;
    }
    const table = node('table','landing-preview-table');
    table.append(node('caption','sr-only',snapshot?'已发布档位的额度与功能对比':'待同步档位的额度与功能对比'));
    const header = table.createTHead().insertRow(), corner = node('th','','额度 / 功能');
    corner.scope = 'col'; header.append(corner);
    for (const tier of selected) {
      const cell = node('th'), name = node('strong','',tier.name), description = node('small','hint',tier.description || '按功能需求选择适合的档位。');
      cell.scope = 'col'; description.title = description.textContent;
      cell.append(name,description); header.append(cell);
    }
    const allowed = (list,names={}) => (list || []).includes('*') ? '全部' : (list || []).length ? list.map(value=>names[value] || value).join('、') : '未开放';
    const rows = [
      ['总创作额度',tier=>amount(quota(tier).totalMicroUSD),'amount'],
      ['5 小时额度',tier=>amount(quota(tier).fiveHourMicroUSD)],
      ['每日额度',tier=>amount(quota(tier).dayMicroUSD)],
      ['7 天额度',tier=>amount(quota(tier).weekMicroUSD)],
      ['有效期',tier=>validity(quota(tier).expiresInDays)],
      ['最大分辨率',tier=>displayResolution(tier)+' · '+number(tier.maxResolution)+' px'],
      ['每次生成张数',tier=>'最多 '+number(tier.maxImages)+' 张'],
      ['参考图上限',tier=>tier.imageEditing?'最多 '+number(tier.maxReferences)+' 张':'未开放'],
      ['图片质量',tier=>allowed(tier.qualities,{low:'低',medium:'中',high:'高',xhigh:'超高',max:'最高'})],
      ['构图复杂度',tier=>allowed(tier.complexities,{low:'低',medium:'中',high:'高'})],
      ['透明背景',tier=>tier.transparency],
      ['SVG 矢量图',tier=>hasFormat(tier,'svg')],
      ['GIF 动图',tier=>hasFormat(tier,'gif')],
      ['GIF 帧数上限',tier=>hasFormat(tier,'gif')?number(tier.maxGifFrames)+' 帧':'—'],
      ['高级模式',tier=>tier.advanced],
      ['联网搜索',tier=>tier.webSearch],
      ['提示词润色',tier=>tier.promptPolish],
      ['参考图与编辑',tier=>tier.imageEditing],
      ['输出格式',tier=>allowed(tier.formats,{png:'PNG',jpeg:'JPEG',webp:'WebP',svg:'SVG',gif:'GIF'})],
      ['思维深度预设',tier=>allowed(tier.reasoningModes,{instant:'Instant',flash:'Flash',medium:'Medium',high:'High',extra:'Extra',max:'Max'})],
      ['模型思考深度',tier=>allowed(tier.reasoningEfforts,{none:'不启用',minimal:'最少',low:'低',medium:'中',high:'高',xhigh:'超高',max:'最高'})],
      ['生图模型',tier=>allowed(tier.imageModels)],
      ['思维模型',tier=>allowed(tier.reasoningModels)]
    ];
    const body=table.createTBody();
    for (const [label,getValue,kind] of rows) {
      const row=body.insertRow(), title=node('th','',label); title.scope='row'; row.append(title);
      if (kind) row.className='landing-preview-'+kind+'-row';
      for (const tier of selected) {
        const value=getValue(tier), cell=row.insertCell();
        if (typeof value==='boolean') cell.append(status(value?'允许':'未开放',value?'good':'tier-unavailable'));
        else cell.append(node(kind==='amount'?'strong':'span',kind==='amount'?'number':'',value));
      }
    }
    preview.append(table);
  }

  window.renderLandingTierSettings = () => {
    if (!state) return;
    const current=publication(), catalog=plans();
    const signature=JSON.stringify([current.tierIds,current.publishedAt,current.sourceRevision]);
    if (draftIds === null || snapshotSignature !== signature) {
      draftIds=new Set(current.tierIds); snapshotSignature=signature;
    }
    const available=new Set(catalog.map(tier => tier.id));
    draftIds=new Set([...draftIds].filter(id=>available.has(id)));
    const catalogSignature=JSON.stringify(catalog.map(tier => [tier.id,tier.name,tier.maxResolution,tier.defaultQuota]));
    if (optionsSignature !== catalogSignature) {
      optionsSignature=catalogSignature;
      $('landing-tier-options').replaceChildren(...catalog.map(tier => {
        const row=node('label','landing-tier-option'), input=node('input'), words=node('span');
        input.type='checkbox';input.value=tier.id;input.checked=draftIds.has(tier.id);
        words.append(node('strong','',tier.name),node('small','caption',displayResolution(tier)+' · 总创作额度 '+amount(quota(tier).totalMicroUSD)));
        row.append(input,words);
        input.addEventListener('change',()=>{if(input.checked)draftIds.add(tier.id);else draftIds.delete(tier.id);window.renderLandingTierSettings();});
        return row;
      }));
    }
    for (const input of $('landing-tier-options').querySelectorAll('input')) input.checked=draftIds.has(input.value);
    const hasSnapshot=!!current.publishedAt;
    const dirty=!hasSnapshot || Number(current.sourceRevision)!==Number(state.appModels.revision) || !sameIds(draftIds,current.tierIds);
    $('landing-tier-publish-state').replaceChildren(status(!hasSnapshot?'尚未同步':dirty?'有未同步变更':'已同步',!hasSnapshot?'':dirty?'warn':'good'),status(current.enabled?'落地页显示中':'落地页已隐藏',current.enabled?'good':''));
    $('landing-tier-published-at').textContent = current.publishedAt ? '上次同步 '+new Date(current.publishedAt).toLocaleString('zh-CN',{hour12:false}) : '尚未同步到落地页';
    $('landing-tier-visibility-hint').textContent = hasSnapshot && current.tiers.length
      ? '显示开关只控制已同步内容；修改档位或勾选范围后，需要重新同步。'
      : '首次使用默认隐藏。选择档位并完成同步后，即可开启落地页展示。';
    const visible=$('landing-tier-enabled'); visible.checked=pendingVisibility ?? current.enabled;visible.disabled=pending || !hasSnapshot || !current.tiers.length;
    visible.title=hasSnapshot&&current.tiers.length?'只切换已同步内容的显示状态':'请先选择档位并同步到落地页';
    $('landing-tier-selection').disabled=pending;
    const selectAll=$('landing-tier-select-all');selectAll.checked=catalog.length>0&&draftIds.size===catalog.length;selectAll.indeterminate=draftIds.size>0&&draftIds.size<catalog.length;
    $('landing-tier-count').textContent=draftIds.size+' / '+catalog.length;
    const sync=$('landing-tier-sync');sync.disabled=pending || !draftIds.size || !dirty;sync.querySelector('span').textContent=pending?'正在保存…':'同步到落地页';
    sync.title=!draftIds.size?'请至少选择一个要公开的档位':!dirty?'所选档位已同步':'';
    sync.querySelector('svg')?.classList.toggle('landing-publish-spinning',pending);
    document.querySelector('.landing-tier-publish').setAttribute('aria-busy',String(pending));
    if (previewMode==='snapshot'&&!hasSnapshot) previewMode='draft';
    renderPreview(current);
  };

  async function savePublication(action,enabled) {
    if (pending || !state) return;
    const current=publication();
    const ids=plans().filter(tier=>draftIds?.has(tier.id)).map(tier=>tier.id);
    if (action==='sync'&&!ids.length) { feedback($('landing-tier-error'),'请至少选择一个要公开的档位。','error'); return; }
    if (action==='visibility'&&enabled&&!current.publishedAt) return;
    feedback($('landing-tier-error'));feedback($('landing-tier-feedback'));
    pendingVisibility=action==='visibility'?enabled:null;pending=true;window.renderLandingTierSettings();
    try {
      const saved=await api('landing-tiers',{action,enabled,revision:current.revision,...(action==='sync'?{tierIds:ids}:{})});
      state.landingTiers=saved;
      const message=action==='sync' ? enabled ? '所选档位已同步并展示到落地页。' : '已同步，开启展示后可在落地页看到。'
        : enabled ? '落地页已显示上次同步的档位。' : '落地页档位已隐藏，已同步内容会保留。';
      await afterSave(message);feedback($('landing-tier-feedback'),message,'good');
    } catch (error) {
      feedback($('landing-tier-error'),error.message,'error');
      try { await refresh(); } catch { /* Keep the error and current draft available for retry. */ }
    } finally { pending=false;pendingVisibility=null;window.renderLandingTierSettings(); }
  }

  $('landing-tier-select-all').addEventListener('change',event=>{draftIds=event.target.checked?new Set(plans().map(tier=>tier.id)):new Set();window.renderLandingTierSettings();});
  $('landing-tier-enabled').addEventListener('change',event=>{void savePublication('visibility',event.target.checked);});
  $('landing-tier-sync').addEventListener('click',()=>{void savePublication('sync',publication().enabled);});
  $('landing-preview-draft').addEventListener('click',()=>{previewMode='draft';window.renderLandingTierSettings();});
  $('landing-preview-snapshot').addEventListener('click',()=>{previewMode='snapshot';window.renderLandingTierSettings();});
  window.resetLandingTierSettings=()=>{draftIds=null;snapshotSignature='';optionsSignature='';previewMode='draft';pending=false;pendingVisibility=null;feedback($('landing-tier-error'));feedback($('landing-tier-feedback'));};
  if (state) window.renderLandingTierSettings();
})();
