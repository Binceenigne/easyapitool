        function toggleFormPanel() {
            const panel = document.getElementById('keyFormPanel');
            panel.classList.toggle('is-hidden');
        }

        function deleteActiveKey() {
            const activeKey = getActiveKey();
            if (!activeKey) return;
            if (window.dbDeleteKey) {
                window.dbDeleteKey(activeKey.id);
            }
        }

        function clearDashboard() {
            const generationKeyLabel = document.getElementById('imageGenerationKeyLabel');
            if (generationKeyLabel) generationKeyLabel.textContent = '请先在顶部添加并选择密钥';
            const values = {
                bigRemainingVal: '—', microRemainVal: '—', bigTotalVal: '—',
                bigPercentVal: '未选择密钥', microPercentVal: '—',
                micro5hRemain: '—', micro5hPercent: '—', micro1dRemain: '—',
                micro1dPercent: '—', micro7dRemain: '—', micro7dPercent: '—',
                microCountdownValue: '—', maskedKeyText: '尚未选择', modelsCount: '—',
                todayUsageVal: '—', todayRequestsVal: '—', totalUsageVal: '—',
                expireDaysLeft: '—', speed10mVal: '—', speed1hVal: '—',
                speed10mStatus: '等待采样', speed1hStatus: '等待采样',
                avgMinVal: '—', avgHourVal: '—', averagePeriodLabel: 'UTC+8 自然日'
            };
            Object.entries(values).forEach(([id, value]) => {
                const element = document.getElementById(id);
                if (element) element.textContent = value;
            });
            ['5h', '1d', '7d'].forEach(windowName => {
                document.getElementById(`micro${windowName}Suffix`).textContent = ' USD 可用';
                document.getElementById(`micro${windowName}Row`).style.order = '0';
                document.getElementById(`micro${windowName}Row`).classList.toggle('is-tightest', windowName === '5h');
            });
            ['bigProgressBar', 'microProgressBar', 'micro5hProgress', 'micro1dProgress', 'micro7dProgress', 'win5hProgress', 'win1dProgress', 'win7dProgress'].forEach(id => {
                const bar = document.getElementById(id);
                if (bar) renderProgressBar(bar, 0, 100, { unlimited: false });
            });
            ['5h', '1d', '7d'].forEach(windowName => renderLimitWindow(windowName, null));
            const status = document.getElementById('topKeyStatus');
            status.className = 'key-status status-neutral';
            status.textContent = '未选择 NO KEY';
            setIconLabel(document.getElementById('limitModeLabel'), 'shield', '等待密钥', 'icon-10 color-brand');
            renderUsageTrend(null);
            closeModelModal();
            renderLucideIcons();
        }

        function renderLimitWindow(windowName, data) {
            const configured = data && Number(data.limit) > 0;
            const remaining = configured ? Math.max(0, Math.min(data.limit, Number(data.remaining) || 0)) : 0;
            const percent = configured ? Math.max(0, Math.min(100, remaining / data.limit * 100)) : 100;
            const displayPercent = window.appState.rateLimitProgressMode === 'used'
                ? 100 - percent
                : percent;
            const percentElement = document.getElementById(`win${windowName}Percent`);
            const remainingElement = document.getElementById(`win${windowName}Remaining`);
            const totalElement = document.getElementById(`win${windowName}Total`);
            const usedElement = document.getElementById(`win${windowName}Used`);
            const countdownElement = document.getElementById(`win${windowName}Countdown`);
            const bar = document.getElementById(`win${windowName}Progress`);

            if (!data) {
                percentElement.textContent = '—';
                remainingElement.textContent = '—';
                totalElement.textContent = '—';
                if (usedElement) usedElement.textContent = '—';
                if (countdownElement) setIconLabel(countdownElement, 'clock', '—');
                renderProgressBar(bar, 0, 100, { unlimited: false });
                return 0;
            }

            if (!configured) {
                percentElement.textContent = '无限额';
                remainingElement.innerHTML = `${iconMarkup('infinity', 'icon-14')} <span class="limit-unit">未限制</span>`;
                totalElement.textContent = formatLimitChange(data?.limitChange, '无窗口上限');
                if (usedElement) usedElement.textContent = '不计入限制';
                if (countdownElement) setIconLabel(countdownElement, 'infinity', '无需重置');
                renderProgressBar(bar, 100, 100, { unlimited: true });
                return 100;
            }

            percentElement.textContent = window.appState.rateLimitProgressMode === 'used'
                ? `${(100 - percent).toFixed(2)}% 已用`
                : `${percent.toFixed(2)}% 剩余`;
            remainingElement.innerHTML = `${remaining.toFixed(4)} <span class="limit-unit">USD 可用</span>`;
            totalElement.textContent = formatLimitChange(data.limitChange, `上限 ${data.limit.toFixed(2)} USD`);
            if (usedElement) usedElement.textContent = `${data.used.toFixed(4)} USD`;
            renderProgressBar(bar, displayPercent, percent, { unlimited: false });
            return percent;
        }

        function formatLimitChange(change, fallback) {
            if (!change) return fallback;
            const previous = Number(change.previous) || 0;
            const current = Number(change.current) || 0;
            if (current <= 0 && previous > 0) {
                return `${fallback} · 已取消限制（原 ${previous.toFixed(2)}）`;
            }
            if (previous <= 0 && current > 0) {
                return `${fallback} · 新增限制`;
            }
            return `${fallback} · 已调整 ${previous.toFixed(2)}→${current.toFixed(2)}`;
        }

        function selectMostConstrainedWindow(activeKey) {
            const windows = [
                { name: '5h', data: activeKey.win5h, order: 0 },
                { name: '1d', data: activeKey.win1d, order: 1 },
                { name: '7d', data: activeKey.win7d, order: 2 }
            ].map(item => {
                const limit = Math.max(0, Number(item.data?.limit) || 0);
                const used = Math.max(0, Number(item.data?.used) || 0);
                const remaining = Math.max(0, Math.min(limit, Number(item.data?.remaining) || 0));
                return {
                    ...item,
                    limit,
                    remaining,
                    configured: limit > 0,
                    percent: limit > 0 ? Math.max(0, Math.min(100, (remaining / limit) * 100)) : 100
                };
            });
            const configured = windows.filter(item => item.configured);
            return (configured.length ? configured : windows).sort((left, right) =>
                left.percent - right.percent ||
                left.remaining - right.remaining ||
                left.order - right.order
            )[0];
        }

        function renderMicroLimitWindows(activeKey) {
            const windows = ['5h', '1d', '7d'].map((name, order) => {
                const data = activeKey[`win${name}`] || {};
                const limit = Math.max(0, Number(data.limit) || 0);
                const used = Math.max(0, Number(data.used) || 0);
                const remaining = Math.max(0, Math.min(limit, Number(data.remaining) || 0));
                return {
                    name, order, data, limit, remaining,
                    configured: limit > 0,
                    percent: limit > 0 ? Math.max(0, Math.min(100, remaining / limit * 100)) : 100
                };
            }).sort((left, right) =>
                Number(right.configured) - Number(left.configured) ||
                left.percent - right.percent || left.remaining - right.remaining || left.order - right.order
            );

            windows.forEach((item, index) => {
                const row = document.getElementById(`micro${item.name}Row`);
                const remain = document.getElementById(`micro${item.name}Remain`);
                const suffix = document.getElementById(`micro${item.name}Suffix`);
                const percent = document.getElementById(`micro${item.name}Percent`);
                const progress = document.getElementById(`micro${item.name}Progress`);
                row.style.order = String(index);
                row.classList.toggle('is-tightest', index === 0);
                remain.textContent = item.configured ? item.remaining.toFixed(2) : '不限';
                suffix.textContent = item.configured ? ' USD 可用' : '';
                const displayPercent = window.appState.rateLimitProgressMode === 'used'
                    ? 100 - item.percent
                    : item.percent;
                percent.textContent = item.configured
                    ? `${displayPercent.toFixed(1)}% ${window.appState.rateLimitProgressMode === 'used' ? '已用' : '剩余'}`
                    : '无限额';
                renderProgressBar(progress, item.configured ? displayPercent : 100, item.percent, {
                    unlimited: !item.configured
                });
            });

            window.microTightestWindow = windows[0];
            document.getElementById('microCountdownLabel').textContent = `${windows[0].name} 窗口重置`;
        }

        function updateUI() {
            const keys = window.appState.keys;
            const activeKey = getActiveKey();
            if (!activeKey) {
                renderKeySwitcher();
                clearDashboard();
                return;
            }

            // 1. 切换密钥菜单刷新
            renderKeySwitcher();
            const generationKeyLabel = document.getElementById('imageGenerationKeyLabel');
            if (generationKeyLabel) generationKeyLabel.textContent = `当前密钥：${activeKey.name}`;
            if (window.appState.activeTitleBarMode === 'minimal') {
                document.getElementById('keyToolbar').title = `当前密钥：${activeKey.name}`;
            }

            // 2. 算可用率
            const remaining = Number.isFinite(activeKey.remainingQuota) ? activeKey.remainingQuota : (activeKey.totalQuota - activeKey.usedQuota);
            const rawPercent = activeKey.totalQuota > 0 ? (remaining / activeKey.totalQuota) * 100 : 0;
            const percentStr = rawPercent.toFixed(2) + "%";

            // 3. 常规与微型模式并列渲染
            document.getElementById('bigRemainingVal').textContent = remaining.toFixed(6);
            document.getElementById('microRemainVal').textContent = remaining.toFixed(2);

            document.getElementById('bigTotalVal').textContent = formatLimitChange(
                activeKey.quotaLimitChange,
                activeKey.totalQuota.toFixed(2) + " USD"
            );
            document.getElementById('bigPercentVal').textContent = percentStr + " 可用";
            document.getElementById('microPercentVal').textContent = percentStr;

            // 4. 应用自适应阈值颜色
            const bigProgress = document.getElementById('bigProgressBar');
            const microProgress = document.getElementById('microProgressBar');

            renderProgressBar(bigProgress, rawPercent, rawPercent, { unlimited: false });
            renderProgressBar(microProgress, rawPercent, rawPercent, { unlimited: false });

            // 5. 极简模式：三个限制按剩余比例排序，最紧张窗口始终优先。
            renderMicroLimitWindows(activeKey);

            // 6. 三大详细窗口深度渲染
            renderLimitWindow('5h', activeKey.win5h);
            renderLimitWindow('1d', activeKey.win1d);
            renderLimitWindow('7d', activeKey.win7d);
            const hasWindowLimits = [activeKey.win5h, activeKey.win1d, activeKey.win7d]
                .some(item => Number(item?.limit) > 0);
            setIconLabel(
                document.getElementById('limitModeLabel'),
                hasWindowLimits ? 'shield' : 'infinity',
                hasWindowLimits ? '受限模式 (quota)' : '无限额模式',
                hasWindowLimits ? 'icon-10 color-brand' : 'icon-10 color-good'
            );

            // 激活状态标签
            const statusLabel = document.getElementById('topKeyStatus');
            if (activeKey.status === 'active') {
                statusLabel.className = "key-status status-active";
                statusLabel.textContent = "有效 ACTIVE";
            } else {
                statusLabel.className = "key-status status-disabled";
                statusLabel.textContent = "失效 DISABLED";
            }

            // 隐藏部分敏感信息
            document.getElementById('maskedKeyText').textContent = activeKey.value;
            const models = Array.isArray(activeKey.models) ? activeKey.models : [];
            document.getElementById('modelsCount').textContent = `${models.length || Number(activeKey.modelsCount) || 0} 个可用`;

            // 天数倒计时
            const remainingDays = activeKey.expireTimestamp ? Math.ceil((activeKey.expireTimestamp - Date.now()) / (1000 * 60 * 60 * 24)) : null;
            document.getElementById('expireDaysLeft').textContent = remainingDays === null
                ? '长期有效'
                : remainingDays < 0
                    ? '已过期'
                    : `${remainingDays} 天后到期`;

            // 用量次数
            document.getElementById('todayUsageVal').textContent = activeKey.todayCost.toFixed(7) + " USD";
            document.getElementById('todayRequestsVal').textContent = `共 ${activeKey.todayRequests} 次成功请求`;
            document.getElementById('totalUsageVal').textContent = activeKey.totalCost.toFixed(7) + " USD";

            // 计算使用流速
            calculateUsageRateMetrics(activeKey);
            updateClockCountdowns();
            renderLucideIcons();
        }

        function calculateUsageRateMetrics(activeKey) {
            const rates = activeKey.rates || {};
            renderIntervalMetric(activeKey, rates.intervals?.['10m'], rates.speed10m, 'speed10mVal', 'speed10mStatus', 600);
            renderIntervalMetric(activeKey, rates.intervals?.['1h'], rates.speed1h, 'speed1hVal', 'speed1hStatus', 3600);
            renderAverageMetrics(activeKey);
            renderUsageTrend(activeKey);
        }

        function renderIntervalMetric(activeKey, interval, fallbackValue, valueId, statusId, intervalSeconds) {
            const valueElement = document.getElementById(valueId);
            const statusElement = document.getElementById(statusId);
            const value = interval ? Number(interval.value) : Number(fallbackValue);
            const status = interval?.status || (Number.isFinite(value) ? 'recorded' : 'unrecorded');
            if (status === 'unrecorded' || !Number.isFinite(value)) {
                valueElement.textContent = '未记录';
                applyLoadStatus(statusElement, null, 'unrecorded');
                return;
            }
            valueElement.textContent = "$" + Math.max(0, value).toFixed(4);
            const load = calculateLoadComponents(activeKey, Math.max(0, value));
            applyLoadStatus(
                statusElement,
                load.overall,
                status,
                load
            );
        }

        function setAveragePeriod(period) {
            window.appState.averagePeriod = period === 'week' ? 'week' : 'today';
            const activeKey = getActiveKey();
            if (activeKey) renderAverageMetrics(activeKey);
        }

        function setTrendPeriod(period) {
            window.appState.trendPeriod = period === '10m' ? '10m' : '1h';
            renderUsageTrend(getActiveKey());
        }

        function renderAverageMetrics(activeKey) {
            const period = window.appState.averagePeriod === 'week' ? 'week' : 'today';
            const rates = activeKey?.rates || {};
            const averages = rates.averages || {};
            const selected = averages[period] || {
                avgMin: Number(rates.avgMin) || 0,
                avgHour: Number(rates.avgHour) || 0,
                label: ''
            };
            document.getElementById('avgMinVal').textContent = "$" + (Number(selected.avgMin) || 0).toFixed(4);
            document.getElementById('avgHourVal').textContent = "$" + (Number(selected.avgHour) || 0).toFixed(4);
            document.getElementById('averagePeriodLabel').textContent = [
                rates.timezone || 'UTC+8',
                period === 'week' ? '自然周' : '自然日',
                selected.label || ''
            ].filter(Boolean).join(' · ');

            const todayButton = document.getElementById('averageTodayButton');
            const weekButton = document.getElementById('averageWeekButton');
            todayButton.classList.toggle('is-active', period === 'today');
            weekButton.classList.toggle('is-active', period === 'week');
        }

        const LOAD_NODE_COLORS = [
            [0, '#10b981'],
            [40, '#facc15'],
            [65, '#f97316'],
            [85, '#f43f5e']
        ];
        const LOAD_NODE_COLOR_INDEX = new Map([
            ['#10b981', 0],
            ['#facc15', 1],
            ['#f97316', 2],
            ['#f43f5e', 3]
        ]);
        const LOAD_TRANSITION_RECIPES = {
            '0-1': [[0, '#10b981'], [0.34, '#65c466'], [0.62, '#a8cf18'], [0.82, '#facc15'], [1, '#facc15']],
            '0-2': [[0, '#10b981'], [0.18, '#65c466'], [0.34, '#a8cf18'], [0.48, '#facc15'], [0.68, '#facc15'], [0.84, '#f5a916'], [1, '#f97316']],
            '0-3': [[0, '#10b981'], [0.12, '#65c466'], [0.24, '#a8cf18'], [0.36, '#facc15'], [0.62, '#facc15'], [0.76, '#f5a916'], [0.88, '#f97316'], [0.94, '#f85a35'], [1, '#f43f5e']],
            '1-2': [[0, '#facc15'], [0.5, '#facc15'], [0.76, '#f5a916'], [1, '#f97316']],
            '1-3': [[0, '#facc15'], [0.34, '#facc15'], [0.54, '#f5a916'], [0.72, '#f97316'], [0.88, '#f85a35'], [1, '#f43f5e']],
            '2-3': [[0, '#f97316'], [0.46, '#f97316'], [0.72, '#f85a35'], [1, '#f43f5e']]
        };

        function loadColor(pressure) {
            const value = Math.max(0, Math.min(100, Number(pressure) || 0));
            if (value >= 85) return LOAD_NODE_COLORS[3][1];
            if (value >= 65) return LOAD_NODE_COLORS[2][1];
            if (value >= 40) return LOAD_NODE_COLORS[1][1];
            return LOAD_NODE_COLORS[0][1];
        }

        function transitionRecipe(fromColor, toColor) {
            const fromIndex = LOAD_NODE_COLOR_INDEX.get(fromColor);
            const toIndex = LOAD_NODE_COLOR_INDEX.get(toColor);
            if (fromIndex === toIndex) return [[0, fromColor], [1, toColor]];
            const lowIndex = Math.min(fromIndex, toIndex);
            const highIndex = Math.max(fromIndex, toIndex);
            const recipe = LOAD_TRANSITION_RECIPES[`${lowIndex}-${highIndex}`];
            return fromIndex < toIndex
                ? recipe
                : recipe.slice().reverse().map(([position, color]) => [1 - position, color]);
        }

        function usageGradientStops(points, width) {
            const stops = [];
            points.forEach((point, index) => {
                if (!point.recorded) return;
                const color = loadColor(point.pressure);
                stops.push({ x: point.x, color });
                const next = points[index + 1];
                if (!next?.recorded) return;
                const nextColor = loadColor(next.pressure);
                transitionRecipe(color, nextColor).slice(1, -1).forEach(([position, transitionColor]) => {
                    stops.push({
                        x: point.x + (next.x - point.x) * position,
                        color: transitionColor
                    });
                });
            });
            return stops.sort((left, right) => left.x - right.x).map(stop => ({
                offset: Math.max(0, Math.min(100, stop.x / width * 100)),
                color: stop.color
            }));
        }

        function smoothTrendPath(points) {
            if (!points.length) return '';
            if (points.length === 1) return `M ${points[0].x} ${points[0].y}`;
            let path = `M ${points[0].x} ${points[0].y}`;
            for (let index = 0; index < points.length - 1; index++) {
                const current = points[index];
                const next = points[index + 1];
                const controlX = (current.x + next.x) / 2;
                path += ` C ${controlX} ${current.y}, ${controlX} ${next.y}, ${next.x} ${next.y}`;
            }
            return path;
        }

        function renderUsageTrend(activeKey) {
            const svg = document.getElementById('usageTrendChart');
            const nodeLayer = document.getElementById('trendNodeLayer');
            const tooltip = document.getElementById('trendTooltip');
            if (!svg || !nodeLayer || !tooltip) return;
            svg.replaceChildren();
            nodeLayer.replaceChildren();
            tooltip.classList.add('hidden');

            const period = window.appState.trendPeriod === '10m' ? '10m' : '1h';
            const periodSeconds = period === '10m' ? 600 : 3600;
            const source = period === '10m' ? activeKey?.rates?.tenMinute2h : activeKey?.rates?.hourly12h;
            const buckets = Array.isArray(source)
                ? source.slice(-12)
                : [];
            document.getElementById('usageTrendTitle').textContent = period === '10m'
                ? '最近 2h · 每 10min 用量'
                : '最近 12h · 每 1h 用量';
            document.getElementById('trend1hButton').classList.toggle('is-active', period === '1h');
            document.getElementById('trend10mButton').classList.toggle('is-active', period === '10m');
            svg.setAttribute('aria-label', period === '10m'
                ? '最近 2 小时逐 10 分钟用量曲线'
                : '最近 12 小时逐小时用量曲线');
            const validBuckets = buckets.filter(item => item.status !== 'unrecorded' && Number.isFinite(Number(item.cost)));
            if (!validBuckets.length) {
                const emptyText = document.createElementNS('http://www.w3.org/2000/svg', 'text');
                emptyText.setAttribute('x', '300');
                emptyText.setAttribute('y', '64');
                emptyText.setAttribute('text-anchor', 'middle');
                emptyText.setAttribute('fill', '#94a3b8');
                emptyText.setAttribute('font-size', '11');
                emptyText.textContent = period === '10m' ? '暂无可用 10 分钟记录' : '暂无可用小时记录';
                svg.append(emptyText);
                return;
            }

            const width = 600;
            const height = 120;
            const paddingX = 12;
            const paddingY = 14;
            const maxCost = Math.max(...validBuckets.map(item => Math.max(0, Number(item.cost))), 0.0001);
            const points = buckets.map((bucket, index) => {
                const recorded = bucket.status !== 'unrecorded' && Number.isFinite(Number(bucket.cost));
                const cost = recorded ? Math.max(0, Number(bucket.cost)) : null;
                const x = buckets.length === 1
                    ? width / 2
                    : paddingX + index * ((width - paddingX * 2) / (buckets.length - 1));
                const y = recorded
                    ? height - paddingY - (cost / maxCost) * (height - paddingY * 2)
                    : height - paddingY;
                const pressure = recorded ? calculateLoadPressure(activeKey, cost, periodSeconds) : null;
                return { x, y, cost, pressure, recorded, bucket };
            });

            const defs = document.createElementNS('http://www.w3.org/2000/svg', 'defs');
            const gradient = document.createElementNS('http://www.w3.org/2000/svg', 'linearGradient');
            gradient.setAttribute('id', 'usageLoadGradient');
            gradient.setAttribute('gradientUnits', 'userSpaceOnUse');
            gradient.setAttribute('x1', '0');
            gradient.setAttribute('x2', String(width));
            usageGradientStops(points, width).forEach(({ offset, color }) => {
                const stop = document.createElementNS('http://www.w3.org/2000/svg', 'stop');
                stop.setAttribute('offset', `${offset}%`);
                stop.setAttribute('stop-color', color);
                gradient.append(stop);
            });
            const fadeGradient = document.createElementNS('http://www.w3.org/2000/svg', 'linearGradient');
            fadeGradient.setAttribute('id', 'usageAreaFade');
            fadeGradient.setAttribute('x1', '0%');
            fadeGradient.setAttribute('y1', '0%');
            fadeGradient.setAttribute('x2', '0%');
            fadeGradient.setAttribute('y2', '100%');
            [
                ['0%', '0.34'],
                ['58%', '0.12'],
                ['100%', '0']
            ].forEach(([offset, opacity]) => {
                const stop = document.createElementNS('http://www.w3.org/2000/svg', 'stop');
                stop.setAttribute('offset', offset);
                stop.setAttribute('stop-color', '#ffffff');
                stop.setAttribute('stop-opacity', opacity);
                fadeGradient.append(stop);
            });
            const mask = document.createElementNS('http://www.w3.org/2000/svg', 'mask');
            mask.setAttribute('id', 'usageAreaFadeMask');
            const maskRect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
            maskRect.setAttribute('width', width);
            maskRect.setAttribute('height', height);
            maskRect.setAttribute('fill', 'url(#usageAreaFade)');
            mask.append(maskRect);
            defs.append(gradient, fadeGradient, mask);
            svg.append(defs);

            [0.25, 0.5, 0.75].forEach(ratio => {
                const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
                line.setAttribute('x1', paddingX);
                line.setAttribute('x2', width - paddingX);
                line.setAttribute('y1', paddingY + (height - paddingY * 2) * ratio);
                line.setAttribute('y2', paddingY + (height - paddingY * 2) * ratio);
                line.setAttribute('stroke', '#94a3b8');
                line.setAttribute('stroke-opacity', '0.16');
                line.setAttribute('stroke-width', '1');
                line.setAttribute('vector-effect', 'non-scaling-stroke');
                svg.append(line);
            });

            const segments = [];
            points.forEach((point, index) => {
                if (!point.recorded) return;
                if (!segments.length || points[index - 1]?.recorded !== true) segments.push([]);
                segments[segments.length - 1].push(point);
            });
            segments.forEach(segment => {
                const linePath = smoothTrendPath(segment);
                const area = document.createElementNS('http://www.w3.org/2000/svg', 'path');
                area.setAttribute('d', `${linePath} L ${segment[segment.length - 1].x} ${height - paddingY} L ${segment[0].x} ${height - paddingY} Z`);
                area.setAttribute('fill', 'url(#usageLoadGradient)');
                area.setAttribute('mask', 'url(#usageAreaFadeMask)');
                area.setAttribute('pointer-events', 'none');
                svg.append(area);

                const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
                path.setAttribute('d', linePath);
                path.setAttribute('fill', 'none');
                path.setAttribute('stroke', 'url(#usageLoadGradient)');
                path.setAttribute('stroke-width', '2.5');
                path.setAttribute('stroke-linecap', 'round');
                path.setAttribute('vector-effect', 'non-scaling-stroke');
                svg.append(path);
            });

            const hitWidth = (width - paddingX * 2) / Math.max(1, buckets.length - 1);
            points.forEach((point, index) => {
                if (point.recorded) {
                    const node = document.createElement('span');
                    node.className = 'trend-point';
                    node.style.left = `${(point.x / width) * 100}%`;
                    node.style.top = `${(point.y / height) * 100}%`;
                    node.style.backgroundColor = loadColor(point.pressure);
                    nodeLayer.append(node);
                }

                const hitZone = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
                hitZone.setAttribute('class', 'trend-hit-zone');
                hitZone.setAttribute('x', Math.max(0, point.x - hitWidth / 2));
                hitZone.setAttribute('y', '0');
                hitZone.setAttribute('width', Math.min(hitWidth, width - Math.max(0, point.x - hitWidth / 2)));
                hitZone.setAttribute('height', height);
                hitZone.addEventListener('mouseenter', () => showTrendTooltip(point, index, points.length));
                hitZone.addEventListener('mousemove', () => showTrendTooltip(point, index, points.length));
                hitZone.addEventListener('mouseleave', () => tooltip.classList.add('hidden'));
                svg.append(hitZone);
            });
        }

        function showTrendTooltip(point, index, pointCount) {
            const tooltip = document.getElementById('trendTooltip');
            const start = new Date(Number(point.bucket.startTimestamp));
            const end = new Date(Number(point.bucket.endTimestamp));
            const timeFormat = { hour: '2-digit', minute: '2-digit', hour12: false };
            tooltip.replaceChildren();
            const timeRow = document.createElement('div');
            timeRow.className = 'tooltip-title';
            timeRow.textContent = `${start.toLocaleTimeString('zh-CN', timeFormat)}–${end.toLocaleTimeString('zh-CN', timeFormat)}`;
            const valueRow = document.createElement('div');
            valueRow.className = 'tooltip-value';
            valueRow.textContent = point.recorded
                ? `${point.bucket.status === 'estimated' ? '估算' : '用量'} $${point.cost.toFixed(4)} · 负载 ${point.pressure.toFixed(0)}%`
                : '未记录';
            tooltip.append(timeRow, valueRow);
            tooltip.style.left = `${pointCount <= 1 ? 50 : (index / (pointCount - 1)) * 100}%`;
            tooltip.style.top = `${Math.max(14, (point.y / 120) * 100)}%`;
            const translateX = index === 0 ? '0' : index === pointCount - 1 ? '-100%' : '-50%';
            const translateY = point.y < 54 ? '8px' : 'calc(-100% - 8px)';
            tooltip.style.transform = `translate(${translateX}, ${translateY})`;
            tooltip.classList.remove('hidden');
        }

        function openModelModal() {
            const activeKey = getActiveKey();
            if (!activeKey) return;
            const models = Array.isArray(activeKey.models) ? activeKey.models : [];
            const modal = document.getElementById('modelModal');
            const list = document.getElementById('modelModalList');
            document.getElementById('modelModalSubtitle').textContent = `${activeKey.name} · ${models.length} 个模型`;
            list.replaceChildren();
            if (!models.length) {
                const empty = document.createElement('div');
                empty.className = 'model-list-empty';
                empty.textContent = '上游暂未返回可访问模型';
                list.append(empty);
            } else {
                models.forEach(model => {
                    const item = document.createElement('div');
                    item.className = 'model-list-item';
                    const icon = document.createElement('i');
                    icon.setAttribute('data-lucide', 'box');
                    icon.setAttribute('class', 'icon-11 color-brand');
                    const label = document.createElement('span');
                    label.className = 'model-list-label';
                    label.textContent = String(model);
                    label.title = String(model);
                    item.append(icon, label);
                    list.append(item);
                });
            }
            openAnimatedModal(modal);
            renderLucideIcons();
            modal.querySelector('button')?.focus();
        }

        function closeModelModal() {
            const modal = document.getElementById('modelModal');
            return closeAnimatedModal(modal);
        }

        function handleModelModalBackdrop(event) {
            if (event.target === event.currentTarget) closeModelModal();
        }

        function calculateLoadComponents(activeKey, intervalCost) {
            const cost = Math.max(0, Number(intervalCost) || 0);
            const totalQuota = Math.max(0, Number(activeKey.totalQuota) || 0);
            const quotaPercent = totalQuota > 0 ? cost / totalQuota * 100 : 0;
            const ratePercent = [activeKey.win5h, activeKey.win1d, activeKey.win7d]
                .map(item => Math.max(0, Number(item?.limit) || 0))
                .filter(limit => limit > 0)
                .reduce((highest, limit) => Math.max(highest, cost / limit * 100), 0);
            const points = [
                [0, 0], [1, 10], [5, 30], [10, 45], [25, 65], [50, 82], [100, 100]
            ];
            const quota = interpolatePressure(quotaPercent, points);
            const rate = interpolatePressure(ratePercent, points);
            const source = quota > rate
                ? '额度'
                : rate > quota
                    ? '速率'
                    : quota > 0
                        ? '额度和速率'
                        : '无';
            return { overall: Math.max(quota, rate), quota, rate, quotaPercent, ratePercent, source };
        }

        function calculateLoadPressure(activeKey, intervalCost, intervalSeconds = 3600) {
            void intervalSeconds;
            return calculateLoadComponents(activeKey, intervalCost).overall;
        }

        function interpolatePressure(value, points) {
            if (value <= points[0][0]) return points[0][1];
            for (let index = 1; index < points.length; index++) {
                const [rightValue, rightPressure] = points[index];
                const [leftValue, leftPressure] = points[index - 1];
                if (value <= rightValue) {
                    const ratio = (value - leftValue) / (rightValue - leftValue);
                    return leftPressure + (rightPressure - leftPressure) * ratio;
                }
            }
            return points[points.length - 1][1];
        }

        function applyLoadStatus(element, pressure, samplingStatus = 'recorded', load = null) {
            if (samplingStatus === 'unrecorded' || !Number.isFinite(pressure)) {
                element.textContent = '未记录';
                element.className = 'load-status status-neutral';
                return;
            }
            const levels = pressure >= 85
                ? ['极高负载', 'status-critical']
                : pressure >= 65
                    ? ['高负载', 'status-high']
                    : pressure >= 40
                        ? ['中度负载', 'status-medium']
                        : ['低负载', 'status-low'];
            element.textContent = `${samplingStatus === 'estimated' ? '估算 · ' : ''}${levels[0]} ${pressure.toFixed(0)}%`;
            element.className = `load-status ${levels[1]}`;
            element.title = load
                ? `触发来源：${load.source}；额度占比 ${load.quotaPercent.toFixed(3)}%，速率限额占比 ${load.ratePercent.toFixed(3)}%`
                : '';
        }

        const PROGRESS_BAR_IDS = [
            'bigProgressBar', 'microProgressBar',
            'micro5hProgress', 'micro1dProgress', 'micro7dProgress',
            'win5hProgress', 'win1dProgress', 'win7dProgress', 'updateProgress'
        ];

        function stableProgressSequence(length, seedText) {
            let seed = 2166136261;
            for (const character of seedText) {
                seed ^= character.charCodeAt(0);
                seed = Math.imul(seed, 16777619);
            }
            const values = Array.from({ length }, (_, index) => index);
            for (let index = length - 1; index > 0; index--) {
                seed = Math.imul(seed ^ (seed >>> 15), 2246822519) >>> 0;
                const target = seed % (index + 1);
                [values[index], values[target]] = [values[target], values[index]];
            }
            return values;
        }

        function progressMatrixSize(barElement) {
            const root = document.getElementById('widget-root');
            if (barElement.id === 'updateProgress') return 4;
            if (barElement.id.startsWith('micro') || root?.className.includes('height-summary-')) return 2;
            return barElement.id === 'bigProgressBar' ? 4 : 3;
        }

        function renderProgressBar(barElement, fillPercentage, remainingPercentage, options = {}) {
            if (!barElement) return;
            const fill = Math.max(0, Math.min(100, Number(fillPercentage) || 0));
            const remaining = Math.max(0, Math.min(100, Number(remainingPercentage) || 0));
            const track = barElement.parentElement;
            const matrix = progressMatrixSize(barElement);
            const trackStyle = track ? getComputedStyle(track) : null;
            const trackContentWidth = track
                ? track.clientWidth
                    - parseFloat(trackStyle?.borderLeftWidth || '0')
                    - parseFloat(trackStyle?.borderRightWidth || '0')
                    - parseFloat(trackStyle?.paddingLeft || '0')
                    - parseFloat(trackStyle?.paddingRight || '0')
                : 0;
            const barHorizontalPadding = 4;
            const edgeSafety = 2;
            const availableWidth = Math.max(
                0,
                Math.floor(trackContentWidth - barHorizontalPadding - edgeSafety)
            );
            const dotSize = matrix === 4 ? 3 : matrix === 3 ? 3 : 2;
            const dotGap = 1;
            const blockGap = matrix === 2 ? 2 : 3;
            const blockSize = matrix * dotSize + (matrix - 1) * dotGap;
            const blockCount = Math.max(0, Math.floor((availableWidth + blockGap) / (blockSize + blockGap)));
            const minBlocks = matrix === 4 ? 8 : matrix === 3 ? 7 : 10;
            const useMatrix = availableWidth > 0 && blockCount >= minBlocks;

            barElement.dataset.fill = String(fill);
            barElement.dataset.remaining = String(remaining);
            barElement.dataset.unlimited = options.unlimited ? 'true' : 'false';
            barElement.dataset.update = options.update ? 'true' : 'false';
            barElement.dataset.active = options.active ? 'true' : 'false';
            barElement.className = useMatrix ? 'matrix-progress' : 'linear-progress';
            barElement.classList.toggle('bar-unlimited', options.unlimited === true);
            barElement.classList.toggle('update-progress', options.update === true);
            barElement.classList.toggle('is-active', options.active === true);
            if (!options.unlimited) setFlexibleBarColor(barElement, remaining);

            if (!useMatrix) {
                if (barElement.childElementCount) barElement.replaceChildren();
                barElement.style.width = '100%';
                barElement.style.maxWidth = '100%';
                barElement.style.setProperty('--linear-progress', String(fill / 100));
                barElement.dataset.renderSignature = `linear:${fill}`;
                return;
            }

            const matrixContentWidth = blockCount * blockSize + Math.max(0, blockCount - 1) * blockGap;
            barElement.style.width = `${matrixContentWidth + barHorizontalPadding}px`;
            barElement.style.maxWidth = '100%';
            barElement.style.setProperty('--matrix-size', matrix);
            barElement.style.setProperty('--matrix-dot-size', `${dotSize}px`);
            barElement.style.setProperty('--matrix-dot-gap', `${dotGap}px`);
            barElement.style.setProperty('--matrix-block-gap', `${blockGap}px`);
            barElement.style.setProperty('--matrix-block-count', blockCount);
            const dotsPerBlock = matrix * matrix;
            const totalDots = blockCount * dotsPerBlock;
            const filledDots = Math.round(totalDots * fill / 100);
            const renderSignature = `matrix:${matrix}:${blockCount}:${filledDots}`;
            if (barElement.dataset.renderSignature === renderSignature) return;
            barElement.dataset.renderSignature = renderSignature;
            const fragment = document.createDocumentFragment();
            let consumed = 0;

            for (let blockIndex = 0; blockIndex < blockCount; blockIndex++) {
                const block = document.createElement('span');
                block.className = 'matrix-block';
                const blockFill = Math.max(0, Math.min(dotsPerBlock, filledDots - consumed));
                if (blockFill === dotsPerBlock) block.classList.add('is-complete');
                else if (blockFill > 0) block.classList.add('is-active');
                const sequence = stableProgressSequence(dotsPerBlock, `${barElement.id}:${matrix}:${blockIndex}`);
                const filledIndexes = new Set(sequence.slice(0, blockFill));
                for (let dotIndex = 0; dotIndex < dotsPerBlock; dotIndex++) {
                    const dot = document.createElement('i');
                    dot.className = filledIndexes.has(dotIndex) ? 'matrix-dot is-filled' : 'matrix-dot';
                    block.append(dot);
                }
                fragment.append(block);
                consumed += blockFill;
            }
            barElement.replaceChildren(fragment);
        }

        function rerenderProgressBars() {
            PROGRESS_BAR_IDS.forEach(id => {
                const bar = document.getElementById(id);
                if (!bar || bar.dataset.fill === undefined) return;
                renderProgressBar(bar, bar.dataset.fill, bar.dataset.remaining, {
                    unlimited: bar.dataset.unlimited === 'true',
                    update: bar.dataset.update === 'true',
                    active: bar.dataset.active === 'true'
                });
            });
        }

        function initializeProgressResizeObserver() {
            if (!('ResizeObserver' in window) || window.__progressTrackResizeObserver) return;
            const widths = new WeakMap();
            const pendingBars = new Set();
            const observer = new ResizeObserver(entries => {
                entries.forEach(entry => {
                    const track = entry.target;
                    const bar = track.firstElementChild;
                    if (!bar || !PROGRESS_BAR_IDS.includes(bar.id)) return;
                    const width = Math.round(entry.contentRect.width);
                    if (width <= 0 || widths.get(track) === width) return;
                    widths.set(track, width);
                    pendingBars.add(bar);
                });
                if (!pendingBars.size) return;
                cancelAnimationFrame(window.__progressTrackResizeFrame || 0);
                window.__progressTrackResizeFrame = requestAnimationFrame(() => {
                    pendingBars.forEach(bar => {
                        if (bar.dataset.fill === undefined) return;
                        renderProgressBar(bar, bar.dataset.fill, bar.dataset.remaining, {
                            unlimited: bar.dataset.unlimited === 'true',
                            update: bar.dataset.update === 'true',
                            active: bar.dataset.active === 'true'
                        });
                    });
                    pendingBars.clear();
                });
            });
            PROGRESS_BAR_IDS.forEach(id => {
                const track = document.getElementById(id)?.parentElement;
                if (track) observer.observe(track);
            });
            window.__progressTrackResizeObserver = observer;
        }

        function setFlexibleBarColor(barElement, percentage) {
            const th = window.appState.thresholds;

            barElement.classList.remove('bar-good', 'bar-warn', 'bar-danger', 'bar-critical', 'bar-unlimited');

            // 根据用户自定义阈值动态决定颜色
            if (percentage > th.warn) {
                barElement.classList.add('bar-good');
            } else if (percentage > th.danger) {
                barElement.classList.add('bar-warn');
            } else if (percentage > th.critical) {
                barElement.classList.add('bar-danger');
            } else {
                barElement.classList.add('bar-critical');
            }
        }

        function updateClockCountdowns() {
            window.ctxTime = new Date();

            // 底部时钟同步显示
            const clockEl = document.getElementById('liveClock');
            if (clockEl) {
                clockEl.textContent = window.ctxTime.toLocaleString('zh-CN', { hour12: false });
            }

            const activeKey = getActiveKey();
            if (!activeKey) return;

            // Android 只在前台由页面定时器刷新；桌面端仍显示后端调度倒计时。
            if (window.appState.refreshCounter > 1) {
                window.appState.refreshCounter--;
                document.getElementById('refreshTimerVal')?.replaceChildren(`${window.appState.refreshCounter}s`);
            } else {
                const intervals = window.appState.refreshIntervals;
                window.appState.refreshCounter = isAndroidPlatform()
                    ? 60
                    : (window.appState.isTabActive ? intervals.foreground : intervals.background);
                document.getElementById('refreshTimerVal')?.replaceChildren(`${window.appState.refreshCounter}s`);
            }

            ['5h', '1d', '7d'].forEach(windowName => {
                const data = activeKey[`win${windowName}`] || {};
                const countdownId = `win${windowName}Countdown`;
                if (Number(data.limit) <= 0) {
                    setIconLabel(document.getElementById(countdownId), 'infinity', '无需重置');
                } else if (!data.resetTime || !Number.isFinite(new Date(data.resetTime).getTime())) {
                    setIconLabel(document.getElementById(countdownId), 'clock', '重置时间未知');
                } else {
                    renderFormattedCountdown(countdownId, new Date(data.resetTime).getTime() - window.ctxTime.getTime());
                }
            });

            const tightest = window.microTightestWindow;
            const microCountdown = document.getElementById('microCountdownValue');
            if (tightest && !tightest.configured) {
                microCountdown.textContent = '无需重置';
            } else if (!tightest?.data?.resetTime || !Number.isFinite(new Date(tightest.data.resetTime).getTime())) {
                microCountdown.textContent = '重置时间未知';
            } else {
                renderTextCountdown(microCountdown, new Date(tightest.data.resetTime).getTime() - window.ctxTime.getTime());
            }
        }

        function formatCountdown(diff) {
            if (diff <= 0) return '正在刷新重置';
            const msInDay = 24 * 60 * 60 * 1000;
            const msInHour = 60 * 60 * 1000;
            const msInMinute = 60 * 1000;
            if (diff >= msInDay) {
                const days = Math.floor(diff / msInDay);
                const hrs = Math.floor((diff % msInDay) / msInHour).toString().padStart(2, '0');
                const mins = Math.floor((diff % msInHour) / msInMinute).toString().padStart(2, '0');
                return `${days}d:${hrs}h:${mins}m 后`;
            }
            const hrs = Math.floor(diff / msInHour).toString().padStart(2, '0');
            const mins = Math.floor((diff % msInHour) / msInMinute).toString().padStart(2, '0');
            const secs = Math.floor((diff % msInMinute) / 1000).toString().padStart(2, '0');
            return `${hrs}h:${mins}m:${secs}s 后`;
        }

        function renderTextCountdown(targetEl, diff) {
            if (targetEl) targetEl.textContent = formatCountdown(diff);
        }

        function renderFormattedCountdown(containerId, diff) {
            const targetEl = document.getElementById(containerId);
            if (!targetEl) return;
            setIconLabel(targetEl, 'clock', formatCountdown(diff));
        }

        async function saveKey(event) {
            event.preventDefault();

            const nickname = document.getElementById('keyNickname').value.trim();
            const value = document.getElementById('keyValue').value.trim();
            const newKey = {
                name: nickname,
                value: value
            };

            if (window.dbSaveKey) {
                try {
                    await window.dbSaveKey(newKey);
                    toggleFormPanel();
                    document.getElementById('keyForm').reset();
                } catch (error) {
                    console.error('保存密钥失败:', error);
                }
            }
        }

        function showToast(message, type = "success") {
            const toast = document.getElementById('toast');
            const toastIcon = document.getElementById('toastIcon');
            const toastMsg = document.getElementById('toastMessage');

            toastMsg.textContent = message;
            const iconName = type === 'success' ? 'circle-check' : type === 'info' ? 'info' : 'circle-alert';
            const iconColor = type === 'success' ? 'color-good' : type === 'info' ? 'color-info' : 'color-critical';
            setLucideIcon(toastIcon, iconName, `icon-14 ${iconColor}`);

            toast.classList.add('is-visible');

            setTimeout(() => {
                toast.classList.remove('is-visible');
            }, 2500);
        }
