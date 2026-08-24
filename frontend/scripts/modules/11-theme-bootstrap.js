        // 初始化主题，并让跟随系统模式响应 Android/桌面的系统主题变化。
        const savedTheme = localStorage.getItem('theme');
        changeThemeMode(['light', 'dark', 'system'].includes(savedTheme) ? savedTheme : 'system');

        const systemThemeMedia = window.matchMedia?.('(prefers-color-scheme: dark)');
        const handleSystemThemeChange = () => {
            if (localStorage.getItem('theme') === 'system') changeThemeMode('system');
        };
        if (systemThemeMedia?.addEventListener) {
            systemThemeMedia.addEventListener('change', handleSystemThemeChange);
        } else {
            systemThemeMedia?.addListener?.(handleSystemThemeChange);
        }
