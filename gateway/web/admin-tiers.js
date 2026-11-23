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
  const tierFor = key => tiers().find(t => t.id === (key?.policy?.tierId || 'default'));
  const bindingCount = id => state.keys.filter(k => (k.policy.tierId || 'default') === id).length;
  const limitName = t => ({1536:'标清',2560:'高清',3840:'超清'}[t.maxResolution] || '—');
  const allowedText = (list, catalog) => (list || []).includes('*') ? '全部' : (list || []).length ? list.map(v => catalog?.find(([id]) => id === v)?.[1] || v).join('、') : '未开放';

  function summary(t, target) {
    target.replaceChildren();
    if (!t) return;
    target.append(node('strong','',t.name),node('p','hint',t.description || '独立管理模型与生成功能权限'));
    const tags = node('div','tier-tags');
    tags.append(status(limitName(t)),status('最多 '+t.maxImages+' 张'),status(all(t.formats,'svg')?'SVG':'静态位图'));
    if (all(t.formats,'gif')) tags.append(status('GIF · '+t.maxGifFrames+' 帧'));
    if (t.transparency) tags.append(status('透明背景'));
    if (t.advanced) tags.append(status('高级思维'));
    target.append(tags);
  }

  window.keyTierBadge = key => status(tierFor(key)?.name || '档位不可用','tier-badge');
  window.prepareKeyTier = key => {
    const select = $('key-tier-select');
    select.replaceChildren(...tiers().map(t => new Option(t.name,t.id)));
    select.value = key?.policy?.tierId || 'default';
    summary(tiers().find(t => t.id === select.value),$('key-tier-summary'));
  };
  $('key-tier-select').addEventListener('change', e => summary(tiers().find(t => t.id === e.target.value),$('key-tier-summary')));

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
    for (const name of ['maxResolution','maxImages','maxReferences','maxGifFrames']) t[name] = Number(f[name].value);
    for (const [name] of switches) t[name] = f[name].checked;
    return t;
  }
  function updateSummary() {
    const t = readTier(); t.name ||= '新档位'; summary(t,$('tier-live-summary'));
  }

  function openTier(t = null, duplicate = false) {
    const baseline = tiers().find(t => t.id === 'default');
    const value = structuredClone(t || baseline);
    editing = t && !duplicate ? t : null; revision = state.appModels.revision;
    if (!t) { value.name = ''; value.description = ''; }
    if (duplicate) value.name += ' · 副本';
    const f = $('tier-form').elements;
    f.name.value = value.name; f.description.value = value.description;
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
    for (const name of ['maxResolution','maxImages','maxReferences','maxGifFrames']) f[name].value = value[name];
    updateSummary(); openDialog('tier-dialog');
  }

  function comparison() {
    const plans = tiers(), query = $('tier-feature-search').value.trim().toLowerCase();
    const definitions = [
      ['说明',t => t.description || '—'],['生图模型',t => allowedText(t.imageModels)],['思维模型',t => allowedText(t.reasoningModels)],
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
      const cell = node('th'), box = node('div','tier-column-title'); cell.scope = 'col';
      box.append(node('small','caption','档位 '+(index+1)),node('strong','',t.name),action('编辑',()=>openTier(t)));
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
    wrap.append(grid); $('tier-comparison').replaceChildren(wrap);
    if (!count) $('tier-comparison').append(empty('Search','没有匹配的差异','可关闭“只看不同项”或更换搜索词。'));
  }

  window.renderTiers = () => {
    if (!state || !tiers().length) return;
    $('tier-list').replaceChildren(table(['档位','能力范围','绑定密钥','操作'],tiers().map(t => {
      const capabilities = node('div','tier-tags');capabilities.append(status(limitName(t)),status(t.advanced?'高级思维':'预设思维'),status(all(t.formats,'gif')?'含 GIF':'静态输出'));
      const buttons = actions(action('编辑',()=>openTier(t)),action('复制',()=>openTier(t,true)));
      if (t.id !== 'default') { const del = action('删除',()=>{ deleting={tier:t,revision:state.appModels.revision};$('delete-key-title').textContent='删除功能档位';$('delete-key-name').textContent='删除“'+t.name+'”？';$('delete-key-note').textContent='该档位未绑定密钥，删除后可重新创建。';$('delete-key-confirm').textContent='删除档位';openDialog('delete-key-dialog'); });del.classList.add('danger-text');del.disabled = bindingCount(t.id)>0;del.title = del.disabled?'请先为绑定的密钥选择其他档位':'';buttons.append(del); }
      return [stacked(t.name,t.description),capabilities,bindingCount(t.id)+' 把',buttons];
    })));
    comparison();
  };

  window.viewDistributedKey = async key => {
    const result = await api('keys/'+encodeURIComponent(key.id)+'/reveal',{});
    $('secret-title').textContent = key.name+' · 完整密钥'; $('secret').value = result.secret;
    $('secret-note').textContent = '仅管理员可查看此值，服务端加密保存。'; openDialog('secret-dialog');
  };
  window.confirmDeleteDistributedKey = key => { deleting = {key,revision:state.usage[key.id].quotaRevision};$('delete-key-title').textContent='删除分发密钥';$('delete-key-confirm').textContent='删除密钥';$('delete-key-note').textContent='删除后立即停止该密钥的新请求，历史用量回执保留。';$('delete-key-name').textContent = '删除“'+key.name+'”（'+key.prefix+'…）？';openDialog('delete-key-dialog'); };
  submit('delete-key-form',async()=>{if(deleting.tier){await api('tiers',{tier:{id:deleting.tier.id},delete:true,revision:deleting.revision});}else{await api('keys/'+encodeURIComponent(deleting.key.id)+'/delete',{revision:deleting.revision});}$('delete-key-dialog').close();await afterSave(deleting.tier?'档位已删除':'密钥已删除');});
  submit('tier-form',async()=>{await api('tiers',{tier:readTier(),revision});$('tier-dialog').close();await afterSave('档位权限已保存并同步');});
  $('tier-form').addEventListener('input',updateSummary);
  $('tier-form').addEventListener('change',updateSummary);
  $('new-tier').onclick = $('new-tier-compare').onclick = () => openTier();
  $('tier-feature-search').addEventListener('input',comparison);
  $('tier-differences').addEventListener('change',comparison);
  if (state) window.renderTiers();
})();
