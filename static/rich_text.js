(function (global) {
    'use strict';

    const MATH_DELIMITERS = [
        {left: '$$', right: '$$', display: true},
        {left: '\\[', right: '\\]', display: true},
        {left: '\\(', right: '\\)', display: false},
        {left: '$', right: '$', display: false}
    ];
    const MATH_PROTECT_RE = /\$\$[\s\S]+?\$\$|\\\[[\s\S]+?\\\]|\\\([\s\S]+?\\\)|\$[^\$\n]+?\$/g;
    const ALLOWED_TAGS = new Set([
        'a', 'blockquote', 'br', 'code', 'del', 'em', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
        'hr', 'img', 'li', 'ol', 'p', 'pre', 'strong', 'sub', 'sup', 'table', 'tbody', 'td',
        'th', 'thead', 'tr', 'ul'
    ]);
    const ALLOWED_ATTRIBUTES = {
        a: new Set(['href', 'title']),
        img: new Set(['src', 'alt', 'title']),
        td: new Set(['align']),
        th: new Set(['align'])
    };

    function escapeHtml(value) {
        return String(value ?? '').replace(/[&<>"']/g, character => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
        })[character]);
    }

    function sanitizeDom(root) {
        root.querySelectorAll('script,style,iframe,object,embed,link,meta').forEach(element => element.remove());
        [...root.querySelectorAll('*')].forEach(element => {
            const tag = element.tagName.toLowerCase();
            if (!ALLOWED_TAGS.has(tag)) {
                element.replaceWith(...element.childNodes);
                return;
            }
            const allowedAttributes = ALLOWED_ATTRIBUTES[tag] || new Set();
            [...element.attributes].forEach(attribute => {
                const name = attribute.name.toLowerCase();
                if (name.startsWith('on')) {
                    element.removeAttribute(attribute.name);
                    return;
                }
                if (!allowedAttributes.has(name)) {
                    element.removeAttribute(attribute.name);
                    return;
                }
                if ((name === 'href' || name === 'src') && !isSafeUrl(attribute.value)) {
                    element.removeAttribute(attribute.name);
                }
            });
        });
    }

    function isSafeUrl(value) {
        const url = String(value || '').trim();
        const compactUrl = url.replace(/[\u0000-\u0020\u007f]+/g, '');
        if (/^javascript:/i.test(compactUrl)) return false;
        if (/^[a-z][a-z0-9+.-]*:/i.test(compactUrl)) return /^(https?:|mailto:)/i.test(compactUrl);
        return true;
    }

    function render(text) {
        let raw = String(text ?? '');
        const mathSegments = [];
        raw = raw.replace(MATH_PROTECT_RE, segment => {
            mathSegments.push(segment);
            return `@@KX${mathSegments.length - 1}@@`;
        });

        let html;
        try {
            if (!global.marked || typeof global.marked.parse !== 'function') throw new Error('marked unavailable');
            html = global.marked.parse(raw, {breaks: true, gfm: true});
        } catch (error) {
            html = '<p>' + escapeHtml(raw).replace(/\n/g, '<br>') + '</p>';
        }
        html = html.replace(/@@KX(\d+)@@/g, (_, index) => escapeHtml(mathSegments[Number(index)]));

        const template = document.createElement('template');
        template.innerHTML = html;
        sanitizeDom(template.content);
        return template.innerHTML;
    }

    function renderMath(container) {
        if (!container || typeof global.renderMathInElement !== 'function') return;
        try {
            global.renderMathInElement(container, {
                delimiters: MATH_DELIMITERS,
                ignoredTags: ['script', 'noscript', 'style', 'textarea', 'pre', 'code', 'option'],
                throwOnError: false
            });
        } catch (error) {
            // Keep the original LaTeX visible when KaTeX cannot render it.
        }
    }

    global.RichText = {render, renderMath};
})(window);
