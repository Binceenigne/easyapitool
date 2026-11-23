// The original Android toolbar and PC-style adjustment popup own navigation.
// Keep these hooks for the existing bootstrap/settings callers without a second bar.
function initializeMobileNavigation() {
    if (!isAndroidPlatform()) return;
    delete document.getElementById('widget-root').dataset.mobilePage;
}
function syncMobileNavigation() {}
