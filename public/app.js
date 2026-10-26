(() => {
  const state = {
    catalog: [],
    itemId: "leica-m6",
    weights: { buyer: 0.34, seller: 0.34, referee: 0.32 },
    result: null,
    activeRound: 0,
    liveTimer: null,
    paypalMode: "sandbox_simulation",
  };

  const $ = (id) => document.getElementById(id);

  document.addEventListener("DOMContentLoaded", async () => {
    bind();
    await loadBootstrap();
    await runOnce({ animate: false, reveal: 5 });
  });

  function bind() {
    $("item-select").addEventListener("change", async (e) => {
      state.itemId = e.target.value;
      syncSlidersFromItem();
      await runOnce({ animate: false, reveal: 5 });
    });
    $("btn-run-live").addEventListener("click", () => runOnce({ animate: true, reveal: 0 }));
    $("btn-step-round").addEventListener("click", stepRound);
    $("btn-view-cert").addEventListener("click", openCert);
    $("btn-open-cert-footer").addEventListener("click", openCert);
    $("btn-close-modal").addEventListener("click", closeCert);
    $("cert-modal-backdrop").addEventListener("click", (e) => {
      if (e.target.id === "cert-modal-backdrop") closeCert();
    });
    $("btn-download-json").addEventListener("click", downloadCert);
    $("btn-reset-weights").addEventListener("click", () => {
      syncSlidersFromItem();
    });
    ["buyer", "seller", "referee"].forEach((key) => {
      $(`slider-${key}`).addEventListener("input", () => {
        state.weights[key] = Number($(`slider-${key}`).value) / 100;
        $(`val-weight-${key}`).textContent = `${$(`slider-${key}`).value}%`;
      });
    });
    document.querySelectorAll(".step-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const n = Number(btn.dataset.step);
        if (!state.result) return;
        state.activeRound = n;
        renderRound(n);
      });
    });
  }

  async function loadBootstrap() {
    const [health, catalog] = await Promise.all([
      fetch("/api/health").then((r) => r.json()),
      fetch("/api/catalog").then((r) => r.json()),
    ]);
    state.catalog = catalog.items;
    state.paypalMode = health.paypal.mode;
    const pill = $("paypal-mode-pill");
    pill.textContent = health.paypal.modeLabel;
    pill.classList.toggle("mode-live", health.paypal.mode === "paypal_sandbox");
    pill.classList.toggle("mode-sim", health.paypal.mode !== "paypal_sandbox");

    $("item-select").innerHTML = catalog.items
      .map((item) => `<option value="${item.id}">${item.title}</option>`)
      .join("");
    $("item-select").value = state.itemId;
    syncSlidersFromItem();
  }

  function syncSlidersFromItem() {
    const item = state.catalog.find((i) => i.id === state.itemId);
    if (!item) return;
    state.weights = { ...item.defaultWeights };
    ["buyer", "seller", "referee"].forEach((key) => {
      const pct = Math.round(state.weights[key] * 100);
      $(`slider-${key}`).value = String(pct);
      $(`val-weight-${key}`).textContent = `${pct}%`;
    });
  }

  async function runOnce({ animate, reveal }) {
    if (state.liveTimer) {
      clearInterval(state.liveTimer);
      state.liveTimer = null;
    }
    $("btn-run-live").disabled = true;
    $("consensus-status-pill").textContent = "NEGOTIATING";
    $("consensus-status-pill").className = "status-pill live";
    $("accepted-banner").classList.add("hidden");

    const res = await fetch("/api/negotiate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        itemId: state.itemId,
        weights: state.weights,
        settle: true,
      }),
    });
    if (!res.ok) {
      $("btn-run-live").disabled = false;
      $("consensus-status-pill").textContent = "ERROR";
      $("consensus-status-pill").className = "status-pill veto";
      return;
    }
    state.result = await res.json();
    $("btn-run-live").disabled = false;
    paintListing(state.result.item);
    $("protocol-sku").textContent = state.result.item.sku;

    if (animate) {
      state.activeRound = 0;
      renderRound(0);
      let n = 0;
      state.liveTimer = setInterval(() => {
        n += 1;
        state.activeRound = n;
        renderRound(n);
        if (n >= 5) {
          clearInterval(state.liveTimer);
          state.liveTimer = null;
        }
      }, 1100);
    } else {
      state.activeRound = reveal || 5;
      renderRound(state.activeRound);
    }
  }

  function stepRound() {
    if (!state.result) return;
    if (state.liveTimer) {
      clearInterval(state.liveTimer);
      state.liveTimer = null;
    }
    state.activeRound = Math.min(5, (state.activeRound || 0) + 1);
    renderRound(state.activeRound);
  }

  function paintListing(item) {
    $("listing-sku").textContent = item.sku;
    $("listing-title").textContent = item.title;
    $("listing-blurb").textContent = item.blurb;
    $("listing-list").textContent = money(item.listPrice, item.currency);
    $("listing-floor").textContent = money(item.sellerReservation, item.currency);
    $("listing-ceil").textContent = money(item.buyerReservation, item.currency);
    $("rail-min").textContent = money(item.sellerReservation, item.currency);
    $("rail-max").textContent = money(item.listPrice, item.currency);
  }

  function renderRound(n) {
    const result = state.result;
    if (!result) return;
    const rounds = result.rounds;
    const visible = n <= 0 ? [] : rounds.filter((r) => r.round <= n);
    const current = n <= 0 ? null : rounds.find((r) => r.round === Math.min(n, 5));

    $("round-count-value").textContent = `${Math.max(0, n)} / 5`;
    $("active-round-indicator").textContent = `Round ${Math.max(0, n)} of 5`;
    document.querySelectorAll(".step-btn").forEach((btn) => {
      const step = Number(btn.dataset.step);
      btn.classList.toggle("active", step <= n);
      btn.classList.toggle("current", step === n);
    });

    const stream = $("protocol-stream");
    stream.innerHTML = visible
      .map((round) => {
        const bubbles = round.messages
          .map((msg) => {
            const side = msg.agentId === "BUYER" ? "buyer" : msg.agentId === "SELLER" ? "seller" : "referee";
            const who = side === "buyer" ? "Buyer" : side === "seller" ? "Seller" : "Referee";
            const metrics = (msg.metricsCited || [])
              .map((m) => `<span class="msg-metric">${esc(m.label)} <strong>${esc(m.value)}</strong></span>`)
              .join("");
            return `<div class="bubble-row ${side}">
              <article class="bubble bubble-${side} stance-${esc(msg.stance)}">
                <div class="bubble-who">${who} · ${esc(msg.stance)}</div>
                <div class="bubble-headline">${esc(msg.headline)}</div>
                <p class="bubble-body">${esc(msg.message)}</p>
                <div class="bubble-metrics">${metrics}</div>
              </article>
            </div>`;
          })
          .join("");
        return `<div class="round-pill">Round ${round.round} of 5</div>${bubbles}`;
      })
      .join("");
    stream.scrollTop = stream.scrollHeight;

    if (!current) {
      resetMeters();
      return;
    }

    const scores = current.scores;
    const split = scores.surplusSplit;
    $("jain-value").textContent = `Jain ${scores.jainFairnessIndex.toFixed(4)}`;
    setRing(scores.jainFairnessIndex);
    $("consensus-score-value").textContent = `${scores.consensusScore}`;
    $("nash-index-value").textContent = `${scores.nashBargainingIndex}`;
    $("surplus-total").textContent = money(scores.surplus, result.item.currency);
    $("bar-buyer-split").style.width = `${(split.buyer * 100).toFixed(1)}%`;
    $("bar-seller-split").style.width = `${(split.seller * 100).toFixed(1)}%`;
    $("split-buyer-pct").textContent = pct(split.buyer);
    $("split-seller-pct").textContent = pct(split.seller);

    setAgent("buyer", scores.buyerUtility, lastStance("BUYER", n), pct(split.buyer));
    setAgent("seller", scores.sellerUtility, lastStance("SELLER", n), pct(split.seller));
    const vetoCount = current.messages.filter((m) => m.stance === "VETO").length;
    setAgent("referee", scores.refereeUtility, lastStance("REFEREE", n), String(vetoCount));
    $("kpi-referee-vetoes").textContent = String(
      result.rounds.slice(0, n).reduce((acc, r) => acc + r.messages.filter((m) => m.stance === "VETO").length, 0)
    );

    $("strip-r1-jain").textContent = result.comparison.jainBaseline.toFixed(3);
    $("strip-r4-jain").textContent = n >= 4 ? result.comparison.jainConsensus.toFixed(3) : "pending";

    $("price-now").textContent = money(current.price, result.item.currency);
    $("term-ship").textContent = `${current.terms.shippingDays} days`;
    $("term-war").textContent = `${current.terms.warrantyMonths} mo`;
    $("term-ret").textContent = `${current.terms.returnDays} days`;
    placeMarkers(result.item, current.price);

    const pill = $("consensus-status-pill");
    if (n < 4) {
      pill.textContent = current.status.includes("VETO") ? "Vetoed" : "In play";
      pill.className = current.status.includes("VETO") ? "status-pill veto" : "status-pill live";
    } else if (n === 4) {
      pill.textContent = "Consensus";
      pill.className = "status-pill ok";
    } else {
      pill.textContent = "Settled";
      pill.className = "status-pill ok";
    }

    const cert = result.certificate;
    $("cert-id-display").textContent = n >= 5 ? cert.certificateId : "pending";
    $("cert-hash-display").textContent = n >= 5 ? cert.sha256Hash : "—";

    if (n >= 5 && result.settlement) {
      const s = result.settlement;
      $("accepted-banner").classList.remove("hidden");
      $("accepted-banner").classList.add("flash");
      $("accepted-price").textContent = money(result.finalDeal.price, result.item.currency);
      $("pp-order-id").textContent = s.orderId;
      $("pp-order-status").textContent = s.status;
      $("pp-capture-id").textContent = s.captureId || "—";
      $("pp-mode").textContent = s.mode;
      $("pp-note").textContent = s.note;
    } else {
      $("accepted-banner").classList.add("hidden");
    }
  }

  function resetMeters() {
    $("jain-value").textContent = "Jain —";
    setRing(0);
    ["buyer", "seller", "referee"].forEach((k) => setAgent(k, 0, "IDLE", "—"));
  }

  function setAgent(key, util, stance, extra) {
    $(`util-${key}`).textContent = util ? Number(util).toFixed(0) : "—";
    $(`bar-${key}`).style.width = `${Math.max(0, Math.min(100, Number(util) || 0))}%`;
    $(`kpi-${key}-stance`).textContent = stance;
    if (key !== "referee") $(`kpi-${key}-share`).textContent = extra;
    const card = $(`card-agent-${key}`);
    card.classList.toggle("active-veto", stance === "VETO");
    card.classList.toggle("active-ok", stance === "APPROVAL" || stance === "CERTIFIED");
  }

  function lastStance(agentId, upToRound) {
    let stance = "IDLE";
    for (const round of state.result.rounds) {
      if (round.round > upToRound) break;
      const msgs = round.messages.filter((m) => m.agentId === agentId);
      if (msgs.length) stance = msgs[msgs.length - 1].stance;
    }
    return stance;
  }

  function setRing(jain) {
    const bar = $("jain-bar-fill");
    if (bar) bar.style.width = `${Math.max(0, Math.min(1, Number(jain) || 0)) * 100}%`;
  }

  function placeMarkers(item, price) {
    const min = item.sellerReservation;
    const max = item.listPrice;
    const span = Math.max(1, max - min);
    const now = Math.max(0, Math.min(1, (price - min) / span));
    const rail = $("rail-glow");
    if (rail) rail.style.width = `${now * 100}%`;
  }

  function openCert() {
    const result = state.result;
    if (!result || state.activeRound < 5) {
      if (result) {
        state.activeRound = 5;
        renderRound(5);
      } else {
        return;
      }
    }
    const cert = result.certificate;
    const settle = result.settlement || {};
    const terms = result.finalDeal.terms || {};
    const lines = [
      ["Item", result.item.title],
      ["Price", money(cert.finalPrice, cert.currency)],
      ["Surplus", money(cert.surplusSplit.surplus, cert.currency)],
      ["Buyer share", pct(cert.surplusSplit.buyer)],
      ["Seller share", pct(cert.surplusSplit.seller)],
      ["Jain index", cert.jainFairnessIndex.toFixed(4)],
      ["Rounds", String(cert.roundCount)],
      ["Shipping", `${terms.shippingDays} days`],
      ["Warranty", `${terms.warrantyMonths} mo`],
      ["Returns", `${terms.returnDays} days`],
      ["PayPal order", settle.orderId || "—"],
      ["Capture", settle.captureId || "—"],
      ["Status", settle.status || "—"],
    ];
    $("modal-proof-summary").innerHTML =
      lines
        .map(
          ([k, v]) =>
            `<div class="receipt-line"><span>${esc(k)}</span><strong>${esc(v)}</strong></div>`
        )
        .join("") +
      `<div class="receipt-total"><span>AMOUNT</span><strong>${esc(money(cert.finalPrice, cert.currency))}</strong></div>`;
    const stamp = $("paid-stamp");
    if (stamp) stamp.textContent = settle.status === "COMPLETED" ? "PAID" : settle.status || "OPEN";
    const certId = $("receipt-cert-id");
    if (certId) certId.textContent = cert.certificateId;
    const hash = $("receipt-hash");
    if (hash) hash.textContent = cert.sha256Hash;
    $("modal-json-viewer").textContent = JSON.stringify(cert, null, 2);
    $("cert-modal-backdrop").classList.remove("hidden");
  }

  function closeCert() {
    $("cert-modal-backdrop").classList.add("hidden");
  }

  function downloadCert() {
    if (!state.result) return;
    const blob = new Blob([JSON.stringify(state.result.certificate, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${state.result.certificate.certificateId}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }

  function money(v, currency) {
    return `$${Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${currency || "USD"}`;
  }
  function pct(v) {
    return `${Math.round(Number(v) * 100)}%`;
  }
  function esc(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }
})();
