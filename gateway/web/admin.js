'use strict';

const $ = id => document.getElementById(id);
const pages = { overview: '总览', pools: '上游号池', keys: '分发密钥', usage: '用量明细', models: '模型配置', proxy: '代理配置' };
const modeNames = { environment: '跟随 .env', direct: '直接连接', custom: '自定义代理' };
const usd = amount => new Intl.NumberFormat('en-US', {style:'currency',currency:'USD',minimumFractionDigits:2,maximumFractionDigits:6}).format(amount);
const number = value => Number(value).toLocaleString('zh-CN');
const fields = id => $(id).elements;
let state = null;
let loadID = 0;
let editingMember = null;
let editingKey = null;
let keyRevision = 0;
let resetKey = null;
let resetRevision = 0;
let renaming = null;
let proxyDirty = false;
let proxyRevision = 0;
let probeVersion = 0;
let opener = null;

function node(tag, className, content) {
  const el = document.createElement(tag);
  if (className) el.className = className;
  if (content != null) el.textContent = content;
  return el;
}

function icon(name) {
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  for (const [key, value] of Object.entries({class:'icon', 'aria-hidden':'true', focusable:'false', viewBox:'0 0 24 24', fill:'none', stroke:'currentColor', 'stroke-width':'1.7', 'stroke-linecap':'round', 'stroke-linejoin':'round'})) {
    svg.setAttribute(key, value);
  }
  const use = document.createElementNS(ns, 'use');
  use.setAttribute('href', '/icons.svg#' + name);
  svg.append(use);
  return svg;
}
document.querySelectorAll('[data-icon]').forEach(el => el.replaceWith(icon(el.dataset.icon)));

function feedback(el, message = '', tone = '') {
  el.textContent = message;
  el.hidden = !message;
  el.dataset.tone = tone;
}
const tell = (text, tone) => feedback($('notice'), text, tone);
const formError = (form, text = '') => feedback(form.querySelector('.error'), text, 'error');

function action(label, handler, glyph) {
  const btn = node('button');
  btn.type = 'button';
  if (glyph) btn.append(icon(glyph));
  btn.append(label);
  btn.addEventListener('click', () => runButton(btn, handler));
  return btn;
}

async function runButton(button, handler) {
  if (button.disabled) return;
  button.disabled = true;
  try { await handler(); }
  catch (error) { if (state) tell(error.message, 'error'); }
  finally { button.disabled = false; }
}

async function api(path, body) {
  const options = {cache:'no-store', credentials:'same-origin'};
  if (body !== undefined) Object.assign(options, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
  let response;
  try { response = await fetch('/admin/' + path, options); }
  catch { throw new Error('无法连接网关，请检查 SSH 隧道后重试'); }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 401 && path !== 'login') showLogin();
    throw new Error(data.detail || '网关返回 HTTP ' + response.status);
  }
  return data;
}

function showLogin() {
  loadID++;
  state = null;
  proxyDirty = false;
  window.resetModelSettings?.();
  document.querySelectorAll('dialog[open]').forEach(d => d.close());
  document.querySelectorAll('form').forEach(f => f.reset());
  $('secret').value = '';
  $('pool-member-fields').replaceChildren();
  $('app').hidden = true;
  $('auth').hidden = false;
  $('login').elements.pin.focus();
}

function navigate() {
  const candidate = location.hash.replace(/^#\/?/, '');
  const page = Object.hasOwn(pages, candidate) ? candidate : 'overview';
  document.querySelectorAll('[data-view]').forEach(view => { view.hidden = view.dataset.view !== page; });
  document.querySelectorAll('[data-page]').forEach(link => {
    if (link.dataset.page === page) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  });
  $('crumb-page').textContent = pages[page];
  document.title = pages[page] + ' · Mirra 分发网关';
  if (page === 'models') window.showModelPreview?.();
}
window.addEventListener('hashchange', () => {
  document.querySelectorAll('dialog[open]').forEach(d => d.close());
  tell('');
  navigate();
  window.scrollTo(0, 0);
});

function theme(dark) {
  document.documentElement.dataset.theme = dark ? 'dark' : 'light';
  const text = dark ? '切换浅色主题' : '切换深色主题';
  $('theme').replaceChildren(icon(dark ? 'Sun' : 'Moon'), node('span', '', text));
  $('theme').setAttribute('aria-label', text);
  $('theme').title = text;
}
let savedTheme;
try { savedTheme = localStorage.getItem('mirra-workspace-theme'); } catch { /* Browser storage is optional. */ }
theme(savedTheme ? savedTheme === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches);
$('theme').onclick = () => {
  theme(document.documentElement.dataset.theme !== 'dark');
  try { localStorage.setItem('mirra-workspace-theme', document.documentElement.dataset.theme); } catch { /* Session theme still applies. */ }
};
$('logout').setAttribute('aria-label', '退出登录');

function openDialog(id) {
  opener = document.activeElement;
  const d = $(id);
  const form = d.querySelector('form');
  if (form) formError(form);
  d.showModal();
  const first = d.querySelector('.editor-body input:not([type=hidden]):not([disabled])');
  if (first) first.focus();
}
document.querySelectorAll('[data-close]').forEach(btn => { btn.onclick = () => btn.closest('dialog').close(); });
document.querySelectorAll('dialog').forEach(d => d.addEventListener('close', () => {
  d.querySelectorAll('input[type=password]').forEach(i => { i.value = ''; });
  if (d.id === 'secret-dialog') $('secret').value = '';
  if (!document.querySelector('dialog[open]') && opener?.isConnected && !opener.closest('[hidden]')) opener.focus();
}));

function table(headers, rows, standalone = false) {
  const wrap = node('div', 'table-wrap' + (standalone ? ' standalone' : ''));
  const t = node('table');
  const h = t.createTHead().insertRow();
  for (const label of headers) {
    const th = node('th', '', label); th.scope = 'col'; h.append(th);
  }
  const b = t.createTBody();
  for (const values of rows) {
    const tr = b.insertRow();
    for (const item of values) tr.insertCell().append(item ?? '—');
  }
  wrap.append(t);
  return wrap;
}
function stacked(title, detail) {
  const cell = node('div'); cell.append(node('strong', '', title));
  if (detail) cell.append(node('small', 'mono', detail));
  return cell;
}
function empty(glyph, title, detail, btn, panel = false) {
  const el = node('div', panel ? 'empty panel' : 'empty');
  el.append(icon(glyph), node('strong', '', title));
  if (detail) el.append(node('p', '', detail));
  if (btn) el.append(btn);
  return el;
}
function status(label, tone = '') { return node('span', 'status ' + tone, label); }
function actions(...buttons) { const el = node('div','row-actions'); el.append(...buttons); return el; }
function metric(label, value, glyph) {
  const el = node('div','metric'); const caption = node('small');
  if (glyph) caption.append(icon(glyph));
  caption.append(label); el.append(caption, node('strong','',value)); return el;
}
function groups() {
  const all = new Map();
  for (const u of state.upstreams) {
    const id = JSON.stringify(u.pool ? [u.pool,u.baseUrl] : [u.id]);
    if (!all.has(id)) all.set(id, {name:u.pool || u.name, base:u.baseUrl, named:!!u.pool, members:[]});
    all.get(id).members.push(u);
  }
  return [...all.values()].sort((a,b) => a.name.localeCompare(b.name, 'zh-CN'));
}
const memberCount = group => group.members.filter(u => u.enabled).length;
function keyStatus(k) {
  if (!k.enabled) return 'disabled';
  if (k.policy.expiresAt && new Date(k.policy.expiresAt).getTime() <= Date.now()) return 'expired';
  return 'active';
}
function budgetText(item) {
  return usd(item.used) + ' / ' + (item.limit ? usd(item.limit) : '不限');
}
function sumRequests(keys, period) { return keys.reduce((sum, k) => sum + state.usage[k.id].requestCounts[period], 0); }
function sumCost(keys, period) { return keys.reduce((sum, k) => sum + Math.round(state.usage[k.id].windows[period].used * 1e6), 0) / 1e6; }

function renderOverview() {
  const gs = groups(), us = state.upstreams, ks = state.keys;
  const canIssue = us.some(u => u.enabled);
  $('overview-action').querySelector('span').textContent = canIssue ? '签发密钥' : '创建号池';
  $('overview-action').onclick = canIssue ? () => openKey() : openPool;
  $('overview-metrics').replaceChildren(
    metric('上游号池', number(gs.filter(g => g.named).length), 'Layers3'),
    metric('启用成员', number(us.filter(u => u.enabled).length), 'Server'),
    metric('启用密钥', number(ks.filter(k => keyStatus(k) === 'active').length), 'KeyRound'),
    metric('近 24 小时请求', number(sumRequests(ks, '1d')), 'Activity')
  );
  $('overview-pools').replaceChildren(gs.length ? table(['号池 / 上游','启用成员','状态'], gs.slice(0,6).map(g =>
    [stacked(g.name, g.base), memberCount(g) + ' / ' + g.members.length, status(memberCount(g) ? '可分发' : '已停用', memberCount(g) ? 'good' : '')]
  )) : empty('Layers3','还没有上游号池','添加第一组上游 API Key 后，即可签发分发密钥。',action('创建号池',openPool,'Plus')));
  $('endpoint').value = state.gateway.publicBaseUrl;
  $('overview-proxy').textContent = modeNames[state.proxy.mode] + (state.proxy.mode === 'environment' && !state.proxy.address ? ' · 直连' : '');
  $('overview-pricing').textContent = state.gateway.pricingReady ? '已配置' : '待配置';
  $('overview-usage').replaceChildren(...[['5 小时窗口','5h'],['24 小时窗口','1d'],['7 天窗口','7d']].map(([label,key])=>metric(label,usd(sumCost(ks,key)))));
}

function renderPools() {
  const query = $('pool-search').value.trim().toLowerCase();
  const gs = groups().filter(g => [g.name,g.base,...g.members.map(u=>u.name)].some(v=>v.toLowerCase().includes(query)));
  $('pool-count').textContent = gs.length + ' 组';
  $('pool-names').replaceChildren(...[...new Set(groups().filter(g=>g.named).map(g=>g.name))].map(n=>new Option(n,n)));
  if (!gs.length) {
    $('pool-list').replaceChildren(query ? empty('Search','没有匹配的号池','试试其他名称或 API 地址。',null,true) :
      empty('Layers3','还没有上游号池','可在一个号池中一次录入多把上游 API Key。',null,true));
    return;
  }
  $('pool-list').replaceChildren(...gs.map(group => {
    const panel = node('section','panel'), head = node('div','pool-header'), identity = node('div');
    const title = node('h2','pool-title'); title.append(icon(group.named ? 'Layers3' : 'Server'),group.name);
    identity.append(title,node('p','mono',group.base));
    const bar = actions();
    if (group.named) {
      if (!group.members.some(u=>u.id === 'openai-env')) bar.append(action('重命名',()=>openRename(group)));
      bar.append(action('添加成员',()=>openMember(null,group),'Plus'));
    }
    head.append(identity,bar);
    const rows = group.members.map(u => {
      const controls = actions(action('测试',async () => {
        const result = await api('upstreams/'+encodeURIComponent(u.id)+'/test',{});
        tell(u.name+' · HTTP '+result.upstreamStatus,result.ok ? 'good' : 'error');
      }));
      if (u.id !== 'openai-env') controls.append(action('编辑',()=>openMember(u,group)));
      return [stacked(u.name, u.id==='openai-env' ? '.env 管理' : ''), status(u.enabled ? '已启用':'已停用',u.enabled ? 'good':''),controls];
    });
    panel.append(head,table(['成员','状态','操作'],rows)); return panel;
  }));
}

function renderKeys() {
  const q = $('key-search').value.trim().toLowerCase(), filter = $('key-filter').value;
  const keys = state.keys.filter(k=>(!filter || keyStatus(k)===filter) && [k.name,k.prefix].some(v=>v.toLowerCase().includes(q)));
  const canIssue = state.upstreams.some(u=>u.enabled);
  $('new-key').disabled = !canIssue;
  $('new-key').title = canIssue ? '' : '先添加并启用上游成员';
  $('key-count').textContent = keys.length + ' 把';
  if (!keys.length) {
    $('key-list').replaceChildren(q || filter ? empty('Search','没有匹配的密钥','可更改状态或搜索词。',null,true) :
      empty('KeyRound','还没有分发密钥',canIssue ? '签发后可将密钥用于手机、桌面客户端或 SDK。' : '先创建号池并启用至少一个上游成员。',canIssue ? null : action('管理号池',()=>{location.hash='/pools';}),true));
    return;
  }
  const rows=keys.map(k=>{
    const u=state.upstreams.find(u=>u.id===k.upstreamId);
    const label={active:'已启用',disabled:'已停用',expired:'已到期'}[keyStatus(k)];
    const ends=k.policy.expiresAt ? new Date(k.policy.expiresAt).toLocaleString('zh-CN',{hour12:false}) : '长期有效';
    const windows=state.usage[k.id].windows;
    const costs=node('div','budgets');
    for (const [id,label] of [['5h','5h'],['1d','1d'],['7d','7d']]) costs.append(node('small','',label+'  '+budgetText(windows[id])));
    return [stacked(k.name,k.prefix+'…'),u?.pool || u?.name || '—',status(label,keyStatus(k)==='active'?'good':keyStatus(k)==='expired'?'warn':''),costs,ends,actions(action('编辑',()=>openKey(k)),action('用量',()=>{$('usage-filter').value=k.id;renderUsage();location.hash='/usage';}),action('重置额度',()=>openReset(k)),action(k.enabled?'停用':'启用',async()=>{
      await api('keys',{...k,revision:state.usage[k.id].quotaRevision,enabled:!k.enabled});
      await afterSave('密钥已'+(k.enabled?'停用':'启用'));
    }))];
  });
  $('key-list').replaceChildren(table(['密钥','号池 / 上游','状态','已用 / 限额（USD）','有效期','操作'],rows,true));
}

function renderUsage() {
  const filter=$('usage-filter'), selected=filter.value;
  filter.replaceChildren(new Option('全部密钥',''),...state.keys.map(k=>new Option(k.name,k.id)));
  if (state.keys.some(k=>k.id===selected)) filter.value=selected;
  const keys=state.keys.filter(k=>!filter.value || k.id===filter.value);
  $('usage-metrics').replaceChildren(...[['5 小时窗口费用','5h'],['24 小时窗口费用','1d'],['7 天窗口费用','7d']].map(([label,id])=>metric(label,usd(sumCost(keys,id)),'Activity')));
  $('usage-detail').hidden=!filter.value;
  if(filter.value){
    const budgets=state.usage[filter.value].usdBudget;
    $('window-detail').replaceChildren(table(['额度','已用','剩余 / 上限','下次刷新'],Object.entries(budgets).map(([w,v])=>{
      const until=v.reset_at?new Date(v.reset_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):(w==='total'?'长期':'首次请求后起算');
      return [{'total':'累计','5h':'5 小时','1d':'24 小时','7d':'7 天'}[w],usd(v.used),v.limit?usd(v.remaining)+' / '+usd(v.limit):'不限',until];
    })));
  }
  const history=keys.flatMap(k=>state.usage[k.id].quotaEvents.map(event=>({...event,name:k.name}))).sort((a,b)=>b.id-a.id);
  $('quota-history').replaceChildren(history.length ? table(['时间','密钥','操作'],history.slice(0,64).map(e=>
    [new Date(e.changedAt*1000).toLocaleString('zh-CN',{hour12:false}),e.name,e.message])) : empty('Clock3','暂无额度变动','修改限额、手动重置和到期刷新后，操作会记录在这里。'));
  if (!keys.length) {
    $('usage-list').replaceChildren(empty('ChartNoAxesColumnIncreasing','暂无用量记录','分发密钥发起请求后，可在这里查看费用和 token 回执。'));
    return;
  }
  $('usage-list').replaceChildren(table(['密钥','请求次数','已计量 token','已结算 USD','待结算 USD'],keys.map(k=>{
    const total=state.usage[k.id].usage.total;
    return [stacked(k.name,k.prefix+'…'),number(total.requests),total.meteredRequests ? number(total.total_tokens) : '—',
      total.pricedRequests || !total.requests ? usd(total.settledMicroUSD/1e6) : '—',usd(total.reservedMicroUSD/1e6)];
  })));
}

const pf = $('proxy-form');
function syncProxy() {
  const mode=pf.elements.mode.value, stored=state?.proxy;
  $('proxy-fields').hidden=mode!=='custom';
  pf.elements.url.disabled=pf.elements.scope.disabled=mode!=='custom';
  pf.elements.url.required=mode==='custom' && stored?.mode!=='custom';
  $('proxy-mode-hint').textContent={
    environment:'使用服务器 MIRRA_EGRESS_PROXY 的配置，留空时直接连接。',
    direct:'网关从服务器直接连接上游。',
    custom: stored?.mode==='custom' ? '留空沿用已保存的代理，填写新地址可替换凭证。' : '配置服务器访问上游使用的代理。'
  }[mode];
}
function resetProxy() {
  proxyDirty=false; probeVersion++;
  pf.reset(); pf.elements.mode.value=state.proxy.mode; pf.elements.scope.value=state.proxy.scope;
  proxyRevision=state.proxy.revision;
  syncProxy(); formError(pf); feedback($('probe-result'));
}
function renderProxy() {
  const p=state.proxy;
  const rows=[['模式',modeNames[p.mode]],['实际出口',p.address || '服务器直连'],['适用范围',p.scope==='openai'?'OpenAI 官方':'全部上游']];
  if (p.address) rows.push(['代理认证',p.authenticated?'已设置':'未设置']);
  $('active-proxy').replaceChildren(...rows.map(([name,value])=>{const item=node('div');item.append(node('dt','',name),node('dd','',value));return item;}));
  $('allowed-hosts').replaceChildren(...state.gateway.allowedHosts.map(h=>node('li','',h)));
  if (!proxyDirty) resetProxy();
}
function proxyInput() {
  return {mode:pf.elements.mode.value,scope:pf.elements.scope.value,url:pf.elements.mode.value==='custom'?pf.elements.url.value:'',revision:proxyRevision};
}
pf.addEventListener('input',()=>{proxyDirty=true;probeVersion++;syncProxy();feedback($('probe-result'));formError(pf);});
$('reset-proxy').onclick=async()=>{try{await refresh();if(state)resetProxy();}catch(e){formError(pf,e.message);}};
$('probe-proxy').onclick=()=>runButton($('probe-proxy'),async()=>{
  if(!pf.reportValidity()) return;
  const version=++probeVersion;
  feedback($('probe-result'),'正在连接 OpenAI 模型接口…');
  try {
    const result=await api('proxy/test',proxyInput());
    if (version===probeVersion) feedback($('probe-result'),result.message+' · '+(result.elapsedMs/1000).toFixed(2)+' 秒'+(result.upstreamStatus?' · HTTP '+result.upstreamStatus:''),result.ok?'good':'error');
  } catch(e) { if(version===probeVersion) feedback($('probe-result'),e.message,'error'); }
});

function addPoolRow() {
  const box=$('pool-member-fields');
  const row=node('div','member-fields');
  for (const [label,name,placeholder,type] of [['成员名称','memberName','例如 主账号','text'],['上游 API Key','memberSecret','sk-…','password']]) {
    const wrap=node('label','',label), input=node('input');
    Object.assign(input,{name,placeholder,type,required:true,maxLength:type==='text'?120:4096,autocomplete:type==='text'?'off':'new-password'});
    wrap.append(input);row.append(wrap);
  }
  const del=action('',()=>{row.remove();syncPoolRows();},'Trash2');
  del.className='icon-button';del.setAttribute('aria-label','移除这一行');row.append(del);
  box.append(row);syncPoolRows();return row;
}
function syncPoolRows() {
  const rows=$('pool-member-fields').children;
  for (const row of rows) row.querySelector('button').disabled=rows.length===1;
  $('add-pool-row').disabled=rows.length>=64;
}
function openPool() {
  $('pool-form').reset();$('pool-member-fields').replaceChildren();addPoolRow();openDialog('pool-dialog');
}
function openRename(group) {
  renaming={id:group.members[0].id,previous:group.name};
  fields('rename-form').name.value=group.name;openDialog('rename-dialog');
}
function openMember(member,group) {
  editingMember=member; const form=$('member-form'),f=form.elements;form.reset();
  $('member-title').textContent=member?'编辑成员':'添加成员';
  f.name.value=member?.name || '';f.pool.value=member?.pool ?? (group.named?group.name:'');
  f.baseUrl.value=member?.baseUrl || group.base;f.apiKey.value='';f.enabled.checked=member?.enabled ?? true;
  f.pool.readOnly=f.baseUrl.readOnly=!member;
  $('member-hint').textContent=member?'留空保留现有密钥。修改 API 地址时须重新填写密钥。':'此密钥只保存在网关服务端。';
  memberRequired();openDialog('member-dialog');
}
function memberRequired() {
  const f=fields('member-form');
  f.apiKey.required=!editingMember || f.baseUrl.value.trim().replace(/\/+$/,'')!==editingMember.baseUrl;
}
fields('member-form').baseUrl.addEventListener('input',memberRequired);

function localDate(date) { return new Date(date.valueOf()-date.getTimezoneOffset()*60000).toISOString().slice(0,16); }
function syncKey() {
  const f=fields('key-form');
  for(const c of $('key-form').querySelectorAll('[data-reveal]')) {
    const area=$(c.dataset.reveal);area.hidden=!c.checked;
    for(const field of area.querySelectorAll('input, textarea, button')) field.disabled=area.hidden;
  }
  f.customKey.required=f.customOn.checked; f.ips.required=f.ipOn.checked; f.expiresAt.required=f.expiryOn.checked;
}
function openKey(k=null) {
  editingKey=k; keyRevision=k?state.usage[k.id].quotaRevision:0; const form=$('key-form'), f=form.elements; form.reset();
  const choices=[];
  for(const group of groups()) {
    const current=group.members.find(u=>u.id===k?.upstreamId);
    const member=current || group.members.find(u=>u.enabled);
    if(!member) continue;
    choices.push(new Option((group.named ? group.name : member.name+'（独立上游）')+' · '+group.base,member.id));
  }
  f.upstreamId.replaceChildren(...choices);
  $('key-title').textContent=$('save-key').textContent=k?'保存密钥修改':'签发密钥';
  $('custom-line').hidden=!!k;f.customOn.disabled=!!k;
  $('pricing-hint').textContent=state.gateway.pricingReady?'USD 为单位，0 表示不限。每次请求先预留 '+usd(state.gateway.reserveMicroUSD/1e6)+'，收到可计价回执后结算。':'服务器需先配置模型费率和预留金额才可启用 USD 限额。';
  for(const b of $('expiry-presets').querySelectorAll('button')) b.setAttribute('aria-pressed','false');
  if(k) {
    const p=k.policy; f.name.value=k.name;f.upstreamId.value=k.upstreamId;f.enabled.checked=k.enabled;
    f.ipOn.checked=!!p.ips?.length;f.ips.value=(p.ips||[]).join('\n');
    f.expiryOn.checked=!!p.expiresAt;f.expiresAt.value=p.expiresAt?localDate(new Date(p.expiresAt)):'';
    f.usdOn.checked=[p.totalMicroUSD,p.fiveHourMicroUSD,p.dayMicroUSD,p.weekMicroUSD].some(Boolean);
    for(const [field,prop] of [['totalUsd','totalMicroUSD'],['fiveHourUsd','fiveHourMicroUSD'],['dayUsd','dayMicroUSD'],['weekUsd','weekMicroUSD']]) f[field].value=p[prop]/1e6;
  }
  syncKey();openDialog('key-dialog');
}
$('key-form').addEventListener('change',e=>{if(e.target.dataset.reveal)syncKey();});
$('expiry-presets').addEventListener('click',e=>{
  const btn=e.target.closest('[data-days]');if(!btn)return;
  const days=Number(btn.dataset.days),field=fields('key-form').expiresAt;
  if(days){const d=new Date();d.setDate(d.getDate()+days);field.value=localDate(d);}
  for(const b of $('expiry-presets').children)b.setAttribute('aria-pressed',String(b===btn));
  if(!days)field.focus();
});
fields('key-form').expiresAt.addEventListener('input',()=>{for(const b of $('expiry-presets').children)b.setAttribute('aria-pressed',String(b.dataset.days==='0'));});

function submit(id, handler) {
  const form=$(id);
  form.addEventListener('submit',async event=>{
    event.preventDefault();
    if(form.dataset.pending==='true') return;
    form.dataset.pending='true';
    const button=form.querySelector('button[type=submit]');button.disabled=true;formError(form);
    try { await handler(form); }
    catch(error) { formError(form,error.message); }
    finally { delete form.dataset.pending;button.disabled=false; }
  });
}

async function refresh() {
  const id=++loadID;
  const fresh=await api('state');
  if(id!==loadID)return;
  state=fresh;$('auth').hidden=true;$('app').hidden=false;
  renderOverview();renderPools();renderKeys();renderUsage();renderProxy();window.renderModelSettings?.();navigate();
  $('last-sync').textContent='更新于 '+new Date().toLocaleTimeString('zh-CN',{hour12:false});
}
async function afterSave(message) {
  tell(message,'good');
  try { await refresh(); }
  catch(e) { if(state)tell(message+'。重新读取列表失败，可点击刷新。','error'); }
}
submit('login',async form=>{
  await api('login',{pin:form.elements.pin.value});form.reset();await refresh();
});
submit('pool-form',async form=>{
  const members=[...$('pool-member-fields').children].map(row=>({name:row.querySelector('[name=memberName]').value,apiKey:row.querySelector('[name=memberSecret]').value}));
  await api('pools',{name:form.elements.name.value,baseUrl:form.elements.baseUrl.value,members});
  $('pool-dialog').close();await afterSave('已创建号池，已添加 '+members.length+' 个成员');
});
submit('rename-form',async form=>{
  await api('pools/rename',{...renaming,name:form.elements.name.value});$('rename-dialog').close();await afterSave('已更新号池名称');
});
submit('member-form',async form=>{
  const f=form.elements;
  await api('upstreams',{id:editingMember?.id || '',name:f.name.value,pool:f.pool.value,baseUrl:f.baseUrl.value,apiKey:f.apiKey.value,enabled:f.enabled.checked});
  $('member-dialog').close();await afterSave('已保存上游成员');
});
submit('key-form',async form=>{
  const f=form.elements;
  const micro=field=>f.usdOn.checked?Math.round(Number(f[field].value||0)*1e6):0;
  let end=null;
  if(f.expiryOn.checked)end=editingKey?.policy.expiresAt && f.expiresAt.value===localDate(new Date(editingKey.policy.expiresAt)) ? editingKey.policy.expiresAt : new Date(f.expiresAt.value).toISOString();
  const issued=await api('keys',{
    id:editingKey?.id||'',revision:editingKey?keyRevision:null,name:f.name.value,upstreamId:f.upstreamId.value,enabled:f.enabled.checked,
    customKey:f.customOn.checked?f.customKey.value:'',
    policy:{ips:f.ipOn.checked?f.ips.value.split(/[\s,]+/).filter(Boolean):[],expiresAt:end,totalMicroUSD:micro('totalUsd'),
      fiveHourMicroUSD:micro('fiveHourUsd'),dayMicroUSD:micro('dayUsd'),weekMicroUSD:micro('weekUsd')}
  });
  $('key-dialog').close();
  if(issued.secret){$('secret').value=issued.secret;$('secret-note').textContent='完整值仅显示一次，请复制后妥善保存。';openDialog('secret-dialog');}
  await afterSave(issued.secret?'已签发分发密钥':'已保存密钥修改');
});
function showResetAmounts() {
  const scope=fields('reset-form').scope.value;
  const rows=Object.entries(state.usage[resetKey.id].usdBudget).filter(([w])=>scope==='all'||w===scope||(scope==='windows'&&w!=='total'));
  $('reset-amounts').replaceChildren(...rows.map(([w,v])=>{
    const row=node('div');row.append(node('dt','',{'total':'累计','5h':'5 小时','1d':'24 小时','7d':'7 天'}[w]),node('dd','',usd(v.used)+' 已用'));return row;
  }));
}
function openReset(k) {
  resetKey=k;resetRevision=state.usage[k.id].quotaRevision;
  $('reset-form').reset();$('reset-key-name').textContent=k.name+' · '+k.prefix+'…';
  showResetAmounts();openDialog('reset-dialog');
}
fields('reset-form').scope.onchange=showResetAmounts;
submit('reset-form',async form=>{
  const result=await api('keys/'+encodeURIComponent(resetKey.id)+'/reset',{scope:form.elements.scope.value,revision:resetRevision});
  state.usage[resetKey.id]=result.usage;
  $('reset-dialog').close();await afterSave('已重置所选额度，用量记录已保留');
});
submit('proxy-form',async()=>{
  const current=await api('proxy',proxyInput());
  state.proxy=current;resetProxy();await afterSave('代理配置已生效');
});

async function copy(input, target) {
  try { await navigator.clipboard.writeText(input.value); if(target)target.textContent='已复制';else tell('客户端 API 地址已复制','good'); }
  catch { input.focus();input.select();if(target)target.textContent='已选中，请复制密钥。';else tell('已选中，请复制地址。'); }
}
$('copy-key').onclick=()=>copy($('secret'),$('secret-note'));
$('copy-endpoint').onclick=()=>copy($('endpoint'));
$('new-pool').onclick=openPool;$('new-key').onclick=()=>openKey();
$('add-pool-row').onclick=()=>addPoolRow().querySelector('input').focus();
$('pool-search').oninput=renderPools;$('key-search').oninput=renderKeys;$('key-filter').onchange=renderKeys;$('usage-filter').onchange=renderUsage;
$('refresh').onclick=()=>runButton($('refresh'),async()=>{await refresh();tell('数据已刷新','good');});
$('logout').onclick=()=>runButton($('logout'),async()=>{await api('logout',{});showLogin();formError($('login'));});
refresh().catch(error=>{if(error.message!=='请先登录')formError($('login'),error.message);});
