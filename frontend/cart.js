"use strict";

const els = {
  list: document.getElementById("cart-list"),
  empty: document.getElementById("cart-empty"),
  totalRow: document.getElementById("cart-total-row"),
  totalAmount: document.getElementById("cart-total-amount"),
  checkoutButton: document.getElementById("checkout-button"),
  checkoutError: document.getElementById("checkout-error"),
};

async function loadProductsById() {
  const response = await fetch("/products?limit=1000");
  if (!response.ok) throw new Error("Couldn't load the product catalog.");
  const data = await response.json();
  const byId = new Map();
  for (const product of data.products) byId.set(product.id, product);
  return byId;
}

function cartRowHTML(item, product) {
  if (!product) {
    return `
      <div class="cart-row">
        <div class="cart-row-info"><strong>This product is no longer available</strong></div>
        <button class="icon-btn" data-remove="${escapeHtml(item.productId)}" type="button" aria-label="Remove">✕</button>
      </div>`;
  }
  const unitPrice = formatPrice(product.price, product.currency);
  const lineTotal =
    product.price !== null && product.price !== undefined
      ? formatPrice(product.price * item.quantity, product.currency)
      : "—";
  return `
    <div class="cart-row">
      <img class="cart-row-image" src="${escapeHtml(product.image_url)}" alt=""
        onerror="this.style.display='none'" />
      <div class="cart-row-info">
        <strong>${escapeHtml(product.title)}</strong>
        <span class="cart-row-price">${escapeHtml(unitPrice || "No price set")}</span>
      </div>
      <div class="cart-row-qty">
        <button type="button" data-qty-down="${escapeHtml(item.productId)}" aria-label="Decrease quantity">−</button>
        <span aria-label="Quantity">${item.quantity}</span>
        <button type="button" data-qty-up="${escapeHtml(item.productId)}" aria-label="Increase quantity">+</button>
      </div>
      <div class="cart-row-total">${escapeHtml(lineTotal)}</div>
      <button class="icon-btn" data-remove="${escapeHtml(item.productId)}" type="button" aria-label="Remove">✕</button>
    </div>`;
}

function computeTotal(cart, products) {
  let total = 0;
  let currency = null;
  let ok = cart.length > 0;
  for (const item of cart) {
    const product = products.get(item.productId);
    if (!product || product.price === null || product.price === undefined || !product.currency) {
      ok = false;
      continue;
    }
    if (currency && currency !== product.currency) ok = false;
    currency = currency || product.currency;
    total += product.price * item.quantity;
  }
  return { total, currency, ok };
}

async function render() {
  let products;
  try {
    products = await loadProductsById();
  } catch (err) {
    els.list.innerHTML = `<p class="status-line is-error">${escapeHtml(err.message)}</p>`;
    return;
  }

  const cart = getCart();
  if (cart.length === 0) {
    els.list.innerHTML = "";
    els.empty.hidden = false;
    els.totalRow.hidden = true;
    els.checkoutButton.disabled = true;
    return;
  }
  els.empty.hidden = true;
  els.list.innerHTML = cart.map((item) => cartRowHTML(item, products.get(item.productId))).join("");

  els.list.querySelectorAll("[data-qty-up]").forEach((btn) =>
    btn.addEventListener("click", () => {
      const id = btn.dataset.qtyUp;
      const item = getCart().find((i) => i.productId === id);
      setCartItemQuantity(id, (item ? item.quantity : 0) + 1);
      render();
    })
  );
  els.list.querySelectorAll("[data-qty-down]").forEach((btn) =>
    btn.addEventListener("click", () => {
      const id = btn.dataset.qtyDown;
      const item = getCart().find((i) => i.productId === id);
      setCartItemQuantity(id, (item ? item.quantity : 1) - 1);
      render();
    })
  );
  els.list.querySelectorAll("[data-remove]").forEach((btn) =>
    btn.addEventListener("click", () => {
      removeFromCart(btn.dataset.remove);
      render();
    })
  );

  const { total, currency, ok } = computeTotal(cart, products);
  els.totalRow.hidden = false;
  els.totalAmount.textContent = ok && currency ? formatPrice(total, currency) : "Unavailable";
  els.checkoutButton.disabled = !ok;
}

async function startCheckout() {
  els.checkoutError.hidden = true;
  const user = await fetchCurrentUser();
  if (!user) {
    window.location.assign("/account.html?next=cart");
    return;
  }

  els.checkoutButton.disabled = true;
  els.checkoutButton.textContent = "Redirecting to Stripe…";
  try {
    const items = getCart().map((item) => ({ product_id: item.productId, quantity: item.quantity }));
    const response = await fetch("/payments/checkout-session", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ items }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data.checkout_url) {
      throw new Error(data.detail || `Server responded with HTTP ${response.status}`);
    }
    clearCart();
    window.location.assign(data.checkout_url);
  } catch (err) {
    els.checkoutError.textContent = `Couldn't start checkout: ${err.message}`;
    els.checkoutError.hidden = false;
    els.checkoutButton.disabled = false;
    els.checkoutButton.textContent = "Proceed to checkout";
  }
}

function init() {
  initNav("cart");
  render();
  els.checkoutButton.addEventListener("click", startCheckout);
}

document.addEventListener("DOMContentLoaded", init);
