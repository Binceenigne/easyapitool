        // 初始化加载默认色彩风格
        if (localStorage.getItem('theme') === 'light' || (!('theme' in localStorage) && window.matchMedia('(prefers-color-scheme: light)').matches)) {
            changeThemeMode('light');
        } else {
            changeThemeMode('dark');
        }
