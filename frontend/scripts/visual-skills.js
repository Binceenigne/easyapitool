const imageVisualSkills = (() => {
    const catalog = new Map(IMAGE_VISUAL_SKILLS.map(skill => [skill.id, skill]));
    const selected = new Map();
    const assets = 'assets/visual-skills/';
    let editor = null;
    let opener = null;
    let root;
    let grid;

    function stepsFor(skill, choices) {
        return skill.steps.filter(step => !step.when || Object.entries(step.when)
            .every(([id, allowed]) => allowed.includes(choices[id])));
    }

    function choicesFor(step, chosen) {
        return step.exclude ? step.choices.filter(choice => choice.id !== chosen[step.exclude]) : step.choices;
    }

    function choiceList(skill, chosen) {
        return stepsFor(skill, chosen).map(step => choicesFor(step, chosen)
            .find(choice => choice.id === chosen[step.id])).filter(Boolean);
    }

    function agentInstructions(skill, chosen) {
        return [skill.agent, ...choiceList(skill, chosen).map(choice => choice.agent)].filter(Boolean);
    }

    function requiresAgent() {
        return [...selected].some(([id, choices]) => agentInstructions(catalog.get(id), choices).length > 0);
    }

    function hasAgentMode() {
        return imageEditState.reasoningAdvanced && imageAdvancedAllowed()
            || imageReasoningModesForTier().some(mode => mode !== 'instant');
    }

    function reasoningIssue() {
        if (!requiresAgent()) return '';
        if (!hasAgentMode()) return '当前档位没有可用的思维模式，请移除 Agent 风格或切换密钥';
        return !imageEditState.reasoningAdvanced && imageEditState.reasoningMode === 'instant'
            ? 'Agent 风格需要 Flash 或其他已开放的思维模式' : '';
    }

    function compose(prompt) {
        if (!selected.size) return prompt;
        const recipes = [...selected].map(([id, chosen]) => {
            const skill = catalog.get(id);
            const options = choiceList(skill, chosen).filter(choice => choice.prompt);
            const agent = agentInstructions(skill, chosen);
            return [`${skill.name}`, skill.prompt, ...options.map(choice => `${choice.name}：${choice.prompt}`),
                ...agent.map(instruction => `Agent 规划：${instruction}`)].join('\n');
        });
        return `${prompt}\n\n【已选视觉风格】\n按用户的主题、参考图和所选输出规格使用下列配方。按现有素材理解模板变量，组合多个风格时融合相容特征。\n\n${recipes.join('\n\n')}`;
    }

    function thumbnail(skill, choice, step) {
        const thumb = document.createElement('span');
        thumb.className = 'visual-skill-thumb';
        thumb.setAttribute('aria-hidden', 'true');
        const cover = choice?.cover || skill.cover;
        if (choice?.colors?.length) {
            const colors = choice.colors;
            thumb.classList.add('is-swatch');
            for (const color of colors) {
                const band = document.createElement('span');
                band.style.backgroundColor = color;
                thumb.append(band);
            }
        } else if (step?.layout && choice.id !== 'default') {
            const variant = Number(choice.id) - 1;
            thumb.classList.add('is-diagram');
            thumb.dataset.scheme = String(variant % 9);
            thumb.style.setProperty('--diagram-hue', String((skill.cell * 41 + variant * 27) % 360));
            thumb.append(document.createElement('span'), document.createElement('span'), document.createElement('span'));
        } else if (cover) {
            const picture = document.createElement('img');
            picture.src = assets + cover;
            picture.alt = '';
            picture.draggable = false;
            thumb.append(picture);
        } else {
            thumb.classList.add('is-atlas');
            thumb.style.backgroundImage = `url('${assets}${skill.atlas}.webp')`;
            thumb.style.backgroundPosition = `${skill.cell % 3 * 50}% ${Math.floor(skill.cell / 3) * 50}%`;
            if (choice && choice.id !== 'default') thumb.style.setProperty('--variant-hue', `${(Number(choice.id) - 1) * 19}deg`);
        }
        return thumb;
    }

    function makeTile(skill, { choice, step, active = false, onSelect }) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'visual-skill-choice';
        button.dataset.skillId = skill.id;
        if (choice) button.dataset.choiceId = choice.id;
        button.toggleAttribute('data-selected', active);
        button.setAttribute('aria-pressed', String(active));
        const needsAgent = Boolean(skill.agent || choice?.agent);
        const containsAgentChoice = !choice && !needsAgent && skill.steps.some(item => item.choices.some(option => option.agent));
        const name = document.createElement('span');
        name.className = 'visual-skill-choice-name';
        name.textContent = choice?.name || skill.name;
        button.title = choice ? choice.name : skill.title;
        if (needsAgent && !hasAgentMode()) {
            button.disabled = true;
            button.title += ' · 当前档位未开放此风格所需的思维模式';
        }
        button.append(thumbnail(skill, choice, step), name);
        if (needsAgent || containsAgentChoice) {
            const badge = document.createElement('span');
            badge.className = 'visual-skill-agent-badge';
            badge.textContent = containsAgentChoice ? '含 Agent' : 'Agent';
            button.append(badge);
        }
        button.addEventListener('click', onSelect);
        return button;
    }

    function activeStep() {
        return editor && editor.steps[editor.index];
    }

    function render(direction = '') {
        const step = activeStep();
        root.dataset.view = editor ? 'steps' : 'library';
        document.getElementById('imageSkillTitle').textContent = editor?.skill.name || '视觉风格';
        document.getElementById('imageSkillBack').hidden = !editor;
        document.getElementById('imageSkillSteps').hidden = !step;
        const indicator = document.getElementById('imageSkillStepLabel');
        indicator.textContent = step ? `${step.name} · ${editor.index + 1}/${editor.steps.length}` : '';
        const next = document.getElementById('imageSkillNext');
        next.disabled = !step || !editor.chosen[step.id];
        next.title = step && editor.index === editor.steps.length - 1 ? '应用风格' : '下一步';
        next.setAttribute('aria-label', next.title);
        const previous = document.getElementById('imageSkillPrevious');
        previous.disabled = !step || editor.index === 0;
        if (step) {
            grid.replaceChildren(...choicesFor(step, editor.chosen).map(choice => makeTile(editor.skill, {
                choice, step, active: choice.id === editor.chosen[step.id], onSelect: () => selectChoice(choice)
            })));
        } else {
            grid.replaceChildren(...IMAGE_VISUAL_SKILLS.map(skill => makeTile(skill, {
                active: selected.has(skill.id), onSelect: () => chooseSkill(skill)
            })));
        }
        grid.scrollTop = 0;
        grid.classList.remove('from-left', 'from-right');
        if (direction && !window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
            void grid.offsetWidth;
            grid.classList.add(`from-${direction}`);
        }
        renderLucideIcons();
        (grid.querySelector('.visual-skill-choice[data-selected]') || grid.firstElementChild)?.focus({ preventScroll: true });
    }

    function begin(skill) {
        const chosen = { ...selected.get(skill.id) };
        editor = { skill, chosen, steps: stepsFor(skill, chosen), index: 0 };
        render('right');
    }

    function chooseSkill(skill) {
        if (skill.agent && !hasAgentMode()) return showToast('当前密钥档位未开放此风格所需的思维模式', 'info');
        if (!selected.has(skill.id) && (skill.quick || !skill.steps.length)) {
            const chosen = Object.fromEntries(skill.steps.map(step => [step.id, step.choices[0].id]));
            activate(skill, chosen);
        } else {
            begin(skill);
        }
    }

    function selectChoice(choice) {
        const step = activeStep();
        if (!step) return;
        if (choice.agent && !hasAgentMode()) return showToast('这项配方需要已开放的思维模式', 'info');
        editor.chosen[step.id] = choice.id;
        editor.steps = stepsFor(editor.skill, editor.chosen);
        for (const item of editor.steps) {
            if (!choicesFor(item, editor.chosen).some(value => value.id === editor.chosen[item.id])) delete editor.chosen[item.id];
        }
        nextStep();
    }

    function nextStep() {
        if (!activeStep() || !editor.chosen[activeStep().id]) return;
        if (editor.index === editor.steps.length - 1) return activate(editor.skill, editor.chosen);
        editor.index += 1;
        render('right');
    }

    function activate(skill, chosen) {
        if (agentInstructions(skill, chosen).length && !hasAgentMode()) {
            showToast('当前密钥档位未开放此风格所需的思维模式', 'info');
            return;
        }
        const editing = selected.has(skill.id);
        selected.set(skill.id, { ...chosen });
        close(false);
        updateSelection(!editing);
        const target = editing ? document.querySelector(`.visual-skill-reopen[data-skill-id="${skill.id}"]`)
            : document.getElementById('imageSkillButton');
        target?.focus({ preventScroll: true });
    }

    function updateSelection(scrollToEnd = false) {
        if (requiresAgent() && imageEditState.reasoningMode === 'instant' && !imageEditState.reasoningAdvanced) {
            setImageReasoningMode('flash');
        }
        syncImageReasoningLabels();
        renderEditImageList(scrollToEnd);
        syncImageGenerationAvailability();
    }

    function renderCards(list) {
        for (const [id, chosen] of selected) {
            const skill = catalog.get(id);
            const card = document.createElement('div');
            card.className = 'image-edit-file visual-skill-package';
            const agent = agentInstructions(skill, chosen).length > 0;
            card.toggleAttribute('data-agent', agent);
            const edit = document.createElement('button');
            edit.type = 'button';
            edit.className = 'visual-skill-reopen';
            edit.dataset.skillId = id;
            edit.title = [skill.name, ...choiceList(skill, chosen).map(choice => choice.name), agent ? '需要 Agent' : ''].filter(Boolean).join(' · ');
            edit.setAttribute('aria-label', `编辑风格 ${edit.title}`);
            edit.setAttribute('aria-haspopup', 'dialog');
            const label = document.createElement('span');
            label.className = 'visual-skill-package-name';
            label.textContent = skill.name;
            edit.append(label, thumbnail(skill));
            if (agent) {
                const badge = document.createElement('span');
                badge.className = 'visual-skill-agent-badge';
                badge.textContent = 'Agent';
                edit.append(badge);
            }
            edit.addEventListener('click', () => open(edit, skill));
            const remove = createEditImageRemoveButton(skill.name, () => {
                selected.delete(id);
                updateSelection();
                document.getElementById('imageSkillButton')?.focus({ preventScroll: true });
            });
            card.append(edit, remove);
            list.append(card);
        }
    }

    function clearReferences() {
        selected.clear();
        imageEditState.files = [];
        close(false);
        updateSelection();
        if (!imageEditState.activeRequests.size) {
            document.getElementById('imageEditStatus').textContent = '填写提示词即可生成，参考图可选';
        }
        document.getElementById('editImageSelection').focus({ preventScroll: true });
    }

    function clearPrompt() {
        if (imageEditState.polishing) return;
        const textarea = document.getElementById('imageEditPrompt');
        textarea.value = '';
        document.getElementById('imagePromptModalTextarea').value = '';
        resizeImagePrompt();
        textarea.focus({ preventScroll: true });
    }

    function position() {
        if (!root || root.hidden) return;
        const shell = document.querySelector('.image-edit-prompt-shell').getBoundingClientRect();
        const trigger = document.getElementById('imageSkillButton').getBoundingClientRect();
        const bounds = document.getElementById('appMain').getBoundingClientRect();
        const left = Math.max(8, shell.left + 1);
        const width = Math.min(shell.width - 2, window.innerWidth - left - 8);
        const bottom = Math.max(8, window.innerHeight - trigger.top + 8);
        const height = Math.max(0, trigger.top - Math.max(8, bounds.top + 8) - 8);
        root.style.left = `${left}px`;
        root.style.width = `${width}px`;
        root.style.bottom = `${bottom}px`;
        root.style.height = `${Math.min(height, 420)}px`;
    }

    function open(trigger, skill = null) {
        if (!root.hidden && opener === trigger) return close();
        closeImageGenerationControls();
        closeImageReasoningMenu();
        if (opener) opener.setAttribute('aria-expanded', 'false');
        opener = trigger;
        editor = null;
        root.hidden = false;
        opener.setAttribute('aria-expanded', 'true');
        position();
        if (skill) begin(skill);
        else render();
    }

    function close(restoreFocus = true) {
        if (!root || root.hidden) return;
        root.hidden = true;
        if (opener) {
            opener.setAttribute('aria-expanded', 'false');
            if (restoreFocus && opener.isConnected) opener.focus({ preventScroll: true });
        }
        opener = null;
        editor = null;
    }

    function init() {
        root = document.getElementById('imageSkillPicker');
        document.body.append(root);
        grid = document.getElementById('imageSkillGrid');
        const trigger = document.getElementById('imageSkillButton');
        trigger.addEventListener('click', () => open(trigger));
        document.getElementById('clearImagePromptButton').addEventListener('click', clearPrompt);
        document.getElementById('clearImageReferencesButton').addEventListener('click', clearReferences);
        document.getElementById('imageSkillClose').addEventListener('click', () => close());
        document.getElementById('imageSkillBack').addEventListener('click', () => { editor = null; render('left'); });
        document.getElementById('imageSkillNext').addEventListener('click', nextStep);
        document.getElementById('imageSkillPrevious').addEventListener('click', () => {
            if (editor?.index > 0) { editor.index -= 1; render('left'); }
        });
        document.addEventListener('pointerdown', event => {
            if (!root.hidden && !root.contains(event.target) && !opener?.contains(event.target)) close(false);
        });
        window.addEventListener('resize', position, { passive: true });
        window.visualViewport?.addEventListener('resize', position, { passive: true });
        renderEditImageList();
    }

    return { init, close, renderCards, count: () => selected.size, requiresAgent, reasoningIssue, compose };
})();
