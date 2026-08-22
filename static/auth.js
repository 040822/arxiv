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

    root.AuthUI = AuthUI;

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
