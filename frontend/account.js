"use strict";

const els = {
  accountInfo: document.getElementById("account-info"),
  accountEmail: document.getElementById("account-email"),
  accountCreated: document.getElementById("account-created"),
  logoutButton: document.getElementById("logout-button"),
  authForms: document.getElementById("auth-forms"),
  tabLogin: document.getElementById("tab-login"),
  tabRegister: document.getElementById("tab-register"),
  loginForm: document.getElementById("login-form"),
  registerForm: document.getElementById("register-form"),
  loginError: document.getElementById("login-error"),
  registerError: document.getElementById("register-error"),
};

const NEXT_PAGES = { cart: "/cart.html", orders: "/orders.html" };

function nextUrl() {
  const next = new URLSearchParams(window.location.search).get("next");
  return NEXT_PAGES[next] || "/";
}

function showLoggedIn(user) {
  els.authForms.hidden = true;
  els.accountInfo.hidden = false;
  els.accountEmail.textContent = user.email;
  els.accountCreated.textContent = formatDate(user.created_at);
}

function showLoggedOut() {
  els.accountInfo.hidden = true;
  els.authForms.hidden = false;
}

function switchTab(tab) {
  const isLogin = tab === "login";
  els.tabLogin.classList.toggle("is-active", isLogin);
  els.tabRegister.classList.toggle("is-active", !isLogin);
  els.loginForm.hidden = !isLogin;
  els.registerForm.hidden = isLogin;
}

async function submitAuth(url, body, errorEl) {
  errorEl.hidden = true;
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    errorEl.textContent = data.detail || `Server responded with HTTP ${response.status}`;
    errorEl.hidden = false;
    return false;
  }
  return true;
}

async function init() {
  initNav("account");

  const user = await fetchCurrentUser();
  if (user) {
    showLoggedIn(user);
  } else {
    showLoggedOut();
  }

  els.tabLogin.addEventListener("click", () => switchTab("login"));
  els.tabRegister.addEventListener("click", () => switchTab("register"));

  els.loginForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const ok = await submitAuth(
      "/auth/login",
      {
        email: document.getElementById("login-email").value,
        password: document.getElementById("login-password").value,
      },
      els.loginError
    );
    if (ok) window.location.assign(nextUrl());
  });

  els.registerForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const ok = await submitAuth(
      "/auth/register",
      {
        email: document.getElementById("register-email").value,
        password: document.getElementById("register-password").value,
      },
      els.registerError
    );
    if (ok) window.location.assign(nextUrl());
  });

  els.logoutButton.addEventListener("click", async () => {
    await fetch("/auth/logout", { method: "POST" });
    showLoggedOut();
  });
}

document.addEventListener("DOMContentLoaded", init);
