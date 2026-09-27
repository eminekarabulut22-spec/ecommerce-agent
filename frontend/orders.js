"use strict";

const els = {
  signedOut: document.getElementById("orders-signed-out"),
  empty: document.getElementById("orders-empty"),
  list: document.getElementById("orders-list"),
};

const STATUS_LABEL = { pending: "Pending", paid: "Paid", failed: "Failed" };

function orderRowHTML(order) {
  const amount = formatMinorAmount(order.amount_total, order.currency);
  return `
    <a class="order-row" href="/order.html?order_id=${encodeURIComponent(order.order_id)}">
      <span class="order-row-id">${escapeHtml(order.order_id.slice(0, 8))}…</span>
      <span class="order-row-date">${escapeHtml(formatDate(order.created_at))}</span>
      <span class="order-row-amount">${escapeHtml(amount)}</span>
      <span class="badge order-status-${escapeHtml(order.status)}">${escapeHtml(STATUS_LABEL[order.status] || order.status)}</span>
    </a>`;
}

async function init() {
  initNav("orders");

  const user = await fetchCurrentUser();
  if (!user) {
    els.signedOut.hidden = false;
    return;
  }

  const response = await fetch("/payments/orders");
  if (!response.ok) {
    els.list.innerHTML = `<p class="status-line is-error">Couldn't load orders (HTTP ${response.status}).</p>`;
    return;
  }
  const data = await response.json();
  if (data.orders.length === 0) {
    els.empty.hidden = false;
    return;
  }
  els.list.innerHTML = data.orders.map(orderRowHTML).join("");
}

document.addEventListener("DOMContentLoaded", init);
