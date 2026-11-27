import { createIcons } from './icons.min.js';

const style = document.createElement('link');
style.rel = 'stylesheet';
style.href = new URL('./tiers-v1.css?v=semantic-colors-1', import.meta.url).href;
const styleReady = new Promise(resolve => {
  style.addEventListener('load', () => resolve(true), { once: true });
  style.addEventListener('error', () => resolve(false), { once: true });
});
document.head.append(style);
const labels = {
  formats: { png: 'PNG', jpeg: 'JPEG', webp: 'WebP', svg: 'SVG', gif: 'GIF' },
  qualities: { low: '低', medium: '中', high: '高', xhigh: '超高', max: '最高' },
  complexities: { low: '低', medium: '中', high: '高' },
  modes: { instant: '直接生图', flash: 'Flash', medium: 'Medium', high: 'High', extra: 'Extra', max: 'Max' },
  efforts: { none: '不启用', minimal: '最少', low: '低', medium: '中', high: '高', xhigh: '超高', max: '最高' },
};
const money = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 0, maximumFractionDigits: 6 });
const node = (tag, className, text) => {
  const item = document.createElement(tag);
  if (className) item.className = className;
  if (text !== undefined) item.textContent = text;
  return item;
};
const glyph = name => { const icon = node('i'); icon.dataset.lucide = name; icon.setAttribute('aria-hidden', 'true'); return icon; };
const fee = value => Number.isSafeInteger(value) && value > 0 ? money.format(value / 1_000_000) : '不限';
const permitted = (values, value) => Array.isArray(values) && (values.includes('*') || values.includes(value));
const choices = (values, map, allLabel = '全部已开放选项') => !Array.isArray(values) || !values.length ? '未开放'
  : values.includes('*') ? allLabel : values.map(value => map?.[value] || value).join('、');
const validity = value => value > 0 ? `签发后 ${value} 天` : '长期有效';
const quota = tier => tier.defaultQuota || {};
const resolution = tier => `${{1536:'标清',2560:'高清',3840:'超清'}[tier.maxResolution] || ''} ${tier.maxResolution} px`.trim();

const mainRows = [
  ['总创作额度', 'aperture', t => fee(quota(t).totalMicroUSD), 'amount'],
  ['5 小时额度', '', t => fee(quota(t).fiveHourMicroUSD)],
  ['每日额度', '', t => fee(quota(t).dayMicroUSD)],
  ['7 天额度', '', t => fee(quota(t).weekMicroUSD)],
  ['有效期', '', t => validity(quota(t).expiresInDays)],
  ['RPM（语言 + 图像）', '', t => t.rpmLimit > 0 ? `${Number(t.rpmLimit).toLocaleString('zh-CN')} 次 / 分钟` : '不限'],
  ['最高分辨率', 'monitor', resolution, 'resolution'],
  ['每次生成', 'copy', t => `最多 ${t.maxImages} 张`],
  ['输出格式', 'play', t => choices(t.formats, labels.formats, 'PNG · JPEG · WebP · SVG · GIF'), 'formats'],
  ['高级思维', 'sparkles', t => t.advanced, 'reasoning'],
  ['透明背景', 'aperture', t => t.transparency, 'transparency'],
  ['SVG 矢量图', 'aperture', t => permitted(t.formats, 'svg'), 'vector'],
  ['GIF 动图', 'play', t => permitted(t.formats, 'gif'), 'animation'],
];
const extraRows = [
  ['生图模型', 'aperture', t => choices(t.imageModels, null, '全部已开放生图模型')],
  ['思维模型', 'sparkles', t => choices(t.reasoningModels, null, '全部已开放思维模型')],
  ['思维深度预设', '', t => choices(t.reasoningModes, labels.modes)],
  ['模型思考深度', '', t => choices(t.reasoningEfforts, labels.efforts)],
  ['图片质量', '', t => choices(t.qualities, labels.qualities)],
  ['构图复杂度', '', t => choices(t.complexities, labels.complexities)],
  ['参考图编辑', 'scan-eye', t => t.imageEditing ? `最多 ${t.maxReferences} 张参考图` : false, 'editing'],
  ['GIF 最大帧数', 'play', t => permitted(t.formats, 'gif') ? `${t.maxGifFrames} 帧` : false, 'animation'],
  ['联网搜索', 'scan-eye', t => t.webSearch, 'search'],
  ['提示词润色', 'sparkles', t => t.promptPolish, 'reasoning'],
  ['视觉风格', 'aperture', t => t.visualSkills !== false],
];

function capabilityBadge(text, tone, icon) {
  const badge = node('span', 'mirra-tier-table__badge');
  badge.dataset.tone = tone;
  if (icon) badge.append(glyph(icon));
  badge.append(node('span', '', text));
  return badge;
}

function appendCellValue(cell, value, kind, tier) {
  if (kind === 'resolution') {
    const label = {1536:'标清',2560:'高清',3840:'超清'}[tier.maxResolution];
    const tone = {1536:'neutral',2560:'blue',3840:'purple'}[tier.maxResolution] || 'neutral';
    const stack = node('span', 'mirra-tier-table__cell-stack');
    stack.append(capabilityBadge(label || value, tone, 'monitor'), node('small', 'mirra-tier-table__detail', `${tier.maxResolution} px`));
    cell.append(stack);
    return;
  }
  if (kind === 'formats') {
    const formats = tier.formats?.includes('*') ? Object.keys(labels.formats) : tier.formats || [];
    const group = node('span', 'mirra-tier-table__format-badges');
    for (const format of formats) {
      const [tone, icon] = {svg:['purple','aperture'],gif:['blue','play'],webp:['teal','scan-eye']}[format] || ['neutral', null];
      group.append(capabilityBadge(labels.formats[format] || format, tone, icon));
    }
    cell.append(formats.length ? group : node('span', 'mirra-tier-table__status is-unavailable', '未开放'));
    return;
  }
  const special = {reasoning:['purple','sparkles'],transparency:['teal','aperture'],vector:['purple','aperture'],animation:['blue','play'],editing:['blue','scan-eye'],search:['blue','scan-eye']}[kind];
  if (value !== false && special) {
    cell.append(capabilityBadge(value === true ? '支持' : value, ...special));
    return;
  }
  if (typeof value === 'boolean') cell.append(node('span', `mirra-tier-table__status ${value ? 'is-allowed' : 'is-unavailable'}`, value ? '支持' : '未开放'));
  else cell.append(node('span', kind === 'amount' ? 'mirra-tier-table__amount' : '', value));
}

function appendRows(body, definitions, plans) {
  for (const [label, icon, get, kind] of definitions) {
    const row = body.insertRow();
    const name = node('th'); name.scope = 'row';
    const words = node('span', 'mirra-tier-table__label');
    if (icon) words.append(glyph(icon));
    words.append(node('span', '', label)); name.append(words); row.append(name);
    plans.forEach(tier => {
      const cell = row.insertCell(), value = get(tier);
      appendCellValue(cell, value, kind, tier);
    });
    if (kind === 'amount') row.className = 'mirra-tier-table__quota';
  }
}

let signature = '', mounted = null, resizeObserver = null, busy = false;
function unmount() { resizeObserver?.disconnect(); resizeObserver = null; mounted?.remove(); mounted = null; signature = ''; }
function render(payload) {
  if (!payload?.enabled || !Array.isArray(payload.tiers) || !payload.tiers.length) { unmount(); return; }
  const plans = payload.tiers.filter(t => typeof t.id === 'string' && typeof t.name === 'string' && t.name.trim());
  if (!plans.length) { unmount(); return; }
  const nextSignature = JSON.stringify([payload.revision, payload.publishedAt, plans]);
  if (signature === nextSignature && mounted) return;
  const anchor = document.getElementById('download'); if (!anchor) return;
  const section = node('section', 'mirra-plans'); section.id = 'creative-tiers'; section.setAttribute('aria-labelledby', 'creative-tiers-title');
  const intro = node('div', 'mirra-plans__intro'), label = node('p', 'mirra-plans__label'); label.append(glyph('sparkles'), '创作档位对比');
  const title = node('h2', '', '给灵感，恰好的空间。'); title.id = 'creative-tiers-title';
  intro.append(label, title, node('p', 'mirra-plans__description', '横向对比各档位的默认额度、模型与生成功能。'));
  const tools = node('div', 'mirra-plans__tools'); tools.append(node('p', '', `${plans.length} 个档位 · 横向对比`));
  const controls = node('div', 'mirra-plans__controls'), rail = node('div', 'mirra-plans__rail');
  rail.tabIndex = 0; rail.setAttribute('role', 'region'); rail.setAttribute('aria-label', '档位横向对比表，可横向滚动，左侧项目名称固定');
  const table = node('table', 'mirra-tier-table'); table.style.setProperty('--plan-count', plans.length);
  const caption = node('caption', 'mirra-tier-table__caption', '各创作档位的默认额度与功能权限对比');
  const columns = node('colgroup'), first = node('col', 'mirra-tier-table__first-col'); columns.append(first, ...plans.map(() => node('col')));
  table.append(caption, columns);
  const head = table.createTHead().insertRow(), corner = node('th', 'mirra-tier-table__corner', '功能与额度'); corner.scope = 'col'; head.append(corner);
  plans.forEach(tier => {
    const cell = node('th'); cell.scope = 'col';
    const description = node('p', '', tier.description || '按创作所需，选择适合的能力与额度。'); description.title = tier.description || '';
    cell.append(node('h3', '', tier.name), description); head.append(cell);
  });
  appendRows(table.createTBody(), mainRows, plans);
  const extra = table.createTBody(); extra.id = 'creative-tier-extra-rows'; extra.hidden = true; appendRows(extra, extraRows, plans);
  rail.append(table);
  for (const direction of [-1, 1]) {
    const button = node('button', 'mirra-plans__arrow'); button.type = 'button'; button.dataset.direction = direction;
    button.setAttribute('aria-label', direction === -1 ? '查看前面的档位' : '查看后面的档位'); button.append(glyph('arrow-up-right'));
    button.addEventListener('click', () => rail.scrollBy({left: direction * head.cells[1].getBoundingClientRect().width,
      behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth'}));
    controls.append(button);
  }
  const updateControls = () => { const max = rail.scrollWidth - rail.clientWidth; controls.hidden = max < 2;
    controls.children[0].disabled = rail.scrollLeft <= 2; controls.children[1].disabled = rail.scrollLeft >= max - 2; };
  rail.addEventListener('scroll', updateControls, {passive:true});
  rail.addEventListener('keydown', event => { if (event.target === rail && ['ArrowLeft','ArrowRight'].includes(event.key)) {
    event.preventDefault(); controls.children[event.key === 'ArrowLeft' ? 0 : 1].click(); } });
  tools.append(controls);
  const expand = node('button', 'mirra-plans__expand'); expand.type = 'button'; expand.setAttribute('aria-controls', extra.id); expand.setAttribute('aria-expanded', 'false');
  const expandLabel = node('span', '', '展开完整能力对比'); expand.append(expandLabel, glyph('plus'));
  expand.addEventListener('click', () => { extra.hidden = !extra.hidden; expand.setAttribute('aria-expanded', String(!extra.hidden));
    expandLabel.textContent = extra.hidden ? '展开完整能力对比' : '收起完整能力对比'; });
  const note = node('p', 'mirra-plans__note', '额度按实际调用费用计算，签发密钥时可单独调整。RPM 按每把密钥计算，语言与图像调用共享每分钟配额；实际权限与期限以所持密钥为准。');
  section.append(intro, tools, rail, expand, note);
  resizeObserver?.disconnect(); if (mounted) mounted.replaceWith(section); else anchor.before(section);
  mounted = section; signature = nextSignature; createIcons(); requestAnimationFrame(updateControls);
  if ('ResizeObserver' in window) { resizeObserver = new ResizeObserver(updateControls); resizeObserver.observe(rail); }
}

async function refresh() {
  if (busy || document.hidden) return; busy = true;
  const controller = new AbortController(), timeout = setTimeout(() => controller.abort(), 12000);
  try { const response = await fetch('/v1/app/tiers', {cache:'no-store',credentials:'omit',signal:controller.signal});
    if (response.ok) { const payload = await response.json(); if (await styleReady) render(payload); }
  } catch { /* This optional section must never interrupt the app downloads. */ }
  finally { clearTimeout(timeout); busy = false; }
}
refresh(); setInterval(refresh, 60000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
