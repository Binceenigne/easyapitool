        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', initializePage, { once: true });
        } else {
            initializePage();
        }

        window.addEventListener('load', () => {
            renderLucideIcons();
            reportFrontendStartup('frontend_load');
        });
