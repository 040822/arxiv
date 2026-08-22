"""Browser-facing contracts for the shared authentication UI module."""

import json
import pathlib
import subprocess
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
AUTH_JS = (REPO_ROOT / "static" / "auth.js").read_text(encoding="utf-8")


class AuthUiJavascriptTests(unittest.TestCase):
    def _run_node(self, assertions):
        harness = r'''
const vm = require("vm");

class Element {
    constructor(document, id) {
        this.document = document;
        this.id = id;
        this.attributes = {};
        this.listeners = {};
        this.hidden = false;
    }
    setAttribute(name, value) { this.attributes[name] = String(value); }
    getAttribute(name) { return this.attributes[name] ?? null; }
    addEventListener(type, callback) {
        (this.listeners[type] ||= []).push(callback);
    }
    dispatchEvent(type, init = {}) {
        const event = {
            key: init.key,
            target: init.target || this,
            defaultPrevented: false,
            preventDefault() { this.defaultPrevented = true; },
        };
        for (const callback of this.listeners[type] || []) callback(event);
        return event;
    }
    contains(target) { return target === this; }
    querySelectorAll(selector) {
        return selector === '[role="menuitem"]' ? this.document.menuItems : [];
    }
    focus() { this.document.activeElement = this; }
}

class Document {
    constructor() {
        this.readyState = "complete";
        this.listeners = {};
        this.activeElement = null;
        this.menuItems = [];
        this.toggle = new Element(this, "account-menu-toggle");
        this.toggle.setAttribute("aria-expanded", "false");
        this.menu = new Element(this, "account-menu");
        this.menu.hidden = true;
        this.outside = new Element(this, "outside");
        this.elements = {
            "account-menu-toggle": this.toggle,
            "account-menu": this.menu,
        };
        for (const id of ["settings", "password", "logout"]) {
            const item = new Element(this, id);
            item.setAttribute("role", "menuitem");
            this.menuItems.push(item);
        }
    }
    getElementById(id) { return this.elements[id] || null; }
    querySelector(selector) {
        return selector === 'meta[name="csrf-token"]' ? {content: "csrf-test"} : null;
    }
    addEventListener(type, callback) {
        (this.listeners[type] ||= []).push(callback);
    }
    dispatchEvent(type, init = {}) {
        const event = {target: init.target || this};
        for (const callback of this.listeners[type] || []) callback(event);
    }
}

class Headers {
    constructor() { this.values = {}; }
    set(name, value) { this.values[name] = value; }
}

const document = new Document();
let lastFetch = null;
const window = {
    document,
    location: {origin: "http://test"},
    fetch(input, options) {
        lastFetch = {input, options};
        return Promise.resolve({});
    },
};
vm.runInNewContext(%s, {window, document, Headers, console});
function assert(condition, message) {
    if (!condition) throw new Error(message);
}
%s
''' % (json.dumps(AUTH_JS), assertions)
        result = subprocess.run(
            ["node", "-e", harness],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_account_menu_has_accessible_keyboard_and_pointer_behaviour(self):
        self._run_node(r'''
const {toggle, menu, menuItems, outside} = document;
assert(toggle.getAttribute("aria-expanded") === "false", "menu starts closed");
toggle.dispatchEvent("click");
assert(toggle.getAttribute("aria-expanded") === "true" && !menu.hidden, "click opens menu");
document.dispatchEvent("click", {target: outside});
assert(toggle.getAttribute("aria-expanded") === "false" && menu.hidden, "outside click closes menu");
toggle.dispatchEvent("keydown", {key: "ArrowDown"});
assert(document.activeElement === menuItems[0], "ArrowDown focuses first item");
menu.dispatchEvent("keydown", {key: "ArrowDown"});
assert(document.activeElement === menuItems[1], "ArrowDown moves to next item");
menu.dispatchEvent("keydown", {key: "End"});
assert(document.activeElement === menuItems[2], "End focuses last item");
menu.dispatchEvent("keydown", {key: "Home"});
assert(document.activeElement === menuItems[0], "Home focuses first item");
menu.dispatchEvent("keydown", {key: "Escape"});
assert(menu.hidden && toggle.getAttribute("aria-expanded") === "false", "Escape closes menu");
assert(document.activeElement === toggle, "Escape restores focus to trigger");
toggle.dispatchEvent("click");
toggle.dispatchEvent("click");
assert(menu.hidden && toggle.getAttribute("aria-expanded") === "false", "initialization is idempotent");
toggle.dispatchEvent("click");
document.dispatchEvent("focusin", {target: outside});
assert(menu.hidden && toggle.getAttribute("aria-expanded") === "false", "focus leaving menu closes it");
''')

    def test_shared_fetch_wrapper_still_adds_csrf_for_same_origin_writes(self):
        self._run_node(r'''
window.fetch("/api/auth/logout", {method: "POST"});
assert(lastFetch.options.headers.values["X-CSRF-Token"] === "csrf-test", "CSRF wrapper preserved");
''')

    def test_auth_script_passes_node_syntax_check(self):
        result = subprocess.run(
            ["node", "--check", str(REPO_ROOT / "static" / "auth.js")],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
