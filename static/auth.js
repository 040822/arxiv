(function (root) {
    const AuthUI = root.AuthUI || {};

    AuthUI.togglePassword = function (inputId, button) {
        const input = document.getElementById(inputId);
        if (!input) return;
        const visible = input.type === 'password';
        input.type = visible ? 'text' : 'password';
        if (button) {
            button.setAttribute('aria-pressed', String(visible));
            button.setAttribute('aria-label', visible ? '隐藏密码' : '显示密码');
            button.textContent = visible ? '🙈' : '👁';
        }
    };

    AuthUI.logout = async function (redirectUrl = '/login') {
        try {
            await fetch('/api/auth/logout', {method: 'POST'});
        } finally {
            window.location.href = redirectUrl;
        }
    };

    AuthUI.initAccountMenu = function (doc = root.document) {
        if (!doc || !doc.querySelector) return;
        const toggle = doc.getElementById('account-menu-toggle');
        const menu = doc.getElementById('account-menu');
        if (!toggle || !menu || toggle.__authMenuReady) return;
        toggle.__authMenuReady = true;

        const menuItems = () => Array.from(menu.querySelectorAll('[role="menuitem"]'));
        const setOpen = (open, focusFirst = false) => {
            toggle.setAttribute('aria-expanded', String(open));
            menu.hidden = !open;
            if (open && focusFirst) {
                menuItems()[0]?.focus();
            }
        };
        const close = (restoreFocus = false) => {
            setOpen(false);
            if (restoreFocus) toggle.focus();
        };
        const focusItem = (index) => {
            const items = menuItems();
            if (!items.length) return;
            items[(index + items.length) % items.length].focus();
        };

        toggle.addEventListener('click', () => {
            const open = toggle.getAttribute('aria-expanded') !== 'true';
            setOpen(open);
        });
        toggle.addEventListener('keydown', (event) => {
            if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
                event.preventDefault();
                setOpen(true, false);
                focusItem(event.key === 'ArrowDown' ? 0 : -1);
            } else if (event.key === 'Escape') {
                event.preventDefault();
                close();
            }
        });
        menu.addEventListener('keydown', (event) => {
            const items = menuItems();
            const current = items.indexOf(doc.activeElement);
            if (event.key === 'ArrowDown') {
                event.preventDefault();
                focusItem(current + 1);
            } else if (event.key === 'ArrowUp') {
                event.preventDefault();
                focusItem(current - 1);
            } else if (event.key === 'Home') {
                event.preventDefault();
                focusItem(0);
            } else if (event.key === 'End') {
                event.preventDefault();
                focusItem(-1);
            } else if (event.key === 'Escape') {
                event.preventDefault();
                close(true);
            }
        });
        doc.addEventListener('click', (event) => {
            if (!menu.contains(event.target) && !toggle.contains(event.target)) {
                close();
            }
        });
        doc.addEventListener('focusin', (event) => {
            if (!menu.contains(event.target) && !toggle.contains(event.target)) {
                close();
            }
        });
    };

    root.AuthUI = AuthUI;

    const initializeAccountMenu = () => AuthUI.initAccountMenu();
    if (root.document && root.document.readyState === 'loading' && root.document.addEventListener) {
        root.document.addEventListener('DOMContentLoaded', initializeAccountMenu);
    } else {
        initializeAccountMenu();
    }

    const originalFetch = window.fetch.bind(window);
    window.fetch = function (input, options) {
        const requestOptions = Object.assign({}, options || {});
        const method = String(requestOptions.method || 'GET').toUpperCase();
        const safe = ['GET', 'HEAD', 'OPTIONS', 'TRACE'].includes(method);
        const url = typeof input === 'string' ? input : input.url;
        const sameOrigin = !url || url.startsWith('/') || url.startsWith(window.location.origin);
        if (!safe && sameOrigin) {
            const token = document.querySelector('meta[name="csrf-token"]')?.content || '';
            requestOptions.headers = new Headers(requestOptions.headers || {});
            requestOptions.headers.set('X-CSRF-Token', token);
        }
        return originalFetch(input, requestOptions);
    };
})(window);
