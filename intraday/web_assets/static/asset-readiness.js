"use strict";
(() => {
  const target = document.getElementById("asset-catalog");
  const message = document.getElementById("readiness-message");
  const refreshButton = document.getElementById("readiness-refresh");
  let market = "spot", generation = 0, pending = null;
  const time = value => value ? new Date(value).toLocaleString("vi-VN", {timeZone: "Asia/Ho_Chi_Minh", hour12: false}) : "Chưa có";
  const labels = {
    history_days: "History (ngày)", history_coverage: "Coverage history",
    latest_candle_age: "Tuổi nến (giây)", elapsed_hours: "Thời gian (giờ)",
    heartbeat_coverage: "Coverage heartbeat", matured_setups: "Setup trưởng thành",
    matured_outcomes: "Outcome trưởng thành", after_cost_score: "Score sau phí",
    hard_risk_violations: "Vi phạm hard risk", outcome_coverage: "Coverage outcome",
    closed_trades: "Giao dịch đóng", net_return_pct: "Return sau chi phí", drawdown_pct: "Drawdown",
  };
  function element(tag, text) {
    const node = document.createElement(tag); node.textContent = text; return node;
  }
  function cell(row, text = "") {
    const node = element("td", text); row.append(node); return node;
  }
  function notice(text) {
    const row = document.createElement("tr"), node = cell(row, text);
    node.colSpan = 6; target.replaceChildren(row);
  }
  function number(value, unit) {
    if (value === null || value === undefined) return "N/A";
    const amount = unit === "fraction" ? value * 100 : value;
    return Number(amount).toLocaleString("vi-VN", {maximumFractionDigits: 2}) + (unit === "fraction" || unit === "percent" ? "%" : "");
  }
  function evidence(container, row) {
    const details = document.createElement("details");
    const summary = document.createElement("summary"); summary.textContent = "Xem bằng chứng";
    details.append(summary);
    details.append(element("p", "Gate: " + (row.gate_version || "v1")));
    if (row.cost_profile) {
      const profile = row.cost_profile, prefix = row.market;
      details.append(element("p", "Mỗi fill: phí sàn " + profile[prefix + "_fee_bps"] + " bps + trượt giá giả định " + profile[prefix + "_slippage_bps"] + " bps. Spread bid/ask và funding Perp riêng."));
      details.append(element("p", "Profile v" + profile.version + " · biểu phí đọc " + time(profile.fee_observed_at) + " · không phải phí thực thu account."));
    }
    details.append(element("p", "Champion: " + (row.champion_id || "chưa có")));
    details.append(element("p", "Candidate: " + (row.candidate_id || "chưa có") + " · " + (row.candidate_status || "—")));
    details.append(element("p", "Bắt đầu: " + time(row.started_at) + " · Cutoff replay: " + time(row.replay_cutoff)));
    if (row.legacy_champion) details.append(element("p", "Champion BTC kế thừa; không yêu cầu bootstrap lại. Không suy ra quyền trading."));
    for (const kind of ["replay", "soak"]) {
      const saved = row[kind];
      details.append(element("p", kind + " đã lưu: " + (saved ? saved.status + " · " + saved.evaluation_id + " · " + time(saved.evaluated_at) : "chưa có")));
      if (saved) {
        details.append(element("p", "Lý do đã lưu: " + (saved.reason_codes.join(" · ") || "không có blocker")));
        details.append(element("pre", JSON.stringify(saved.metrics, null, 2)));
        if (saved.run_id) {
          const link = element("a", "Xem account replay");
          link.href = "/replay/" + encodeURIComponent(saved.run_id); details.append(link);
        }
      }
    }
    if (row.replay_v1 || row.soak_v1) {
      details.append(element("p", "Bằng chứng v1 giữ nguyên · replay: " + (row.replay_v1?.status || "chưa có") + " · soak: " + (row.soak_v1?.status || "chưa có")));
    }
    if (row.preview) {
      details.append(element("p", "Preview chưa lưu: " + row.preview.status + " · không có evaluation ID"));
      details.append(element("pre", JSON.stringify(row.preview.metrics, null, 2)));
    }
    container.append(details);
  }
  function render(report) {
    target.replaceChildren();
    if (!report.rows.length) { notice("Không có coin/scope khớp thị trường này."); return; }
    for (const item of report.rows) {
      const row = document.createElement("tr"); row.dataset.market = item.market;
      const coin = cell(row, item.symbol + " · " + (item.market === "spot" ? "Spot" : "Perp"));
      evidence(coin, item);
      cell(row, item.phase_label + " · " + item.lifecycle);
      const gates = cell(row);
      if (!item.gates.length) gates.textContent = "Xem rule/evaluation trong bằng chứng.";
      for (const gate of item.gates) {
        gates.append(element("p", (labels[gate.key] || gate.key) + ": " + number(gate.value, gate.unit) + " " + gate.comparison + " " + number(gate.required, gate.unit) + " · " + (gate.status === "pass" ? "đạt" : gate.status === "unknown" ? "chưa có dữ liệu" : "chưa đạt")));
      }
      const next = cell(row, item.next_action_label);
      for (const blocker of [...item.blockers, ...item.venue_blockers]) next.append(element("p", blocker));
      cell(row, time(item.earliest_evaluation_at));
      cell(row, item.venue ? item.venue.venue + " / " + item.venue.environment + " · " + item.venue.instrument : "Chưa chọn sàn");
      target.append(row);
    }
  }
  async function refresh() {
    pending?.abort(); pending = new AbortController();
    const current = ++generation, selected = market;
    refreshButton.disabled = true; target.setAttribute("aria-busy", "true");
    message.textContent = "Đang đọc readiness " + selected + "…"; notice("Đang đọc source…");
    try {
      const response = await fetch("/api/assets/readiness?market=" + selected, {credentials: "omit", signal: pending.signal});
      if (!response.ok) throw new Error("Không đọc được readiness (HTTP " + response.status + "). Kiểm tra database v23 và thử lại.");
      const report = await response.json();
      if (current !== generation) return;
      render(report); message.textContent = "Readiness " + selected + " · cập nhật " + time(report.generated_at) + " UTC+7 · preview đọc source, chưa cấp quyền trading.";
    } catch (error) {
      if (current !== generation || error.name === "AbortError") return;
      notice("Readiness chưa tải được."); message.textContent = error.message;
    } finally {
      if (current === generation) { refreshButton.disabled = false; target.removeAttribute("aria-busy"); }
    }
  }
  document.addEventListener("aigt:market", event => { market = event.detail; generation++; pending?.abort(); });
  document.addEventListener("aigt:catalog-refresh", refresh);
  refreshButton.addEventListener("click", refresh);
})();
