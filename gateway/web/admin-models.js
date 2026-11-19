'use strict';

(() => {
  const form = $('model-form');
  const source = $('model-upstream');
  const search = $('model-search');
  const preview = $('app-preview');
  const slots = ['luna', 'terra', 'sol'];
  const modes = ['Instant', 'Flash', 'Medium', 'High', 'Extra', 'Max'];
  const featureTarget = $('model-feature-target');
  const features = {
    complexity: $('model-feature-complexity'),
    animation: $('model-feature-animation')
  };
  const selected = new Set();
  const imageFeatures = new Map();
  let found = [];
  let dirty = false;
  let revision = 0;
  let fetchRound = 0;
  let hasCatalog = false;
  const efforts = [
    ['none', '不启用 · none'],
    ['minimal', '最少 · minimal'],
    ['low', '低 · low'],
    ['medium', '中 · medium'],
    ['high', '高 · high'],
    ['xhigh', '超高 · xhigh'],
    ['max', '最大 · max']
  ];
  for (const mode of modes) {
    const preset = node('fieldset', 'model-slot');
    const inputs = node('div', 'model-slot-fields');
    const model = node('input');
    model.name = mode + 'Model';
    model.setAttribute('list', 'model-catalog');
    model.spellcheck = false;
    model.required = mode !== 'Instant';
    if (!model.required) model.placeholder = '留空直接生图';
    const depth = node('select');
    depth.name = mode + 'Effort';
    depth.dataset.effort = '';
    depth.required = true;
    const modelLabel = node('label', '', '默认模型');
    const depthLabel = node('label', '', '思考深度');
    modelLabel.append(model);
    depthLabel.append(depth);
    inputs.append(modelLabel, depthLabel);
    preset.append(node('legend', '', mode), inputs);
    $('model-reasoning-presets').append(preset);
  }
  for (const field of form.querySelectorAll('[data-effort]')) {
    const options = field.name === 'svgEffort' ? [['', '上游默认'], ...efforts] : efforts;
    field.replaceChildren(...options.map(([value, label]) => new Option(label, value)));
  }

  function draft() {
    const fields = form.elements;
    const reasoning = Object.fromEntries(slots.map(slot => [slot, {
      ...state.appModels.reasoning[slot], model: fields[slot + 'Model'].value.trim()
    }]));
    const reasoningModes = Object.fromEntries(modes.map(mode => [mode.toLowerCase(), {
      model: fields[mode + 'Model'].value.trim(), effort: fields[mode + 'Effort'].value
    }]));
    return {
      revision, defaultImage: fields.defaultImage.value, imageModels: [...selected], reasoning, reasoningModes,
      imageFeatures: Object.fromEntries([...imageFeatures].filter(([model]) => selected.has(model))),
      svg: { enabled: fields.svgEnabled.checked, model: fields.svgModel.value.trim(), effort: fields.svgEffort.value }
    };
  }

  function updatePreview() {
    if (!state || !preview.getAttribute('src')) return;
    const profile = draft();
    if (profile.defaultImage) preview.contentWindow?.postMessage({ type: 'mirra-app-models', profile }, '*');
    $('preview-state').textContent = dirty ? '未保存的草稿' : '已保存配置';
  }
  preview.addEventListener('load', updatePreview);

  function listModels() {
    return [...new Set([...selected, ...state.appModels.imageModels, ...found])];
  }

  function renderImageModels() {
    const filter = search.value.trim().toLowerCase();
    const choices = listModels().filter(model => model.toLowerCase().includes(filter));
    const list = $('model-image-list');
    list.replaceChildren(...choices.map(model => {
      const label = node('label', 'model-choice');
      const check = node('input');
      check.type = 'checkbox';
      check.value = model;
      check.checked = selected.has(model);
      check.addEventListener('change', () => {
        if (check.checked) {
          if (selected.size >= 128) {
            check.checked = false;
            formError(form, '最多可开放 128 个生图模型');
            return;
          }
          selected.add(model);
        } else {
          selected.delete(model);
        }
        updateDefaultModels();
        markDirty();
      });
      label.append(check, node('span', 'mono', model));
      return label;
    }));
    if (!choices.length) list.append(node('p', 'hint', '没有匹配的模型'));
  }

  function updateDefaultModels(preferred = form.elements.defaultImage.value) {
    const select = form.elements.defaultImage;
    select.replaceChildren(...[...selected].map(model => new Option(model, model)));
    if (selected.has(preferred)) select.value = preferred;
    select.disabled = selected.size === 0;
    const active = featureTarget.value;
    featureTarget.replaceChildren(...[...selected].map(model => new Option(model, model)));
    featureTarget.value = selected.has(active) ? active : select.value;
    featureTarget.disabled = select.disabled;
    syncFeatures();
    $('selected-images').textContent = '已开放 ' + selected.size + ' 个';
  }

  function syncFeatures() {
    const model = featureTarget.value;
    const enabledByDefault = /^gpt-image-2\.6-(?:flare|sunburst)(?:-|$)/.test(model);
    for (const [feature, field] of Object.entries(features)) {
      field.disabled = !model;
      field.checked = imageFeatures.get(model)?.[feature] ?? enabledByDefault;
    }
  }

  function updateCatalog() {
    const current = state.appModels;
    const extra = [current.svg.model, ...Object.values(current.reasoning).map(preset => preset.model),
      ...Object.values(current.reasoningModes).map(preset => preset.model)];
    $('model-catalog').replaceChildren(...[...new Set([...listModels(), ...extra])].filter(Boolean).sort().map(model => new Option(model, model)));
    renderImageModels();
  }

  function syncSVG() {
    $('model-svg-fields').hidden = !form.elements.svgEnabled.checked;
  }

  function loadForm(profile) {
    dirty = false;
    revision = profile.revision;
    selected.clear();
    profile.imageModels.forEach(model => selected.add(model));
    imageFeatures.clear();
    for (const [model, options] of Object.entries(profile.imageFeatures)) imageFeatures.set(model, options);
    const fields = form.elements;
    for (const slot of slots) {
      fields[slot + 'Model'].value = profile.reasoning[slot].model;
    }
    for (const mode of modes) {
      const preset = profile.reasoningModes[mode.toLowerCase()];
      fields[mode + 'Model'].value = preset.model;
      fields[mode + 'Effort'].value = preset.effort;
    }
    fields.svgEnabled.checked = profile.svg.enabled;
    fields.svgModel.value = profile.svg.model;
    fields.svgEffort.value = profile.svg.effort;
    updateDefaultModels(profile.defaultImage);
    updateCatalog();
    syncSVG();
    formError(form);
    updatePreview();
  }

  function markDirty() {
    dirty = true;
    syncSVG();
    formError(form);
    updatePreview();
  }

  function showSourceStatus(message = '') {
    const text = $('model-source-status');
    text.textContent = message || (source.value ? '选择来源后拉取 GET /models。' : '添加上游账户后即可拉取模型。');
  }

  window.renderModelSettings = () => {
    const previous = source.value;
    const upstreams = state.upstreams.filter(u => u.enabled);
    source.replaceChildren(...upstreams.map(u => new Option([u.pool, u.name, u.baseUrl].filter(Boolean).join(' · '), u.id)));
    if (!upstreams.length) source.append(new Option('暂无启用的上游', ''));
    if (upstreams.some(u => u.id === previous)) source.value = previous;
    if (previous !== source.value) {
      fetchRound++;
      found = [];
      hasCatalog = false;
    }
    source.disabled = !source.value;
    $('fetch-models').disabled = !source.value;
    if (!hasCatalog) showSourceStatus();
    if (!dirty) loadForm(state.appModels);
    else updateCatalog();
  };

  window.showModelPreview = () => {
    if (!state) return;
    if (!preview.getAttribute('src')) preview.src = '/app-preview/';
    else updatePreview();
  };

  window.resetModelSettings = () => {
    found = [];
    selected.clear();
    dirty = false;
    hasCatalog = false;
    fetchRound++;
    preview.removeAttribute('src');
  };

  source.addEventListener('change', () => {
    fetchRound++;
    found = [];
    hasCatalog = false;
    showSourceStatus();
    updateCatalog();
  });
  search.addEventListener('input', renderImageModels);
  featureTarget.addEventListener('change', syncFeatures);
  for (const field of Object.values(features)) {
    field.addEventListener('change', () => {
      imageFeatures.set(featureTarget.value, { complexity: features.complexity.checked, animation: features.animation.checked });
      markDirty();
    });
  }
  form.addEventListener('input', event => {
    if (event.target !== search && event.target.name) markDirty();
  });
  $('fetch-models').addEventListener('click', () => runButton($('fetch-models'), async () => {
    const currentSource = source.value;
    if (!currentSource) return;
    const round = ++fetchRound;
    showSourceStatus('正在拉取上游模型…');
    try {
      const result = await api('upstreams/' + encodeURIComponent(currentSource) + '/models');
      if (round !== fetchRound) return;
      found = result.models;
      hasCatalog = true;
      updateCatalog();
      showSourceStatus('上游返回 ' + found.length + ' 个模型。');
    } catch (error) {
      if (round === fetchRound) showSourceStatus(error.message);
    }
  }));

  $('reset-models').addEventListener('click', () => runButton($('reset-models'), async () => {
    await refresh();
    if (state) loadForm(state.appModels);
  }));

  submit('model-form', async () => {
    const profile = draft();
    if (!profile.defaultImage) throw new Error('请选择至少一个生图模型');
    state.appModels = await api('models', profile);
    loadForm(state.appModels);
    tell('模型配置已保存', 'good');
  });
  if (state) {
    window.renderModelSettings();
    if (location.hash === '#/models') window.showModelPreview();
  }
})();
