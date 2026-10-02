(() => {
  "use strict";
  const target = document.getElementById("confidence-proposals");
  if (!target) return;
  const message = document.getElementById("confidence-message");
  const percent = value => typeof value === "number" && Number.isFinite(value) ? (value * 100).toLocaleString("vi-VN", {maximumFractionDigits: 4}) + "%" : "—";
  const number = value => typeof value === "number" && Number.isFinite(value) ? value.toLocaleString("vi-VN", {maximumFractionDigits: 3}) : "—";
  const time = value => value && !Number.isNaN(Date.parse(value)) ? new Intl.DateTimeFormat("vi-VN", {dateStyle: "short", timeStyle: "medium", timeZone: "Asia/Ho_Chi_Minh"}).format(new Date(value)) : "—";
  const labels = {running: "Đang review / cần kiểm tra nếu bị gián đoạn", pending_review: "Chờ operator duyệt", deferred: "Chưa đủ bằng chứng", reject: "Không đạt", error: "Review lỗi", dismissed: "Operator đã bỏ qua"};
  function cell(row, text) {
    const element = document.createElement("td"); element.textContent = text; row.append(element); return element;
  }
  let loading = false;
  async function refresh() {
    if (loading) return;
    loading = true; target.setAttribute("aria-busy", "true");
    try {
      const response = await fetch("/api/replay/confidence", {cache: "no-store"});
      if (!response.ok) throw new Error("Không thể đọc proposal; nguồn hoặc report chưa sẵn sàng.");
      const report = await response.json(); target.replaceChildren();
      for (const item of report.items) {
        const row = document.createElement("tr"), review = item.proposal;
        cell(row, item.symbol + " · " + (item.current_rule_status || "chưa có source rule"));
        cell(row, percent(item.current_threshold) + " → " + percent(review?.proposed_threshold));
        cell(row, (review ? (labels[review.status] || review.status) : "Chưa có review") + (review?.blockers?.length ? " · " + review.blockers.join(" · ") : ""));
        const evidence = cell(row, (review?.model_ref || "Chưa gọi LLM") + " · " + (review?.proposal?.rationale || "Đợi review đủ bằng chứng."));
        if (review?.window) {
          const note = document.createElement("p"); note.textContent = "Selection: " + time(review.window.start) + " → " + time(review.window.split) + "; holdout → " + time(review.window.end); evidence.append(note);
        }
        const summary = review?.holdout?.summary;
        cell(row, summary ? "Return " + number(summary.net_return_pct) + "% · DD " + number(summary.max_drawdown_known_pct) + "% · " + number(summary.closed_trades) + " trades · fee " + number(summary.exchange_fee_known) + " / slip " + number(summary.slippage_cost_known) + " / funding " + number(summary.funding_paid_known) + " USDT" : "Chưa có replay holdout");
        const links = cell(row, time(review?.created_at));
        for (const key of ["training_baseline", "training_candidate", "holdout_baseline", "holdout"]) {
          const id = review?.[key]?.run_id;
          if (typeof id !== "string" || !/^[a-f0-9]{32}$/.test(id)) continue;
          const link = document.createElement("a"); link.href = "/replay/" + id; link.textContent = " · " + key; links.append(link);
        }
        target.append(row);
      }
      if (!report.items.length) { const row = document.createElement("tr"); const empty = cell(row, "Chưa có coin hoặc proposal."); empty.colSpan = 6; target.append(row); }
      message.textContent = "Read-only · không tự áp dụng confidence hoặc kích hoạt trading.";
    } catch (error) { message.textContent = error.message; }
    finally { loading = false; target.removeAttribute("aria-busy"); }
  }
  document.getElementById("confidence-refresh").addEventListener("click", refresh);
  document.addEventListener("aigt:market", event => { document.getElementById("confidence-section").hidden = event.detail !== "perp"; });
  refresh();
})();
