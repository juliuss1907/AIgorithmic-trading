"use strict";
(() => {
  const $ = id => document.getElementById(id);
  let info = null, preview = null, pending = false, generation = 0;
  const message = text => { $("perp-message").textContent = text; };
  const time = value => new Date(value).toLocaleString("vi-VN", {timeZone:"Asia/Ho_Chi_Minh", hour12:false});
  async function api(path, method = "GET", body = null, key = null) {
    const token = $("control-token").value.trim();
    if (!token) throw new Error("Nhập control token ở trên, không phải Binance API key.");
    const headers = {Authorization:"Bearer " + token};
    if (body) { headers["Content-Type"] = "application/json"; headers["Idempotency-Key"] = key; }
    const response = await fetch(path, {method, headers, body:body ? JSON.stringify(body) : null, credentials:"omit"});
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Yêu cầu không hợp lệ.");
    return data;
  }
  function render(data) {
    const target = $("perp-summary"); target.replaceChildren();
    for (const [label,value] of [["Cặp / thị trường", data.symbol + " · " + (data.market || "Perp")],
      ["Sàn / tài khoản", (data.venue || "bnb") + " / " + (data.environment || "demo") + " · " + data.account.split(":").at(-1).slice(0,6) + "…"],
      ["Trạng thái", data.status + (data.stale ? " · dữ liệu cũ" : "")], ["AIGT", data.aigt_state || "chưa xác minh"],
      ["Margin", data.margin_mode || "chưa xác minh"], ["Leverage sàn / cấu hình", data.actual_leverage === undefined ? "chưa xác minh" : data.actual_leverage + "x / " + data.configured_leverage + "x"],
      ["Lệnh mở", data.open_orders ?? "chưa xác minh"], ["Nhận lúc UTC+7", time(data.observed_at)]]) {
      const row = document.createElement("div"), dt = document.createElement("dt"), dd = document.createElement("dd");
      dt.textContent = label; dd.textContent = String(value); row.append(dt,dd); target.append(row);
    }
    const positions = $("perp-positions"); positions.replaceChildren();
    for (const position of data.positions || []) {
      const row = document.createElement("tr");
      for (const value of [position.side, position.quantity, position.entry_price, position.mark_price, position.unrealized_pnl,
        position.managed_by_aigt ? "AIGT" : "Ngoài AIGT / chưa quản lý"]) {
        const cell = document.createElement("td"); cell.textContent = String(value); row.append(cell);
      }
      positions.append(row);
    }
    if (!positions.children.length) {
      const row = document.createElement("tr"), cell = document.createElement("td"); cell.colSpan = 6;
      cell.textContent = data.status === "verified" ? "Không có vị thế." : "Chưa xác minh vị thế."; row.append(cell); positions.append(row);
    }
    $("perp-edit").disabled = pending || data.status !== "verified" || data.stale || !!data.change_blockers?.length;
    if (data.change_blockers?.length) message("Chặn đổi leverage: " + data.change_blockers.join("; "));
  }
  async function read() {
    const current = ++generation;
    info = null; $("perp-edit").disabled = true;
    message("Đang đọc projection từ settings controller…");
    try {
      const data = await api("/api/perp/" + encodeURIComponent($("perp-symbol").value.trim()));
      if (current !== generation) return;
      info = data; message(data.stale ? "Dữ liệu cũ; chờ controller cập nhật rồi đọc lại." : "Không kích hoạt giao dịch."); render(data);
    } catch (error) {
      if (current !== generation) return;
      $("perp-summary").replaceChildren(); $("perp-positions").replaceChildren(); message(error.message);
    }
  }
  $("perp-read-form").addEventListener("submit", event => { event.preventDefault(); read(); });
  $("perp-symbol").addEventListener("input", () => { generation++; info = null; $("perp-edit").disabled = true; });
  $("perp-edit").addEventListener("click", () => {
    if (!info || pending) return;
    preview = null; $("leverage-form").hidden = false; $("leverage-confirmation").hidden = true;
    $("leverage-value").value = info.configured_leverage;
    $("leverage-dialog").showModal(); $("leverage-value").focus();
  });
  $("leverage-form").addEventListener("submit", event => {
    event.preventDefault(); const target = Number($("leverage-value").value);
    if (!info || !Number.isInteger(target) || target < 1 || target > 10) return;
    preview = {account:info.account, symbol:info.symbol, target, previous:info.actual_leverage,
      revision:info.setting_revision, route_digest:info.route_digest, observed_at:info.observed_at};
    $("leverage-preview").textContent = info.symbol + " · Binance Demo · " + info.margin_mode + " · Leverage: " + info.actual_leverage + "x → " + target + "x";
    $("leverage-form").hidden = true; $("leverage-confirmation").hidden = false; $("leverage-cancel").focus();
  });
  for (const id of ["leverage-input-cancel", "leverage-cancel"]) $(id).addEventListener("click", () => $("leverage-dialog").close());
  $("leverage-dialog").addEventListener("close", () => { preview = null; $("perp-edit").focus(); });
  $("leverage-confirm").addEventListener("click", async () => {
    if (!preview || pending) return;
    const accepted = preview; pending = true; $("perp-edit").disabled = true; $("leverage-dialog").close();
    const key = Array.from(crypto.getRandomValues(new Uint8Array(16)), b => b.toString(16).padStart(2,"0")).join("");
    try {
      let request = await api("/api/perp/" + accepted.symbol + "/leverage-requests", "POST", accepted, key);
      message("Đã xác nhận; chờ controller áp dụng. Request: " + request.id);
      const deadline = Date.now()+65000;
      while (["queued","applying"].includes(request.status) && Date.now() < deadline) {
        await new Promise(resolve => setTimeout(resolve,1000));
        request = await api("/api/perp/" + accepted.symbol + "/leverage-requests/" + encodeURIComponent(request.id));
      }
      await read();
      message("Leverage request: " + request.status + (request.reason ? " · " + request.reason : "") + " · " + request.id + ". Không tự bật trading.");
    } catch (error) { message(error.message + " Nếu đã gửi Confirm, kiểm tra request trước khi thử lại. Mã xác nhận: perp:" + key); }
    finally { pending = false; if (info) render(info); }
  });
  document.addEventListener("aigt:market", event => {
    $("perp-controls").hidden = event.detail !== "perp";
    if (event.detail !== "perp") { generation++; info = null; $("perp-edit").disabled = true; if ($("leverage-dialog").open) $("leverage-dialog").close(); }
  });
})();
