(function () {
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
})();
