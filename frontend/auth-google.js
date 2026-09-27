"use strict";

// "Continue with Google" on the account page. The whole flow is a full-page redirect handled by
// the backend (/auth/google/login -> Google -> /auth/google/callback), which ends by setting the
// same session cookie as email/password login - this file only wires up the link.
// It starts itself on DOMContentLoaded (see the bottom), so it doesn't depend on account.js.

// Must match NEXT_PAGES in account.js and the backend's allowlist.
const GOOGLE_NEXT_KEYS = ["cart", "orders"];

const GOOGLE_ERROR_MESSAGES = {
  google: "Google sign-in didn't complete. Please try again, or use your email and password.",
};

function googleLoginUrl(search) {
  const next = new URLSearchParams(search).get("next");
  const base = "/auth/google/login";
  return GOOGLE_NEXT_KEYS.includes(next) ? `${base}?next=${next}` : base;
}

function googleErrorMessage(search) {
  const error = new URLSearchParams(search).get("error");
  return error && Object.hasOwn(GOOGLE_ERROR_MESSAGES, error) ? GOOGLE_ERROR_MESSAGES[error] : null;
}

async function initGoogleSignIn({
  doc = document,
  fetchFn = fetch,
  search = window.location.search,
} = {}) {
  const errorEl = doc.getElementById("google-error");
  const message = googleErrorMessage(search);
  if (errorEl && message) {
    errorEl.textContent = message;
    errorEl.hidden = false;
  }

  const block = doc.getElementById("google-signin");
  const link = doc.getElementById("google-signin-link");
  if (!block || !link) return;

  let enabled = false;
  try {
    const response = await fetchFn("/auth/providers");
    if (response.ok) enabled = Boolean((await response.json()).google);
  } catch {
    // Backend unreachable - just leave the Google button hidden.
  }
  if (!enabled) return;

  link.setAttribute("href", googleLoginUrl(search));
  block.hidden = false;
}

// Self-start in the browser. Skipped when there's no DOM (the Node tests load this file directly).
if (typeof document !== "undefined") {
  document.addEventListener("DOMContentLoaded", () => initGoogleSignIn());
}
