"""Vietnamese research report and a standalone equity figure from saved artifacts."""

import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd


def generate(output):
    output = Path(output)
    summary = json.loads((output / "summary.json").read_text())
    provenance = json.loads((output / "provenance.json").read_text())
    config = provenance["experiment"]
    periods = list(config["periods"])
    base_cost = 5 if 5 in config["slippage_bps"] else config["slippage_bps"][0]
    cost_label = f"{base_cost:g}bps"
    strategy = config["strategy"]
    strategy_label = f"SMA {strategy['fast_window']}/{strategy['slow_window']}"
    symbol = config["symbol"]
    capital = config["initial_cash"]
    fig, axes = plt.subplots(len(periods), 1, figsize=(10, 4 * len(periods)), squeeze=False)
    for period, ax in zip(periods, axes[:, 0]):
        for strategy_name, color, label in (("sma", "#176b87", strategy_label),
                                       ("buy-hold", "#ad5b27", "Buy and hold")):
            curve = pd.read_csv(output / f"{period}/{cost_label}/{strategy_name}/equity.csv", index_col="date", parse_dates=True)
            ax.plot(curve.index, curve["equity"], color=color, label=label, linewidth=1.5)
        ax.set_title(f"{period.title()} | {symbol} | {base_cost:g} bps per fill", loc="left")
        ax.set_ylabel("Portfolio value (USD)")
        ax.grid(alpha=0.18)
        ax.legend(frameon=False)
    fig.suptitle("Fixed rules, separate evaluation windows", fontsize=15)
    fig.tight_layout()
    fig.savefig(output / "equity.png", dpi=160)
    plt.close(fig)
    lines = [
        f"# {config['title']}", "",
        f"**Giả thuyết:** {config['hypothesis']}", "",
        f"Engine: `{provenance['engine']}`. Dữ liệu: {provenance['data']['rows']:,} phiên từ "
        f"{provenance['data']['start']} đến {provenance['data']['end']}.", "",
        "## Cách đọc", "",
        f"SMA là trung bình giá đóng cửa của một số phiên gần nhất. Sau mỗi phiên, nếu SMA "
        f"{strategy['fast_window']} cao hơn SMA {strategy['slow_window']} thì mục tiêu là nắm giữ "
        f"{symbol}; nếu thấp hơn hoặc bằng thì giữ tiền mặt. "
        "Lệnh thực hiện tại giá mở cửa phiên kế tiếp, cộng/trừ trượt giá. Không dịch tín hiệu hai lần.", "",
        f"Mỗi giai đoạn bắt đầu lại với {capital:,.2f} USD; dữ liệu trước giai đoạn chỉ dùng tính chỉ báo. "
        "Các giai đoạn được đánh giá riêng. Quy tắc được đóng băng trong cấu hình của lần chạy; "
        "không có tối ưu tham số tự động.", "",
        "## Kết quả", "",
        "Lợi nhuận là tổng cả giai đoạn. CAGR quy đổi theo 252 phiên/năm. Drawdown là mức giảm "
        "từ đỉnh vốn xuống đáy tiếp theo, bao gồm vốn ban đầu. Một vòng giao dịch gồm một lần mua và bán.", "",
        "| Giai đoạn | Trượt giá/lệnh | Phương án | Lợi nhuận | CAGR | Drawdown | Vòng giao dịch | Thời gian có vị thế | Giữ TB (phiên) |",
        "|---|---:|---|---:|---:|---:|---:|---:|---:|",
    ]
    for key, row in summary.items():
        period, cost, strategy = key.split("/")
        lines.append(f"| {period} | {cost} | {strategy} | {row['total_return']:.2%} | "
                     f"{row['cagr_252']:.2%} | {row['max_drawdown']:.2%} | {row['round_trips']} | "
                     f"{row['time_in_market']:.1%} | {row['mean_holding_sessions']:.1f} |")
    lines.extend(["", "![Đường vốn của hai phương án](equity.png)", "", "## Điều kết quả cho thấy", ""])
    for period in periods:
        sma = summary[f"{period}/{cost_label}/sma"]
        hold = summary[f"{period}/{cost_label}/buy-hold"]
        difference = (sma["total_return"] - hold["total_return"]) * 100
        lines.append(f"- **{period}, {base_cost:g} bps/lệnh:** {strategy_label} kết thúc với {sma['final_equity']:,.2f} USD; "
                     f"mua–nắm giữ với {hold['final_equity']:,.2f} USD. Chênh lệch lợi nhuận: "
                     f"{difference:+.2f} điểm phần trăm. Drawdown lần lượt {sma['max_drawdown']:.2%} "
                     f"và {hold['max_drawdown']:.2%}.")
    lines.extend([
        "", "Đây là kết quả trên một tài sản và hai giai đoạn đã biết; chưa chứng minh lợi thế bền vững. "
        "Nếu thay đổi quy tắc sau khi xem 2022–2025 thì giai đoạn đó không còn là dữ liệu chưa nhìn thấy.",
        "", "## Giả định và giới hạn", "",
        "- Giá OHLC được nhân với `Adj Close / Close`. Đây là đơn vị giá tổng lợi nhuận tổng hợp, "
        "có phản ánh điều chỉnh chia tách/cổ tức; không phải báo giá USD thực tế từng ngày. "
        "Cổ tức không được cộng tiền thêm lần nữa. So sánh dùng cùng cách xử lý cho cả hai phương án.",
        "- Vị thế dùng tối đa vốn khả dụng, đơn vị mô phỏng làm tròn theo engine tới 0,01. "
        "Không bán khống, không vay, không cân lại hằng ngày. Tiền mặt không sinh lãi.",
        "- Commission giả định 0; trượt giá 0/5/10 bps mỗi chiều, với 5 bps = 0,05%. "
        "Chưa mô hình hóa thuế, spread riêng, thanh khoản hay hạn chế tài khoản broker.",
        "- Cuối mỗi giai đoạn thanh lý bắt buộc ở giá đóng cửa có trượt giá cho cả hai phương án. "
        "Đây là quy ước chốt sổ, không phải tín hiệu chiến lược.",
        "- Dữ liệu điều chỉnh lấy tại thời điểm tải, có thể được nhà cung cấp sửa về sau. "
        "Giữ snapshot và checksum để tái lập; nghiên cứu này chưa phải mô phỏng dữ liệu point-in-time hoàn chỉnh.",
        "- Báo cáo này so với một lần chạy buy-and-hold riêng. Cột benchmark/alpha tự sinh bên trong "
        "`artifacts/` của Vibe-Trading không được dùng làm đối chứng chính vì không cùng mô hình khớp lệnh/chi phí.",
        "", "## Kiểm chứng và bằng chứng", "",
        "Mỗi ca có `audit.json`: đối chiếu tất cả lệnh với tín hiệu phiên trước, giá mở cửa và "
        "trượt giá; tính lại tiền mặt, số lượng và vốn cuối từng phiên độc lập với engine. "
        "`fills-exact.csv` lưu giá chưa làm tròn để kiểm tra tính toán. `config.json` lưu giả định của ca đó.", "",
        "Bảng dưới lấy ba vòng đầu của mỗi giai đoạn ở mức chi phí chính. "
        "Giá là giá điều chỉnh; P&L = số lượng × (giá bán − giá mua), commission bằng 0.", "",
        "| Giai đoạn | Ngày mua | Giá mua | Ngày bán | Giá bán | Số lượng | P&L USD |",
        "|---|---|---:|---|---:|---:|---:|",
    ])
    for period in periods:
        fills = pd.read_csv(output / f"{period}/{cost_label}/sma/fills-exact.csv")
        for i in range(0, min(len(fills) - 1, 6), 2):
            buy, sell = fills.iloc[i], fills.iloc[i + 1]
            pnl = buy.signed_quantity * (sell.execution_price - buy.execution_price)
            lines.append(f"| {period} | {buy.timestamp} | {buy.execution_price:.6f} | {sell.timestamp} | "
                         f"{sell.execution_price:.6f} | {buy.signed_quantity:.2f} | {pnl:.2f} |")
    lines.extend(["", "Kiểm tra tái lập bằng lệnh `compare` trong README. "
                  "`provenance.json` lưu nguồn, thời điểm tải, checksum dữ liệu, mã nguồn và lockfile.", ""])
    (output / "report.md").write_text("\n".join(lines), encoding="utf-8")
