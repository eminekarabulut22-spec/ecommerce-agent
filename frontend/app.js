"use strict";

const state = {
  products: [],
  search: "",
  statusFilter: "all",
  sourceFilter: "all",
};

const els = {
  grid: document.getElementById("grid"),
  stats: document.getElementById("stats"),
  statusLine: document.getElementById("status-line"),
  searchInput: document.getElementById("search-input"),
  modalOverlay: document.getElementById("modal-overlay"),
  modalBody: document.getElementById("modal-body"),
  modalClose: document.getElementById("modal-close"),
  themeToggle: document.getElementById("theme-toggle"),
  themeToggleIcon: document.getElementById("theme-toggle-icon"),
  checkoutBanner: document.getElementById("checkout-banner"),
};

function escapeHtml(value) {
  const div = document.createElement("div");
  div.textContent = value ?? "";
  return div.innerHTML;
}

function confidenceLevel(score) {
  if (score >= 0.8) return "high";
  if (score >= 0.5) return "medium";
  return "low";
}

function formatPrice(price, currency) {
  if (price === null || price === undefined) return null;
  try {
    return new Intl.NumberFormat(undefined, {
      style: "currency",
      currency: currency || "USD",
      maximumFractionDigits: 2,
    }).format(price);
  } catch {
    return `${price}${currency ? " " + currency : ""}`;
  }
}

function priceBlockHTML(price, currency) {
  const formatted = formatPrice(price, currency);
  if (!formatted) {
    return `<span class="card-price is-missing">Price not available</span>`;
  }
  return `
    <span class="price-block">
      <span class="card-price">${escapeHtml(formatted)}</span>
      <span class="price-source">Business price</span>
    </span>`;
}

function isExtractionConfidenceField(field) {
  return field !== "price" && field !== "currency";
}

function formatDate(iso) {
  try {
    return new Date(iso).toLocaleString(undefined, {
      dateStyle: "medium",
      timeStyle: "short",
    });
  } catch {
    return iso;
  }
}

function averageConfidence(scores) {
  const values = Object.entries(scores || {})
    .filter(([field]) => isExtractionConfidenceField(field))
    .map(([, score]) => score);
  if (values.length === 0) return null;
  return values.reduce((a, b) => a + b, 0) / values.length;
}

// --- Data loading -----------------------------------------------------------------

async function loadProducts() {
  els.statusLine.textContent = "Loading products…";
  els.statusLine.classList.remove("is-error");
  try {
    const response = await fetch("/products?limit=500");
    if (!response.ok) {
      throw new Error(`Server responded with HTTP ${response.status}`);
    }
    const data = await response.json();
    state.products = data.products || [];
    els.statusLine.textContent = "";
    renderStats();
    applyFilters();
  } catch (err) {
    els.statusLine.textContent =
      `Couldn't load products (${err.message}). Is the API running? ` +
      `Start it with: uvicorn ecommerce_agent.api.main:app --reload`;
    els.statusLine.classList.add("is-error");
    els.grid.innerHTML = "";
  }
}

// --- Stats --------------------------------------------------------------------------

function renderStats() {
  const total = state.products.length;
  const valid = state.products.filter((p) => p.validation_status === "valid").length;
  const needsReview = total - valid;
  const demo = state.products.filter((p) => p.is_demo).length;
  const agentExtracted = total - demo;

  const cards = [
    { label: "Total products", value: total },
    { label: "Valid", value: valid },
    { label: "Needs review", value: needsReview },
    { label: "Agent-extracted", value: agentExtracted },
    { label: "Demo data", value: demo },
  ];

  els.stats.innerHTML = cards
    .map(
      (c) => `
      <div class="stat-card">
        <div class="stat-value">${c.value}</div>
        <div class="stat-label">${escapeHtml(c.label)}</div>
      </div>`
    )
    .join("");
}

// --- Filtering ------------------------------------------------------------------------

function applyFilters() {
  const q = state.search.trim().toLowerCase();

  const filtered = state.products.filter((p) => {
    if (state.statusFilter !== "all" && p.validation_status !== state.statusFilter) {
      return false;
    }
    if (state.sourceFilter === "demo" && !p.is_demo) return false;
    if (state.sourceFilter === "agent" && p.is_demo) return false;

    if (q) {
      const haystack = [p.title, p.brand, p.manufacturer, p.category]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      if (!haystack.includes(q)) return false;
    }
    return true;
  });

  renderGrid(filtered);
}

// --- Grid / cards ------------------------------------------------------------------------

function productCardHTML(product) {
  const avgConfidence = averageConfidence(product.confidence_scores);
  const statusBadge =
    product.validation_status === "valid"
      ? '<span class="badge badge-valid">✓ Valid</span>'
      : '<span class="badge badge-review">⚠ Needs review</span>';
  const sourceBadge = product.is_demo
    ? '<span class="badge badge-demo">🧪 Demo</span>'
    : '<span class="badge badge-agent">🤖 Agent</span>';

  const subtitle = [product.brand, product.manufacturer].filter(Boolean).join(" · ");

  return `
    <article class="card" tabindex="0" role="button" data-id="${escapeHtml(product.id)}"
      aria-label="View details for ${escapeHtml(product.title)}">
      <div class="card-image-wrap">
        <img src="${escapeHtml(product.image_url)}" alt="${escapeHtml(product.title)}" loading="lazy"
          onerror="this.replaceWith(Object.assign(document.createElement('div'), {className:'no-image', textContent:'No image available'}))" />
        <div class="badge-row">
          ${sourceBadge}
          ${statusBadge}
        </div>
      </div>
      <div class="card-body">
        ${product.category ? `<div class="card-category">${escapeHtml(product.category)}</div>` : ""}
        <div class="card-title">${escapeHtml(product.title)}</div>
        ${subtitle ? `<div class="card-brand">${escapeHtml(subtitle)}</div>` : ""}
        <div class="card-footer">
          ${priceBlockHTML(product.price, product.currency)}
          ${
            avgConfidence !== null
              ? `<span class="confidence-pill">
                   <span class="confidence-dot ${confidenceLevel(avgConfidence)}"></span>
                   ${Math.round(avgConfidence * 100)}%
                 </span>`
              : ""
          }
        </div>
      </div>
    </article>`;
}

function renderGrid(products) {
  if (products.length === 0) {
    const hasAny = state.products.length > 0;
    els.grid.innerHTML = `
      <div class="empty-state">
        <h3>${hasAny ? "No products match your filters" : "No products yet"}</h3>
        <p>${
          hasAny
            ? "Try clearing the search box or resetting the filters above."
            : "Seed some demo data or run the agent to populate the catalog:<br/><br/>" +
              "<code>python scripts/seed_demo_products.py</code><br/><br/>" +
              "<code>python scripts/batch_process_images.py</code>"
        }</p>
      </div>`;
    return;
  }

  els.grid.innerHTML = products.map(productCardHTML).join("");

  els.grid.querySelectorAll(".card").forEach((card) => {
    const openThisCard = () => {
      const product = state.products.find((p) => p.id === card.dataset.id);
      if (product) openModal(product);
    };
    card.addEventListener("click", openThisCard);
    card.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        openThisCard();
      }
    });
  });
}

// --- Modal ------------------------------------------------------------------------------

function confidenceRowHTML(field, score) {
  return `
    <div class="confidence-row">
      <span class="field-name">${escapeHtml(field)}</span>
      <span class="confidence-track">
        <span class="confidence-fill ${confidenceLevel(score)}" style="width:${Math.round(score * 100)}%"></span>
      </span>
      <span>${Math.round(score * 100)}%</span>
    </div>`;
}

function openModal(product) {
  const formattedPrice = formatPrice(product.price, product.currency);
  const priceDisplay = formattedPrice
    ? `${formattedPrice} (business-provided)`
    : null;
  const statusBadge =
    product.validation_status === "valid"
      ? '<span class="badge badge-valid">✓ Valid</span>'
      : '<span class="badge badge-review">⚠ Needs review</span>';
  const sourceBadge = product.is_demo
    ? '<span class="badge badge-demo">🧪 Demo data</span>'
    : '<span class="badge badge-agent">🤖 Agent-extracted</span>';

  const kvItems = [
    ["Category", product.category],
    ["Brand", product.brand],
    ["Manufacturer", product.manufacturer],
    ["Price", priceDisplay],
    ["Extraction model", product.extraction_model || "—"],
    ["Created", formatDate(product.created_at)],
  ].filter(([, value]) => value);

  const confidenceEntries = Object.entries(product.confidence_scores || {})
    .filter(([field]) => isExtractionConfidenceField(field))
    .sort((a, b) => a[1] - b[1]);

  els.modalBody.innerHTML = `
    <img class="modal-image" src="${escapeHtml(product.image_url)}" alt="${escapeHtml(product.title)}"
      onerror="this.style.display='none'" />
    <div class="modal-content">
      <div class="modal-title-row">
        <h2>${escapeHtml(product.title)}</h2>
        <div style="display:flex; gap:6px; flex-wrap:wrap;">${sourceBadge}${statusBadge}</div>
      </div>

      ${product.description ? `<p class="modal-description">${escapeHtml(product.description)}</p>` : ""}

      <div>
        <div class="modal-section-label">Details</div>
        <div class="kv-grid">
          ${kvItems
            .map(
              ([label, value]) => `
              <div class="kv-item">
                <div class="kv-label">${escapeHtml(label)}</div>
                <div class="kv-value">${escapeHtml(value)}</div>
              </div>`
            )
            .join("")}
        </div>
      </div>

      ${buySectionHTML(product)}

      ${
        product.tags && product.tags.length
          ? `<div>
               <div class="modal-section-label">Tags</div>
               <div class="tag-list">
                 ${product.tags.map((t) => `<span class="tag">${escapeHtml(t)}</span>`).join("")}
               </div>
             </div>`
          : ""
      }

      ${
        confidenceEntries.length
          ? `<div>
               <div class="modal-section-label">Extraction confidence</div>
               <div class="confidence-list">
                 ${confidenceEntries.map(([field, score]) => confidenceRowHTML(field, score)).join("")}
               </div>
             </div>`
          : ""
      }

      ${
        product.validation_errors && product.validation_errors.length
          ? `<div class="validation-errors">
               <strong>Why this needs review:</strong>
               <ul>${product.validation_errors.map((e) => `<li>${escapeHtml(e)}</li>`).join("")}</ul>
             </div>`
          : ""
      }
    </div>`;

  const addToCartButton = els.modalBody.querySelector("#add-to-cart-button");
  if (addToCartButton) {
    addToCartButton.addEventListener("click", () => addProductToCart(product, addToCartButton));
  }

  els.modalOverlay.hidden = false;
  document.body.style.overflow = "hidden";
}

function closeModal() {
  els.modalOverlay.hidden = true;
  document.body.style.overflow = "";
}

// --- Cart (test payments happen from the Cart page; see cart.js) --------------------------
//
// This button only ever adds a product id/quantity to the browser's local cart. Pricing,
// order creation, and the Stripe Checkout Session are all handled server-side once the
// shopper proceeds to checkout from the cart.

function purchaseBlocker(product) {
  if (product.price === null || product.price === undefined || !product.currency) {
    return "This product has no price, so it can't be bought.";
  }
  if (product.validation_status !== "valid") {
    return "This product still needs review, so it can't be bought yet.";
  }
  return null;
}

function buySectionHTML(product) {
  const blocker = purchaseBlocker(product);
  return `
    <div class="buy-section">
      <div class="modal-section-label">Test payment</div>
      <button id="add-to-cart-button" class="btn-primary" type="button" ${blocker ? "disabled" : ""}>
        Add to cart
      </button>
      <p class="buy-note">
        ${escapeHtml(blocker || "Stripe test mode — no real money is charged. Use card 4242 4242 4242 4242.")}
      </p>
      <p id="buy-error" class="buy-error" role="alert" hidden></p>
    </div>`;
}

function addProductToCart(product, button) {
  addToCart(product.id, 1);
  button.textContent = "Added ✓";
  button.disabled = true;
  setTimeout(() => {
    button.textContent = "Add to cart";
    button.disabled = false;
  }, 1200);
}

function formatMinorAmount(amount, currency) {
  try {
    const formatter = new Intl.NumberFormat(undefined, { style: "currency", currency });
    const digits = formatter.resolvedOptions().maximumFractionDigits;
    return formatter.format(amount / 10 ** digits);
  } catch {
    return `${amount} ${currency}`;
  }
}

function showCheckoutBanner(kind, message) {
  els.checkoutBanner.className = `checkout-banner is-${kind}`;
  els.checkoutBanner.textContent = message;
  els.checkoutBanner.hidden = false;
}

async function fetchOrder(orderId) {
  const response = await fetch(`/payments/orders/${encodeURIComponent(orderId)}`);
  if (!response.ok) throw new Error(`Server responded with HTTP ${response.status}`);
  return response.json();
}

async function handleCheckoutReturn() {
  const params = new URLSearchParams(window.location.search);
  const outcome = params.get("checkout");
  const orderId = params.get("order_id");
  if (!outcome || !orderId) return;

  // Drop the query string so a reload doesn't re-run this.
  window.history.replaceState(null, "", window.location.pathname);

  if (outcome === "cancel") {
    showCheckoutBanner("neutral", "Checkout cancelled — you were not charged.");
    return;
  }

  showCheckoutBanner("neutral", "Confirming your test payment…");
  // The webhook usually lands within a second or two of the redirect; poll briefly for it.
  for (let attempt = 0; attempt < 10; attempt++) {
    try {
      const order = await fetchOrder(orderId);
      const amount = formatMinorAmount(order.amount_total, order.currency);
      if (order.status === "paid") {
        showCheckoutBanner("success", `Test payment succeeded — ${amount} (order ${order.order_id}).`);
        return;
      }
      if (order.status === "failed") {
        showCheckoutBanner("error", `Test payment failed: ${order.failure_reason || "unknown reason"}`);
        return;
      }
    } catch (err) {
      showCheckoutBanner("error", `Couldn't check the order status (${err.message}).`);
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 1500));
  }
  showCheckoutBanner(
    "warning",
    "Payment submitted, but the order is still pending — the Stripe webhook hasn't arrived yet. " +
      "Is `stripe listen` running?"
  );
}

// --- Theme --------------------------------------------------------------------------------

function applyTheme(theme) {
  if (theme === "dark" || theme === "light") {
    document.documentElement.setAttribute("data-theme", theme);
  } else {
    document.documentElement.removeAttribute("data-theme");
  }
  const isDark =
    theme === "dark" ||
    (theme !== "light" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  els.themeToggleIcon.textContent = isDark ? "☀️" : "🌙";
}

function initTheme() {
  const saved = localStorage.getItem("theme");
  applyTheme(saved);
  els.themeToggle.addEventListener("click", () => {
    const current = document.documentElement.getAttribute("data-theme");
    const isDark =
      current === "dark" ||
      (!current && window.matchMedia("(prefers-color-scheme: dark)").matches);
    const next = isDark ? "light" : "dark";
    localStorage.setItem("theme", next);
    applyTheme(next);
  });
}

// --- Wiring ---------------------------------------------------------------------------------

function init() {
  initTheme();
  initNav("products");

  els.searchInput.addEventListener("input", (e) => {
    state.search = e.target.value;
    applyFilters();
  });

  document.querySelectorAll(".chip[data-filter]").forEach((chip) => {
    chip.addEventListener("click", () => {
      const { filter, value } = chip.dataset;
      document
        .querySelectorAll(`.chip[data-filter="${filter}"]`)
        .forEach((c) => c.classList.toggle("is-active", c === chip));
      if (filter === "status") state.statusFilter = value;
      if (filter === "source") state.sourceFilter = value;
      applyFilters();
    });
  });

  els.modalClose.addEventListener("click", closeModal);
  els.modalOverlay.addEventListener("click", (e) => {
    if (e.target === els.modalOverlay) closeModal();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !els.modalOverlay.hidden) closeModal();
  });

  loadProducts();
  handleCheckoutReturn();
}

init();
