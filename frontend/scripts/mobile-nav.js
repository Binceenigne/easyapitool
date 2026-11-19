function initializeMobileNavigation() {
    if (!isAndroidPlatform()) return;
    const navigation = document.getElementById('mobileNav');
    const root = document.getElementById('widget-root');
    root.dataset.mobilePage = 'image';
    navigation.hidden = false;
    navigation.addEventListener('click', event => {
        const button = event.target.closest('[data-mobile-tab]');
        if (!button) return;
        const page = button.dataset.mobileTab;
        if (page === 'settings') {
            if (!isSettingsPanelOpen()) toggleSettingsPanel();
            return;
        }
        if (isSettingsPanelOpen()) toggleSettingsPanel();
        closeImageGenerationControls();
        closeImageReasoningMenu();
        setWorkspaceMode(page === 'usage' ? 'monitor' : 'image-edit', page === 'results');
    });
    syncMobileNavigation();
}

function syncMobileNavigation() {
    if (!isAndroidPlatform()) return;
    const root = document.getElementById('widget-root');
    const active = isSettingsPanelOpen() ? 'settings' : root.dataset.mobilePage;
    for (const button of document.querySelectorAll('#mobileNav [data-mobile-tab]')) {
        if (active === button.dataset.mobileTab) button.setAttribute('aria-current', 'page');
        else button.removeAttribute('aria-current');
    }
}
