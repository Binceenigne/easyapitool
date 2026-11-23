import { createIcons } from './icons.min.js';

// Optional, explicitly published gateway snapshot. No account or key data is
// requested by this module, and no section is mounted while display is off.
const endpoint = '/v1/app/tiers';
const style = document.createElement('link');
style.rel = 'stylesheet';
style.href = new URL('./tiers-v1.css', import.meta.url).href;
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
const glyph = name => {
  const item = node('i');
  item.dataset.lucide = name;
  item.setAttribute('aria-hidden', 'true');
  return item;
};
const fee = value => Number.isSafeInteger(value) && value > 0 ? money.format(value / 1_000_000) : '不限';
const permitted = (values, value) => Array.isArray(values) && (values.includes('*') || values.includes(value));
const choices = (values, map, allLabel = '全部已开放选项') => {
  if (!Array.isArray(values) || !values.length) return '未开放';
  if (values.includes('*')) return allLabel;
  return values.map(value => map?.[value] || value).join('、');
};
const validity = value => value > 0 ? `签发后 ${value} 天` : '长期有效';

function quotaRow(label, value) {
  const row = node('div');
  row.append(node('dt', '', label), node('dd', '', value));
  return row;
}

function feature(icon, label, value, enabled = true) {
  const row = node('li', 'mirra-plan__feature');
  if (!enabled) row.classList.add('mirra-plan__feature--off');
  const words = node('span');
  words.append(node('span', 'mirra-plan__feature-label', label), node('strong', '', value));
  row.append(glyph(icon), words);
  return row;
}

function planCard(tier, index) {
  const card = node('article', 'mirra-plan');
  const heading = node('header', 'mirra-plan__heading');
  const top = node('div', 'mirra-plan__topline');
  top.append(node('span', '', String(index + 1).padStart(2, '0')), glyph('aperture'));
  heading.append(top, node('h3', '', tier.name), node('p', '', tier.description || '按创作所需，选择适合的能力与额度。'));
  const defaults = tier.defaultQuota || {};
  const meter = node('div', 'mirra-plan__allowance');
  meter.append(node('span', '', '总创作额度'), node('strong', '', fee(defaults.totalMicroUSD)), node('small', '', '以实际调用费用计量 · USD'));
  const quota = node('dl', 'mirra-plan__quota');
  quota.append(quotaRow('5 小时额度', fee(defaults.fiveHourMicroUSD)), quotaRow('每日额度', fee(defaults.dayMicroUSD)),
    quotaRow('7 天额度', fee(defaults.weekMicroUSD)), quotaRow('有效期', validity(defaults.expiresInDays)));
  const features = node('ul', 'mirra-plan__features');
  const resolution = { 1536: '标清', 2560: '高清', 3840: '超清' }[tier.maxResolution] || '';
  features.append(
    feature('scan-eye', '最高分辨率', `${resolution} ${tier.maxResolution} px`.trim()),
    feature('copy', '每次生成', `最多 ${tier.maxImages} 张`),
    feature('sparkles', '思维调控', tier.advanced ? '支持高级模式' : '使用已开放预设'),
    feature('play', '输出格式', choices(tier.formats, labels.formats, 'PNG · JPEG · WebP · SVG · GIF')),
    feature('aperture', '透明背景', tier.transparency ? '支持' : '未开放', tier.transparency),
    feature('scan-eye', '参考图编辑', tier.imageEditing ? `最多 ${tier.maxReferences} 张参考图` : '未开放', tier.imageEditing),
  );
  const details = node('details', 'mirra-plan__details');
  const summary = node('summary', '', '查看全部能力');
  summary.append(glyph('plus'));
  const definitions = node('dl', 'mirra-plan__specs');
  definitions.append(
    quotaRow('生图模型', choices(tier.imageModels, null, '全部已开放生图模型')),
    quotaRow('思维模型', choices(tier.reasoningModels, null, '全部已开放思维模型')),
    quotaRow('思维深度预设', choices(tier.reasoningModes, labels.modes)),
    quotaRow('模型思考深度', choices(tier.reasoningEfforts, labels.efforts)),
    quotaRow('图片质量', choices(tier.qualities, labels.qualities)),
    quotaRow('构图复杂度', choices(tier.complexities, labels.complexities)),
    quotaRow('GIF 最大帧数', permitted(tier.formats, 'gif') ? `${tier.maxGifFrames} 帧` : '未开放'),
    quotaRow('联网搜索', tier.webSearch ? '支持' : '未开放'),
    quotaRow('提示词润色', tier.promptPolish ? '支持' : '未开放'),
  );
  details.append(summary, definitions);
  card.append(heading, meter, quota, features, details);
  return card;
}

let signature = '';
let mounted = null;
let busy = false;
let resizeObserver = null;

function unmount() {
  resizeObserver?.disconnect();
  resizeObserver = null;
  mounted?.remove();
  mounted = null;
  signature = '';
}

function render(payload) {
  if (!payload?.enabled || !Array.isArray(payload.tiers) || !payload.tiers.length) { unmount(); return; }
  const plans = payload.tiers.filter(tier => typeof tier.name === 'string' && tier.name.trim() && typeof tier.id === 'string');
  if (!plans.length) { unmount(); return; }
  const nextSignature = JSON.stringify([payload.revision, payload.publishedAt, plans]);
  if (signature === nextSignature && mounted) return;
  const anchor = document.getElementById('download');
  if (!anchor) return;
  const section = node('section', 'mirra-plans');
  section.id = 'creative-tiers';
  section.setAttribute('aria-labelledby', 'creative-tiers-title');
  const intro = node('div', 'mirra-plans__intro');
  const label = node('p', 'mirra-plans__label');
  label.append(glyph('sparkles'), '创作档位');
  const title = node('h2', '', '给灵感，恰好的空间。');
  title.id = 'creative-tiers-title';
  intro.append(label, title, node('p', 'mirra-plans__description', '了解可用能力与默认创作额度，选择适合自己的创作方式。'));
  const tools = node('div', 'mirra-plans__tools');
  tools.append(node('p', '', `${plans.length} 个创作档位`));
  const controls = node('div', 'mirra-plans__controls');
  const rail = node('div', 'mirra-plans__rail');
  rail.tabIndex = 0;
  rail.setAttribute('role', 'region');
  rail.setAttribute('aria-label', '创作档位对比，可横向滚动');
  rail.dataset.count = String(plans.length);
  for (const direction of [-1, 1]) {
    const button = node('button', 'mirra-plans__arrow');
    button.type = 'button';
    button.setAttribute('aria-label', direction === -1 ? '查看前面的档位' : '查看后面的档位');
    button.append(glyph('arrow-up-right'));
    button.dataset.direction = String(direction);
    button.addEventListener('click', () => rail.scrollBy({ left: direction * Math.max(280, rail.clientWidth * .75), behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth' }));
    controls.append(button);
  }
  const updateControls = () => {
    const max = rail.scrollWidth - rail.clientWidth;
    controls.hidden = max < 2;
    controls.children[0].disabled = rail.scrollLeft <= 2;
    controls.children[1].disabled = rail.scrollLeft >= max - 2;
  };
  rail.addEventListener('scroll', updateControls, { passive: true });
  rail.addEventListener('keydown', event => {
    if (event.target !== rail || !['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
    event.preventDefault();
    controls.children[event.key === 'ArrowLeft' ? 0 : 1].click();
  });
  tools.append(controls);
  rail.append(...plans.map(planCard));
  const note = node('p', 'mirra-plans__note');
  note.append(glyph('scan-eye'), '以上为档位默认值。额度按实际调用费用计算，签发密钥时可单独调整；实际权限与期限以所持密钥为准。');
  section.append(intro, tools, rail, note);
  resizeObserver?.disconnect();
  if (mounted) mounted.replaceWith(section); else anchor.before(section);
  mounted = section;
  signature = nextSignature;
  createIcons();
  requestAnimationFrame(updateControls);
  if ('ResizeObserver' in window) {
    resizeObserver = new ResizeObserver(updateControls);
    resizeObserver.observe(rail);
  }
}

async function refresh() {
  if (busy || document.hidden) return;
  busy = true;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 12000);
  try {
    const response = await fetch(endpoint, { cache: 'no-store', credentials: 'omit', signal: controller.signal });
    if (!response.ok) return;
    const payload = await response.json();
    if (await styleReady) render(payload);
  } catch {
    // This optional section must never interfere with the app downloads.
  } finally {
    clearTimeout(timeout);
    busy = false;
  }
}

refresh();
setInterval(refresh, 60000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
