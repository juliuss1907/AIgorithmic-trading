"use strict";
(() => {
  const $ = id => document.getElementById(id);
  let market = "spot", generation = 0, busy = false;
  const number = value => value === null || value === undefined ? "N/A" : Number(value).toLocaleString("en-US", {maximumFractionDigits: 4});
  const time = value => value ? new Date(value).toLocaleString("vi-VN", {timeZone: "Asia/Ho_Chi_Minh", hour12: false}) : "N/A";
  const message = value => { $("asset-message").textContent = value; };
  const requestKey = () => crypto.randomUUID ? crypto.randomUUID() : Array.from(crypto.getRandomValues(new Uint8Array(16)), b => b.toString(16).padStart(2, "0")).join("");
  function cell(row, text) {
    const td = document.createElement("td"); td.textContent = text; row.append(td); return td;
  }
  async function api(path, method = "GET", body = null, key = null) {
    const headers = {};
    if (method !== "GET") {
      const token = $("control-token").value.trim();
      if (!token) throw new Error("Nhập control token để thực hiện thao tác ghi.");
      headers.Authorization = "Bearer " + token;
      headers["Idempotency-Key"] = key || requestKey();
      headers["Content-Type"] = "application/json";
    }
    const response = await fetch(path, {method, headers, body: body ? JSON.stringify(body) : null, credentials: "omit"});
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Yêu cầu không hợp lệ hoặc chưa được cấp quyền.");
    return data;
  }
  async function catalog() {
    const selectedMarket = market, data = await api("/api/assets?market=" + market);
    if (selectedMarket !== market) return;
    const target = $("asset-catalog"); target.replaceChildren();
    for (const asset of data.assets) {
      const row = document.createElement("tr");
      const scope = market === "spot" ? "spot_4h" : "perp_intraday";
      cell(row, asset.symbol); cell(row, asset.stages[scope] || "chưa đăng ký");
      const route = asset.execution_routes.find(r => r.market === market);
      cell(row, route ? route.venue + " / " + route.environment : "chưa chọn"); target.append(row);
    }
  }
  function render(report) {
    const target = $("venue-results"); target.replaceChildren();
    for (const venue of report.venues) {
      const row = document.createElement("tr");
      cell(row, venue.venue + " / " + venue.environment);
      cell(row, (venue.instrument || "—") + " · " + venue.status);
      cell(row, number(venue.volume_24h_quote) + " " + (venue.quote_asset || ""));
      cell(row, number(venue.spread_bps));
      const depth = side => [5, 10, 25].map(bps => number(venue.depth_quote?.[side]?.[String(bps)])).join(" · ");
      cell(row, depth("bid") + " / " + depth("ask") + " " + (venue.quote_asset || ""));
      cell(row, number(venue.buy_slippage_bps) + " / " + number(venue.sell_slippage_bps));
      cell(row, time(venue.received_at));
      const action = cell(row, venue.reason || "");
      if (venue.selectable) {
        const button = document.createElement("button"); button.type = "button"; button.textContent = "Chọn Binance Demo";
        button.addEventListener("click", async () => {
          if (!confirm("Chọn Binance Demo cho " + report.symbol + " " + report.market + "? Thao tác này chưa kích hoạt trade.")) return;
          button.disabled = true;
          try {
            await api("/api/assets/" + report.symbol + "/venues/" + report.market, "PUT", {venue: "bnb", environment: "demo", scan_id: report.id});
            message("Đã chọn Binance Demo. Chưa kích hoạt giao dịch; hãy kiểm tra rule, soak và allocation."); await catalog();
          } catch (error) { message(error.message); button.disabled = false; }
        }); action.replaceChildren(button);
      }
      if (venue.warnings?.length) { const note = document.createElement("small"); note.textContent = venue.warnings.join(" · "); action.append(note); }
      target.append(row);
    }
  }
  async function scan(add) {
    if (busy) return;
    busy = true; const current = ++generation, scope = market;
    $("asset-form").setAttribute("aria-busy", "true");
    try {
      const body = {symbol: $("asset-symbol").value.trim().toUpperCase(), market: scope};
      if (add) await api("/api/assets", "POST", body);
      message("Đang quét Demo/Testnet… tối đa khoảng 10 giây.");
      let report = await api("/api/asset-scans", "POST", {...body, notional: Number($("asset-notional").value)});
      const started = Date.now();
      while (report.status === "running" && Date.now() - started < 22000 && current === generation) {
        await new Promise(resolve => setTimeout(resolve, 500));
        report = await api("/api/asset-scans/" + report.id);
      }
      if (current !== generation) return;
      if (report.status !== "completed") throw new Error("Quét chưa hoàn tất. Coin vẫn được giữ; hãy quét lại.");
      render(report); message("Quét xong. Kiểm tra volume và thanh khoản rồi chọn sàn; không tự bật trade."); await catalog();
    } catch (error) { if (current === generation) message(error.message); }
    finally { busy = false; $("asset-form").removeAttribute("aria-busy"); }
  }
  document.querySelectorAll("[data-market]").forEach(button => button.addEventListener("click", () => {
    if (market === button.dataset.market) return;
    market = button.dataset.market; generation++;
    document.querySelectorAll("[data-market]").forEach(b => b.setAttribute("aria-pressed", String(b === button)));
    $("venue-results").replaceChildren(); message("Đã chuyển nhóm. Quét lại để chọn venue riêng cho " + market + ".");
    catalog().catch(error => message(error.message));
  }));
  $("asset-form").addEventListener("submit", event => { event.preventDefault(); scan(true); });
  $("asset-rescan").addEventListener("click", () => { if ($("asset-form").reportValidity()) scan(false); });
  catalog().catch(error => message(error.message));
})();
