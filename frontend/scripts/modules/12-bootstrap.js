        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', initializePage, { once: true });
        } else {
            initializePage();
        }

        window.addEventListener('load', () => reportFrontendStartup('frontend_load'));
