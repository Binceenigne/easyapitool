import { createIcons } from './icons.min.js';

createIcons();

const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
if (!reducedMotion.matches && 'IntersectionObserver' in window) {
  document.body.classList.add('js-motion');
  const observer = new IntersectionObserver(entries => {
    for (const entry of entries) {
      if (entry.isIntersecting) {
        entry.target.classList.add('visible');
        observer.unobserve(entry.target);
      }
    }
  }, { threshold: 0.08 });
  document.querySelectorAll('.reveal').forEach(element => observer.observe(element));
}

const apiBase = location.protocol === 'file:' || ['localhost', '127.0.0.1'].includes(location.hostname)
  ? 'https://imggen.djyx.me' : '';

document.querySelectorAll('[data-download-platform]').forEach(link => {
  link.href = `${apiBase}/v1/app/update/download?platform=${link.dataset.downloadPlatform}`;
});

async function loadRelease(card) {
  const platform = card.dataset.releasePlatform;
  const field = name => card.querySelector(`[data-release-field="${name}"]`);
  const retry = card.querySelector('[data-release-retry]');
  const integrity = card.querySelector('[data-release-integrity]');
  retry.hidden = true;
  integrity.hidden = true;
  field('status').textContent = '正在获取正式版信息';
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 12000);
  try {
    const response = await fetch(
      `${apiBase}/v1/app/update?platform=${platform}&currentVersionCode=0&currentBuildTimestampMs=0`,
      { cache: 'no-store', signal: controller.signal },
    );
    if (!response.ok) throw new Error('Release unavailable');
    const release = await response.json();
    if (!release.ok || release.platform !== platform || !release.latestVersion
        || !Number.isFinite(release.downloadSize) || release.downloadSize <= 0) {
      throw new Error('Invalid release');
    }
    const version = `v${release.latestVersion}`;
    const date = new Date(release.latestBuildTime);
    field('version').textContent = version;
    field('size').textContent = `${(release.downloadSize / 1048576).toFixed(1)} MB`;
    field('date').textContent = Number.isNaN(date.getTime()) ? '暂未提供'
      : new Intl.DateTimeFormat('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' }).format(date);
    field('status').textContent = '来自官方发布渠道';
    document.querySelector(`[data-platform-version="${platform}"]`).textContent =
      `${platform === 'windows' ? 'Windows' : 'Android'} ${version}`;
    if (/^[a-f0-9]{64}$/i.test(release.sha256 || '')) {
      field('hash').textContent = release.sha256;
      integrity.hidden = false;
    }
  } catch {
    field('status').textContent = '版本信息暂不可用，仍可下载安装包。';
    retry.hidden = false;
  } finally {
    clearTimeout(timeout);
  }
}

document.querySelectorAll('[data-release-platform]').forEach(card => {
  card.querySelector('[data-release-retry]').addEventListener('click', () => loadRelease(card));
  card.querySelector('[data-release-copy]').addEventListener('click', async event => {
    const label = event.currentTarget.querySelector('span');
    try {
      await navigator.clipboard.writeText(card.querySelector('[data-release-field="hash"]').textContent);
      label.textContent = '已复制';
    } catch {
      label.textContent = '请选中上方校验值复制';
    }
  });
  loadRelease(card);
});

const stage = document.getElementById('hero-stage');
const motionControl = document.getElementById('motion-control');
let paused = reducedMotion.matches;
function updateMotion() {
  stage.classList.toggle('motion-paused', paused);
  const label = paused ? '播放动画' : '暂停动画';
  motionControl.setAttribute('aria-label', label);
  motionControl.title = label;
  motionControl.innerHTML = `<i data-lucide="${paused ? 'play' : 'pause'}" aria-hidden="true"></i>`;
  createIcons();
}
motionControl.addEventListener('click', () => { paused = !paused; updateMotion(); });
reducedMotion.addEventListener('change', event => { paused = event.matches; updateMotion(); });
updateMotion();
motionControl.hidden = false;
