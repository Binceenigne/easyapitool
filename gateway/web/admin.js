'use strict';

const $ = id => document.getElementById(id);
const keyForm = $('key');
const upstreamForm = $('upstream');

function message(text = '') {
  $('message').textContent = text;
  $('message').hidden = !text;
}

async function api(path, body) {
  const options = body === undefined ? {} : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
  };
  const response = await fetch(`/admin/${path}`, options);
  const data = await response.json();
  if (response.status === 401 && path !== 'login') showLogin();
  if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
  return data;
}

function showLogin() {
  $('settings').hidden = $('logout').hidden = true;
  $('login').hidden = false;
  $('secret').value = '';
  $('issued').hidden = true;
}

function edit(form, entry) {
  for (const input of form.elements) {
    if (!input.name || !Object.hasOwn(entry, input.name)) continue;
    if (input.type === 'checkbox') input.checked = entry[input.name];
    else input.value = entry[input.name];
  }
  form.elements.name.focus();
}

function localDateTime(date) {
  return new Date(date.valueOf() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}

function syncToggles() {
  for (const control of keyForm.querySelectorAll('[data-reveal]')) {
    const area = $(control.dataset.reveal);
    area.hidden = !control.checked;
    for (const input of area.querySelectorAll('input,textarea')) input.disabled = area.hidden;
  }
  keyForm.elements.customKey.required = keyForm.elements.customOn.checked;
  keyForm.elements.ips.required = keyForm.elements.ipOn.checked;
  keyForm.elements.expiresAt.required = keyForm.elements.expiryOn.checked;
}

function editKey(key) {
  const p = key.policy;
  edit(keyForm, { ...key, customOn: false, customKey: '', ipOn: Boolean(p.ips?.length), ips: p.ips?.join('\n') || '',
    totalUsd: p.totalMicroUSD / 1e6, rateOn: [p.fiveHourMicroUSD, p.dayMicroUSD, p.weekMicroUSD].some(Boolean),
    fiveHourUsd: p.fiveHourMicroUSD / 1e6, dayUsd: p.dayMicroUSD / 1e6, weekUsd: p.weekMicroUSD / 1e6,
    expiryOn: Boolean(p.expiresAt), expiresAt: p.expiresAt ? localDateTime(new Date(p.expiresAt)) : '' });
  keyForm.elements.customOn.disabled = true;
  $('save-key').textContent = '保存修改';
  syncToggles();
}

function button(text, handler) {
  const element = document.createElement('button');
  element.type = 'button';
  element.textContent = text;
  element.onclick = async () => {
    element.disabled = true;
    try { await handler(); }
    catch (error) { message(error.message); }
    finally { element.disabled = false; }
  };
  return element;
}

function row(title, summary, ...actions) {
  const li = document.createElement('li');
  const text = document.createElement('span');
  const detail = document.createElement('small');
  text.textContent = title;
  detail.textContent = summary;
  text.append(detail);
  li.append(text, ...actions);
  return li;
}

async function refresh() {
  const { upstreams, keys, usage } = await api('state');
  $('login').hidden = true;
  $('settings').hidden = $('logout').hidden = false;
  $('upstreams').replaceChildren(...upstreams.map(upstream => row(upstream.name, [upstream.baseUrl, upstream.pool && `池 ${upstream.pool}`, upstream.enabled ? '启用' : '停用'].filter(Boolean).join(' · '),
    ...(upstream.id === 'openai-env' ? [] : [button('编辑', () => edit(upstreamForm, { ...upstream, apiKey: '' }))]),
    button('测试', async () => {
      const data = await api(`upstreams/${encodeURIComponent(upstream.id)}/test`, {});
      message(`${upstream.name} · HTTP ${data.upstreamStatus}`);
    })
  )));
  const select = keyForm.elements.upstreamId;
  const selected = select.value;
  select.replaceChildren(...upstreams.map(upstream => new Option(upstream.pool ? `${upstream.pool}（${upstream.name}）` : upstream.name, upstream.id)));
  if (upstreams.some(upstream => upstream.id === selected)) select.value = selected;
  $('keys').replaceChildren(...keys.map(key => {
    const windows = usage[key.id].windows;
    const total = usage[key.id].usage.total;
    const limit = key => `${windows[key].used}/${windows[key].limit || '∞'}`;
    const tokens = total.meteredRequests ? `${total.total_tokens.toLocaleString()} tokens` : '暂无 token 回执';
    const cost = total.pricedRequests || total.reservedMicroUSD
      ? ` · 已结算 $${(total.settledMicroUSD / 1e6).toFixed(6)} · 待结算 $${(total.reservedMicroUSD / 1e6).toFixed(6)}` : '';
    const expiry = key.policy.expiresAt ? ` · ${new Date(key.policy.expiresAt).toLocaleString()} 到期` : '';
    return row(key.name, `${key.prefix}… · ${key.enabled ? '启用' : '停用'} · 5h ${limit('5h')} · 7d ${limit('7d')} · ${tokens}${cost}${expiry}`,
      button('编辑', () => editKey(key)),
      button(key.enabled ? '停用' : '启用', async () => {
        await api('keys', { ...key, enabled: !key.enabled });
        await refresh();
      })
    );
  }));
}

function onSubmit(form, handler) {
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const submit = event.submitter;
    if (submit) submit.disabled = true;
    try { await handler(Object.fromEntries(new FormData(form))); }
    catch (error) { message(error.message); }
    finally { if (submit) submit.disabled = false; }
  });
}

onSubmit($('login'), async data => {
  await api('login', data);
  $('login').reset();
  message();
  await refresh();
});
onSubmit(upstreamForm, async data => {
  await api('upstreams', { ...data, enabled: upstreamForm.elements.enabled.checked });
  upstreamForm.reset();
  message('已保存上游');
  await refresh();
});
onSubmit(keyForm, async data => {
  const micro = amount => Math.round(Number(amount || 0) * 1e6);
  const result = await api('keys', {
    id: data.id, name: data.name, upstreamId: data.upstreamId, customKey: data.customKey || '',
    enabled: keyForm.elements.enabled.checked, limit5h: Number(data.limit5h), limit7d: Number(data.limit7d),
    policy: {
      ips: String(data.ips || '').split(/[\s,]+/).filter(Boolean), expiresAt: data.expiresAt ? new Date(data.expiresAt).toISOString() : null,
      totalMicroUSD: micro(data.totalUsd), fiveHourMicroUSD: micro(data.fiveHourUsd), dayMicroUSD: micro(data.dayUsd), weekMicroUSD: micro(data.weekUsd)
    }
  });
  keyForm.reset();
  if (result.secret) {
    $('secret').value = result.secret;
    $('issued').hidden = false;
  }
  message(result.secret ? '已签发密钥' : '已保存修改');
  await refresh();
});
keyForm.addEventListener('reset', () => requestAnimationFrame(() => {
  keyForm.elements.id.value = '';
  keyForm.elements.customOn.disabled = false;
  $('save-key').textContent = '签发密钥';
  syncToggles();
}));
upstreamForm.addEventListener('reset', () => requestAnimationFrame(() => { upstreamForm.elements.id.value = ''; }));
for (const control of keyForm.querySelectorAll('[data-reveal]')) control.onchange = syncToggles;
$('expiry-presets').onclick = event => {
  if (!event.target.matches('button[data-days]')) return;
  const days = Number(event.target.dataset.days);
  const date = new Date();
  date.setDate(date.getDate() + days);
  if (days) keyForm.elements.expiresAt.value = localDateTime(date);
  keyForm.elements.expiresAt.focus();
};
syncToggles();
$('copy').onclick = async () => {
  try { await navigator.clipboard.writeText($('secret').value); message('已复制密钥'); }
  catch { $('secret').select(); }
};
$('logout').onclick = async () => {
  try { await api('logout', {}); showLogin(); message(); }
  catch (error) { message(error.message); }
};
refresh().catch(error => { if ($('login').hidden) message(error.message); });
