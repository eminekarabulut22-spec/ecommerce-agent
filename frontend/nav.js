"use strict";

// --- Cart (localStorage only - the server never sees it until "Proceed to checkout") -------

const CART_STORAGE_KEY = "cart";

function getCart() {
  try {
    const raw = localStorage.getItem(CART_STORAGE_KEY);
    const cart = raw ? JSON.parse(raw) : [];
    return Array.isArray(cart) ? cart : [];
  } catch {
    return [];
  }
}

function setCart(cart) {
  try {
    localStorage.setItem(CART_STORAGE_KEY, JSON.stringify(cart));
  } catch {
    // Private browsing / storage disabled - the cart just won't persist across reloads.
  }
  updateCartBadge();
}

function cartCount(cart = getCart()) {
  return cart.reduce((sum, item) => sum + item.quantity, 0);
}

function addToCart(productId, quantity = 1) {
  const cart = getCart();
  const existing = cart.find((item) => item.productId === productId);
  if (existing) {
    existing.quantity = Math.min(10, existing.quantity + quantity);
  } else {
    cart.push({ productId, quantity: Math.min(10, quantity) });
  }
  setCart(cart);
}

function setCartItemQuantity(productId, quantity) {
  let cart = getCart();
  if (quantity <= 0) {
    cart = cart.filter((item) => item.productId !== productId);
  } else {
    const existing = cart.find((item) => item.productId === productId);
    if (existing) existing.quantity = Math.min(10, quantity);
  }
  setCart(cart);
}

function removeFromCart(productId) {
  setCart(getCart().filter((item) => item.productId !== productId));
}

function clearCart() {
  setCart([]);
}

function updateCartBadge() {
  const badge = document.getElementById("nav-cart-count");
  if (!badge) return;
  const count = cartCount();
  badge.textContent = String(count);
  badge.hidden = count === 0;
}

// --- Auth -------------------------------------------------------------------------------

async function fetchCurrentUser() {
  const response = await fetch("/auth/me");
  if (!response.ok) return null;
  return response.json();
}

// --- Nav ----------------------------------------------------------------------------------

async function initNav(activePage) {
  const nav = document.getElementById("site-nav");
  if (!nav) return;
  nav.innerHTML = `
    <a href="/" data-page="products">Products</a>
    <a href="/cart.html" data-page="cart">Cart <span id="nav-cart-count" class="nav-badge" hidden>0</span></a>
    <a href="/account.html" data-page="account">My Account</a>
    <a href="/orders.html" data-page="orders">Orders</a>
  `;
  nav.querySelectorAll("a").forEach((a) => {
    if (a.dataset.page === activePage) a.classList.add("is-active");
  });
  updateCartBadge();
}

document.addEventListener("DOMContentLoaded", () => updateCartBadge());
