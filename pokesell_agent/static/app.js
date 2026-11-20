/* PokeSell Ops Secure Frontend Controller
 * Strict XSS & Storage Compliance:
 * - Zero innerHTML / outerHTML / insertAdjacentHTML / document.write
 * - Zero alert() / confirm() / prompt()
 * - Zero localStorage / sessionStorage token persistence (uses HttpOnly cookie + in-memory CSRF)
 */

(function () {
  "use strict";

  let csrfToken = "";
  let pendingHitlAction = null;

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) {
      node.className = className;
    }
    if (text !== undefined && text !== null) {
      node.textContent = String(text);
    }
    return node;
  }

  async function apiFetch(path, options) {
    const opts = Object.assign({ credentials: "same-origin" }, options || {});
    opts.headers = Object.assign({}, opts.headers || {});
    if (opts.method && opts.method.toUpperCase() === "POST") {
      opts.headers["Content-Type"] = "application/json";
      opts.headers["X-CSRF-Token"] = csrfToken;
    }
    const resp = await fetch(path, opts);
    if (resp.status === 401) {
      await bootstrapSession();
      if (opts.method && opts.method.toUpperCase() === "POST") {
        opts.headers["X-CSRF-Token"] = csrfToken;
      }
      return fetch(path, opts);
    }
    return resp;
  }

  async function bootstrapSession() {
    const resp = await fetch("/api/v1/session/bootstrap", { credentials: "same-origin" });
    const data = await resp.json();
    csrfToken = data.csrf_token || "";
  }

  function formatUsd(val) {
    const num = Number(val || 0);
    return "$" + num.toFixed(2);
  }

  function renderKpis(insights) {
    document.getElementById("kpi-units").textContent = String(insights.total_units_sold || 0);
    document.getElementById("kpi-tx").textContent =
      String(insights.total_transactions || 0) + " recorded stream sales";
    document.getElementById("kpi-gross").textContent = formatUsd(insights.gross_revenue_usd);
    document.getElementById("kpi-payout").textContent =
      "Net Payout: " + formatUsd(insights.net_payout_after_fees_usd);
    document.getElementById("kpi-profit").textContent = formatUsd(insights.estimated_net_profit_usd);
    document.getElementById("kpi-fees").textContent =
      "Whatnot/eBay Fees: " + formatUsd(insights.total_platform_fees_usd);
    document.getElementById("kpi-bags").textContent =
      String(insights.open_pull_bags_count || 0) + " Open Bag(s)";
    document.getElementById("kpi-vips").textContent =
      String(insights.repeat_buyers_count || 0) + " repeat VIP collector(s)";
  }

  function renderPullBags(bags) {
    const container = document.getElementById("pull-bags-container");
    container.replaceChildren();

    if (!bags || bags.length === 0) {
      container.appendChild(
        el("p", "panel-hint", "No open Pull-Bags on the packing desk yet. Log a sale to open BAG-002+!")
      );
      return;
    }

    bags.forEach(function (bag) {
      const card = el("article", "bag-card");
      const header = el("div", "bag-header");
      header.appendChild(el("span", "bag-id", bag.bag_number + " — " + bag.buyer_handle));

      let badgeClass = "badge badge-blue";
      if (bag.packaging_tier === "INSURED_SIGNATURE_BOX") {
        badgeClass = "badge badge-red";
      } else if (bag.packaging_tier === "SLAB_RIGID_BOX") {
        badgeClass = "badge badge-yellow";
      } else if (bag.packaging_tier === "BUBBLE_MAILER_BMWT") {
        badgeClass = "badge badge-green";
      }
      header.appendChild(el("span", badgeClass, bag.packaging_tier));
      card.appendChild(header);

      card.appendChild(
        el(
          "div",
          "bag-supplies",
          "Bin: " +
            bag.shelf_bin_coordinate +
            " | Items: " +
            String(bag.total_items_in_bag) +
            " | Value: " +
            formatUsd(bag.cumulative_value_usd)
        )
      );

      const ul = el("ul", "bag-items-list");
      (bag.items_manifest || []).forEach(function (item) {
        let desc =
          item.quantity +
          "x " +
          item.item_title +
          " [" +
          item.selling_format +
          "] (Pull: " +
          item.pull_from_location +
          ")";
        if (item.rtyh_hit_card) {
          desc += " -> Hit: " + item.rtyh_hit_card;
        }
        ul.appendChild(el("li", "", desc));
      });
      card.appendChild(ul);

      card.appendChild(
        el(
          "div",
          "bag-supplies",
          "Supplies: " + (bag.protection_supplies_checklist || []).join(" • ")
        )
      );
      container.appendChild(card);
    });
  }

  function renderInventory(inventory) {
    const list = document.getElementById("inventory-list");
    const select = document.getElementById("sale-sku");
    list.replaceChildren();

    const currentSelection = select.value;
    select.replaceChildren();
    const defaultOpt = el("option", "", "-- Custom / Unlisted Stream Item --");
    defaultOpt.value = "";
    select.appendChild(defaultOpt);

    (inventory || []).forEach(function (inv) {
      const opt = el(
        "option",
        "",
        inv.sku + " — " + inv.item_title + " (" + inv.quantity_available + " left @ " + inv.storage_location + ")"
      );
      opt.value = inv.sku;
      opt.dataset.title = inv.item_title;
      opt.dataset.format = inv.selling_format;
      select.appendChild(opt);

      const row = el("div", "inv-item");
      const left = el("div", "");
      left.appendChild(el("strong", "", inv.sku + " — " + inv.item_title));
      left.appendChild(
        el("div", "bag-supplies", "Slot: " + inv.storage_location + " | Format: " + inv.selling_format)
      );
      const qtyBadge = el(
        "span",
        inv.quantity_available <= 2 ? "badge badge-red" : "badge badge-green",
        String(inv.quantity_available) + " in stock"
      );
      row.appendChild(left);
      row.appendChild(qtyBadge);
      list.appendChild(row);
    });
    select.value = currentSelection;
  }

  function renderSalesTable(sales) {
    const tbody = document.getElementById("sales-table-body");
    tbody.replaceChildren();

    (sales || []).forEach(function (sale) {
      const tr = el("tr", sale.is_voided ? "row-voided" : "");
      tr.appendChild(el("td", "", sale.sale_id));
      tr.appendChild(el("td", "", String(sale.platform).toUpperCase()));
      tr.appendChild(el("td", "", sale.buyer_handle));
      tr.appendChild(
        el("td", "", "[" + sale.selling_format + "] " + sale.item_title + " (x" + sale.quantity + ")")
      );
      tr.appendChild(el("td", "", sale.pull_from_location + " -> " + sale.bag_number));
      tr.appendChild(
        el("td", "", formatUsd(sale.hammer_price_usd) + " / " + formatUsd(sale.net_payout_usd))
      );

      const actionTd = el("td", "");
      if (!sale.is_voided) {
        const voidBtn = el("button", "btn btn-ghost btn-small", "Void (HITL)");
        voidBtn.type = "button";
        voidBtn.addEventListener("click", function () {
          openHitlModal({
            type: "refund",
            sale_id: sale.sale_id,
            token: "APPROVE-VOID-" + sale.sale_id,
            description:
              "Confirm buyer refund/void for " +
              sale.sale_id +
              " (" +
              sale.item_title +
              ", " +
              formatUsd(sale.hammer_price_usd) +
              ") and restock SKU inventory?",
          });
        });
        actionTd.appendChild(voidBtn);
      } else {
        actionTd.appendChild(el("span", "badge badge-red", "VOIDED"));
      }
      tr.appendChild(actionTd);
      tbody.appendChild(tr);
    });
  }

  function renderTelemetry(events, spans) {
    const feed = document.getElementById("telemetry-feed");
    feed.replaceChildren();

    (events || []).slice().reverse().forEach(function (ev) {
      const item = el("div", "telemetry-item");
      item.appendChild(
        el(
          "span",
          "",
          "[" + ev.event_phase + "] " + (ev.tool_name || ev.agent_name) + ": " + ev.message
        )
      );
      item.appendChild(el("span", "badge badge-blue", "trace:" + String(ev.trace_id).slice(0, 8)));
      feed.appendChild(item);
    });

    if ((!events || events.length === 0) && (!spans || spans.length === 0)) {
      feed.appendChild(el("p", "panel-hint", "Audit events and OpenTelemetry spans appear here live."));
    }
  }

  function openHitlModal(config) {
    pendingHitlAction = config;
    const dialog = document.getElementById("hitl-modal");
    document.getElementById("hitl-modal-body").textContent = config.description;
    document.getElementById("hitl-modal-token").textContent = "Required Token: " + config.token;
    if (typeof dialog.showModal === "function") {
      dialog.showModal();
    } else {
      dialog.setAttribute("open", "open");
    }
  }

  function closeHitlModal() {
    pendingHitlAction = null;
    const dialog = document.getElementById("hitl-modal");
    if (typeof dialog.close === "function") {
      dialog.close();
    } else {
      dialog.removeAttribute("open");
    }
  }

  async function refreshDashboard() {
    const streamId = document.getElementById("active-stream-input").value.trim();
    const resp = await apiFetch(
      "/api/v1/dashboard?stream_id=" + encodeURIComponent(streamId)
    );
    const data = await resp.json();
    if (data.status === "success") {
      renderKpis(data.insights || {});
      renderPullBags(data.open_pull_bags || []);
      renderInventory(data.inventory || []);
      renderSalesTable(data.recent_sales || []);
      renderTelemetry(data.recent_audit_events || [], data.recent_trace_spans || []);
    }
  }

  function setAgentBanner(text, isError) {
    const box = document.getElementById("agent-response-box");
    box.textContent = text;
    box.style.background = isError ? "#fce8e6" : "#e8f0fe";
    box.style.color = isError ? "#d93025" : "#174ea6";
  }

  document.addEventListener("DOMContentLoaded", async function () {
    await bootstrapSession();
    await refreshDashboard();

    document.getElementById("sale-sku").addEventListener("change", function (ev) {
      const selected = ev.target.selectedOptions[0];
      if (selected && selected.dataset.title) {
        document.getElementById("sale-title").value = selected.dataset.title;
        document.getElementById("sale-format").value = selected.dataset.format;
      }
    });

    document.getElementById("agent-chat-form").addEventListener("submit", async function (ev) {
      ev.preventDefault();
      const input = document.getElementById("agent-prompt-input");
      const streamId = document.getElementById("active-stream-input").value.trim() || "STREAM-LIVE-001";
      const promptText = input.value.trim();
      if (!promptText) {
        return;
      }
      setAgentBanner("Running Coordinator -> Parallel CRM/Inventory Scouts -> Strategist...", false);
      const resp = await apiFetch("/api/v1/chat", {
        method: "POST",
        body: JSON.stringify({ prompt: promptText, stream_id: streamId }),
      });
      const result = await resp.json();
      setAgentBanner(result.reply || result.message || "Completed.", result.status === "error" || result.status === "blocked_by_guardrail");
      input.value = "";
      await refreshDashboard();
    });

    document.getElementById("quick-sale-form").addEventListener("submit", async function (ev) {
      ev.preventDefault();
      const streamId = document.getElementById("active-stream-input").value.trim() || "STREAM-LIVE-001";
      const skuVal = document.getElementById("sale-sku").value.trim();
      const hitVal = document.getElementById("sale-hit-card").value.trim();
      const payload = {
        stream_id: streamId,
        platform: document.getElementById("sale-platform").value,
        selling_format: document.getElementById("sale-format").value,
        buyer_handle: document.getElementById("sale-buyer").value.trim(),
        hammer_price_usd: Number(document.getElementById("sale-price").value),
        item_title: document.getElementById("sale-title").value.trim(),
        quantity: Number(document.getElementById("sale-qty").value || 1),
        rtyh_packs_ripped: Number(document.getElementById("sale-rtyh-packs").value || 0),
        sku: skuVal || null,
        rtyh_hit_card: hitVal || null,
      };
      const resp = await apiFetch("/api/v1/sales", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      const res = await resp.json();
      if (res.status === "success") {
        setAgentBanner(
          "Recorded " +
            res.sale_id +
            " -> " +
            res.assigned_pull_bag_number +
            " (" +
            res.recommended_packaging_tier +
            ") | Buyer: " +
            res.buyer_handle +
            " [" +
            res.buyer_vip_tier +
            "] | Perk: " +
            res.vip_perk_recommendation,
          false
        );
      } else {
        setAgentBanner(
          "Guided Error (" + res.error_code + "): " + res.message + " — " + res.recovery_instructions,
          true
        );
      }
      await refreshDashboard();
    });

    document.getElementById("btn-finalize-manifest").addEventListener("click", function () {
      const streamId = document.getElementById("active-stream-input").value.trim() || "STREAM-LIVE-001";
      openHitlModal({
        type: "manifest",
        stream_id: streamId,
        token: "APPROVE-MANIFEST-" + streamId.toUpperCase(),
        description:
          "Lock all OPEN_ON_DESK Pull-Bags for stream '" +
          streamId +
          "' and mark READY_FOR_CARRIER_DISPATCH?",
      });
    });

    document.getElementById("hitl-btn-cancel").addEventListener("click", closeHitlModal);

    document.getElementById("hitl-btn-confirm").addEventListener("click", async function () {
      if (!pendingHitlAction) {
        return;
      }
      const action = pendingHitlAction;
      closeHitlModal();

      if (action.type === "refund") {
        const resp = await apiFetch("/api/v1/hitl/refund", {
          method: "POST",
          body: JSON.stringify({
            sale_id: action.sale_id,
            reason: "Approved by human streamer in Command Center HITL modal.",
            restock_inventory: true,
            human_approval_token: action.token,
          }),
        });
        const res = await resp.json();
        setAgentBanner(
          res.status === "success"
            ? "HITL Refund Confirmed: " + res.sale_id + " voided and restocked."
            : res.message || "Refund failed.",
          res.status !== "success"
        );
      } else if (action.type === "manifest") {
        const resp = await apiFetch("/api/v1/hitl/finalize-manifest", {
          method: "POST",
          body: JSON.stringify({
            stream_id: action.stream_id,
            human_approval_token: action.token,
          }),
        });
        const res = await resp.json();
        setAgentBanner(
          res.status === "success"
            ? "HITL Manifest Confirmed: " +
                res.manifest_id +
                " locked " +
                String(res.finalized_bags_count) +
                " Pull-Bag(s)!"
            : res.message || "No open bags to finalize.",
          res.status !== "success"
        );
      }
      await refreshDashboard();
    });

    document.getElementById("btn-refresh").addEventListener("click", refreshDashboard);

    document.getElementById("btn-logout").addEventListener("click", async function () {
      await apiFetch("/api/v1/session/logout", { method: "POST", body: "{}" });
      csrfToken = "";
      window.location.href = "/";
    });
  });
})();
