"use strict";

const els = { detail: document.getElementById("order-detail") };

const STATUS_LABEL = { pending: "Pending", paid: "Paid", failed: "Failed" };

function itemRowHTML(item) {
  return `
    <div class="cart-row">
      <div class="cart-row-info">
        <strong>${escapeHtml(item.title)}</strong>
        <span class="cart-row-price">${escapeHtml(formatMinorAmount(item.unit_amount, itemCurrency))} each × ${item.quantity}</span>
      </div>
      <div class="cart-row-total">${escapeHtml(formatMinorAmount(item.amount_subtotal, itemCurrency))}</div>
    </div>`;
}

let itemCurrency = "USD";

function renderOrder(order) {
  itemCurrency = order.currency;
  els.detail.innerHTML = `
    <div class="auth-card">
      <div class="modal-section-label">Order ${escapeHtml(order.order_id)}</div>
      <div class="kv-grid">
        <div class="kv-item">
          <div class="kv-label">Placed</div>
          <div class="kv-value">${escapeHtml(formatDate(order.created_at))}</div>
        </div>
        <div class="kv-item">
          <div class="kv-label">Status</div>
          <div class="kv-value">
            <span class="badge order-status-${escapeHtml(order.status)}">${escapeHtml(STATUS_LABEL[order.status] || order.status)}</span>
          </div>
        </div>
        <div class="kv-item">
          <div class="kv-label">Total</div>
          <div class="kv-value">${escapeHtml(formatMinorAmount(order.amount_total, order.currency))}</div>
        </div>
      </div>
      ${order.failure_reason ? `<p class="buy-error" role="alert">${escapeHtml(order.failure_reason)}</p>` : ""}
      <div class="modal-section-label">Items</div>
      <div class="cart-list">${order.items.map(itemRowHTML).join("")}</div>
    </div>`;
}

async function init() {
  initNav("orders");

  const orderId = new URLSearchParams(window.location.search).get("order_id");
  if (!orderId) {
    els.detail.innerHTML = `<p class="status-line is-error">No order id given.</p>`;
    return;
  }

  const user = await fetchCurrentUser();
  if (!user) {
    els.detail.innerHTML = `<p class="status-line is-error"><a href="/account.html?next=orders">Sign in</a> to view this order.</p>`;
    return;
  }

  const response = await fetch(`/payments/orders/${encodeURIComponent(orderId)}`);
  if (!response.ok) {
    els.detail.innerHTML = `<p class="status-line is-error">Order not found.</p>`;
    return;
  }
  renderOrder(await response.json());
}

document.addEventListener("DOMContentLoaded", init);
