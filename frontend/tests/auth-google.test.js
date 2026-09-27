"use strict";

// Run with: node --test frontend/tests
// Loads the real browser script (frontend/auth-google.js) into a sandbox with a tiny fake DOM
// and fetch - no jsdom, no npm packages. Google is never contacted.

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const SCRIPT = fs.readFileSync(path.join(__dirname, "..", "auth-google.js"), "utf8");

function loadScript() {
  const context = vm.createContext({ URLSearchParams });
  vm.runInContext(SCRIPT, context);
  return context;
}

function fakeElement() {
  return {
    hidden: true,
    textContent: "",
    attributes: { href: "/auth/google/login" },
    setAttribute(name, value) {
      this.attributes[name] = value;
    },
  };
}

function fakeDocument() {
  const elements = {
    "google-signin": fakeElement(),
    "google-signin-link": fakeElement(),
    "google-error": fakeElement(),
  };
  return { elements, getElementById: (id) => elements[id] || null };
}

function fakeFetch({ ok = true, body = { google: true }, throws = false } = {}) {
  const calls = [];
  const fn = async (url) => {
    calls.push(url);
    if (throws) throw new TypeError("Failed to fetch");
    return { ok, json: async () => body };
  };
  fn.calls = calls;
  return fn;
}

// --- googleLoginUrl --------------------------------------------------------------------------

test("googleLoginUrl forwards allowed next pages", () => {
  const { googleLoginUrl } = loadScript();
  assert.equal(googleLoginUrl("?next=cart"), "/auth/google/login?next=cart");
  assert.equal(googleLoginUrl("?next=orders"), "/auth/google/login?next=orders");
});

test("googleLoginUrl drops missing or unsafe next values", () => {
  const { googleLoginUrl } = loadScript();
  for (const search of [
    "",
    "?next=",
    "?next=https://evil.example.com",
    "?next=//evil.example.com",
    "?next=/cart.html",
    "?next=cart%26x%3D1",
    "?next=account",
  ]) {
    assert.equal(googleLoginUrl(search), "/auth/google/login", search);
  }
});

// --- googleErrorMessage ----------------------------------------------------------------------

test("googleErrorMessage maps the backend's error code to a friendly message", () => {
  const { googleErrorMessage } = loadScript();
  assert.match(googleErrorMessage("?error=google&next=cart"), /Google sign-in didn't complete/);
});

test("googleErrorMessage ignores unknown or absent errors", () => {
  const { googleErrorMessage } = loadScript();
  assert.equal(googleErrorMessage(""), null);
  assert.equal(googleErrorMessage("?error=<script>alert(1)</script>"), null);
  assert.equal(googleErrorMessage("?error=toString"), null);
});

// --- initGoogleSignIn ------------------------------------------------------------------------

test("shows the Google button with next when the backend has Google enabled", async () => {
  const { initGoogleSignIn } = loadScript();
  const doc = fakeDocument();
  const fetchFn = fakeFetch();

  await initGoogleSignIn({ doc, fetchFn, search: "?next=orders" });

  assert.deepEqual(fetchFn.calls, ["/auth/providers"]);
  assert.equal(doc.elements["google-signin"].hidden, false);
  assert.equal(doc.elements["google-signin-link"].attributes.href, "/auth/google/login?next=orders");
  assert.equal(doc.elements["google-error"].hidden, true);
});

test("keeps the Google button hidden when Google is not configured", async () => {
  const { initGoogleSignIn } = loadScript();
  const doc = fakeDocument();

  await initGoogleSignIn({ doc, fetchFn: fakeFetch({ body: { google: false } }), search: "" });

  assert.equal(doc.elements["google-signin"].hidden, true);
});

test("keeps the Google button hidden when /auth/providers fails", async () => {
  const { initGoogleSignIn } = loadScript();
  for (const fetchFn of [fakeFetch({ ok: false }), fakeFetch({ throws: true })]) {
    const doc = fakeDocument();
    await initGoogleSignIn({ doc, fetchFn, search: "" });
    assert.equal(doc.elements["google-signin"].hidden, true);
  }
});

test("shows the error message after a failed Google sign-in", async () => {
  const { initGoogleSignIn } = loadScript();
  const doc = fakeDocument();

  await initGoogleSignIn({ doc, fetchFn: fakeFetch(), search: "?error=google&next=cart" });

  const errorEl = doc.elements["google-error"];
  assert.equal(errorEl.hidden, false);
  assert.match(errorEl.textContent, /Google sign-in didn't complete/);
  // The retry link keeps the destination.
  assert.equal(doc.elements["google-signin-link"].attributes.href, "/auth/google/login?next=cart");
});

// --- account.html wiring ---------------------------------------------------------------------

test("account.html includes the Google button markup and loads the script before account.js", () => {
  const html = fs.readFileSync(path.join(__dirname, "..", "account.html"), "utf8");
  assert.match(html, /id="google-signin"[^>]*hidden/);
  assert.match(html, /id="google-signin-link"[^>]*href="\/auth\/google\/login"/);
  assert.match(html, /Continue with Google/);
  assert.ok(html.indexOf("/auth-google.js") < html.indexOf("/account.js"));
  // Email/password forms are still there.
  assert.match(html, /id="login-form"/);
  assert.match(html, /id="register-form"/);
});

test("GOOGLE_NEXT_KEYS matches account.js NEXT_PAGES", () => {
  const { GOOGLE_NEXT_KEYS } = vm.runInContext(
    SCRIPT + "\n;({ GOOGLE_NEXT_KEYS })",
    vm.createContext({ URLSearchParams })
  );
  const accountJs = fs.readFileSync(path.join(__dirname, "..", "account.js"), "utf8");
  const nextPages = accountJs.match(/const NEXT_PAGES = \{([^}]*)\}/)[1];
  const keys = [...nextPages.matchAll(/(\w+):/g)].map((m) => m[1]);
  assert.deepEqual([...GOOGLE_NEXT_KEYS].sort(), keys.sort());
});

// --- Self-start ------------------------------------------------------------------------------

test("starts itself on DOMContentLoaded without any call from account.js", async () => {
  const doc = fakeDocument();
  const listeners = {};
  doc.addEventListener = (type, fn) => {
    listeners[type] = fn;
  };
  const fetchFn = fakeFetch();
  const context = vm.createContext({
    URLSearchParams,
    document: doc,
    fetch: fetchFn,
    window: { location: { search: "?next=cart" } },
  });
  vm.runInContext(SCRIPT, context);

  assert.equal(typeof listeners.DOMContentLoaded, "function");
  assert.equal(doc.elements["google-signin"].hidden, true); // nothing happens before the event
  await listeners.DOMContentLoaded();

  assert.deepEqual(fetchFn.calls, ["/auth/providers"]);
  assert.equal(doc.elements["google-signin"].hidden, false);
  assert.equal(doc.elements["google-signin-link"].attributes.href, "/auth/google/login?next=cart");
});

test("account.js no longer calls initGoogleSignIn (it would run twice)", () => {
  const accountJs = fs.readFileSync(path.join(__dirname, "..", "account.js"), "utf8");
  assert.doesNotMatch(accountJs, /initGoogleSignIn/);
});
