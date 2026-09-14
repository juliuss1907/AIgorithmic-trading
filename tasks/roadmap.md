# Roadmap tổng thể: Hệ thống nghiên cứu và giao dịch thuật toán

Ngày chốt: 2026-09-14. Nền hiện có: pilot SPY SMA 20/50 đã chạy,
được kiểm toán số dư độc lập và tái lập trên Vibe-Trading 0.1.15.

## Đích đến

Xây một hệ thống cá nhân cho chứng khoán Mỹ, đưa một ý tưởng qua vòng đời:

```text
Giả thuyết → quy tắc → dữ liệu có phiên bản → backtest → kiểm định
          → so sánh → giải thích AI → paper trading → cân nhắc giao dịch thật
```

Web app tiếng Việt là giao diện chính. Người dùng không cần sửa code để chạy các chiến lược
được hỗ trợ. CLI và code vẫn là đường kiểm toán và tái lập.

Hệ thống không hứa tìm được chiến lược có lợi nhuận. Kết quả loại bỏ một giả thuyết cũng hợp lệ.
Mỗi giai đoạn chỉ được nâng cấp khi qua cổng kiểm chứng của giai đoạn đó.

## Quyết định nền tảng

- Chứng khoán/ETF Mỹ, dữ liệu ngày, long hoặc cash, không đòn bẩy trong các release đầu.
- Tiếp tục dùng Vibe-Trading qua adapter đã kiểm chứng; không fork toàn bộ repo.
- Python 3.12, FastAPI, Jinja2, JavaScript nhỏ, SQLite và artifact trên filesystem.
- SQLite giữ metadata/trạng thái/ghi chú; snapshot và kết quả là tệp bất biến có checksum.
- Web process và worker process riêng; worker chạy tuần tự để cô lập engine và artifact.
- AI v1 chỉ giải thích bằng chứng; sau đó mới chuyển mô tả thành cấu hình mẫu được kiểm soát.
- ML chỉ bắt đầu sau khi rule-based, dữ liệu đa tài sản và walk-forward hoạt động đúng.
- Chạy local, một người dùng. Cloud, multi-user và broker thật không thuộc v1.

## Giai đoạn 0 — Nền kiểm chứng đã hoàn thành

SPY SMA 20/50, hai kỳ 2015–2021 và 2022–2025, buy-and-hold, ba mức trượt giá,
audit lệnh/số dư và replay không mạng. Kết quả này là bộ hồi quy vàng khi refactor.

## Giai đoạn 1 — Research Workbench v1

Người dùng mở web app, xem pilot, tạo giả thuyết SMA mới, chọn SPY/QQQ, thời gian,
vốn và chi phí; xác nhận quy tắc; chạy nền; xem trạng thái, đường vốn, drawdown,
giao dịch và đối chứng; ghi chú và mở lại sau restart.

Mỗi run đóng băng cấu hình, snapshot, engine, code hash và lineage. Run cũ không bị sửa.
So sánh chỉ xếp hạng hai run khi cùng snapshot, kỳ, vốn và chi phí.

**Cổng G1:** người mới hoàn thành tạo → chạy → đọc một giao dịch → ghi chú mà không dùng terminal;
pilot không đổi số liệu; audit, replay và restart đều đạt.

## Giai đoạn 2 — Rule Strategy Lab

Ba gia đình chiến lược trả lời ba giả thuyết riêng:

| Gia đình | Chỉ báo | Quy tắc mặc định |
|---|---|---|
| Trend following | SMA 20/50; filter close/SMA 200 tùy chọn | Long khi SMA20 > SMA50 và filter cho phép; còn lại cash |
| Mean reversion | RSI 14; Bollinger 20 phiên, 2 độ lệch chuẩn | Vào khi close dưới band dưới và RSI < 30; thoát khi về band giữa hoặc RSI > 50 |
| Breakout | Donchian 20/10; ATR 14 | Vào khi close phá đỉnh 20 phiên trước; thoát khi thủng đáy 10 phiên trước |

Feature phân tích chung: return 1/5/20/60 phiên, realized volatility 20, ATR/close,
relative volume 20, drawdown 20/60, SMA20/SMA50 và close/SMA200.
Mọi giá trị chỉ dùng dữ liệu có sẵn cuối phiên; lệnh sớm nhất ở mở cửa phiên sau.

Không tự tìm bộ tham số tốt nhất. Người dùng nhân bản một giả thuyết và ghi lý do trước khi đổi.
Evaluation đã xem được gắn nhãn `observed` và không thể trở lại thành holdout.

**Cổng G2:** ba strategy có golden test công thức, test không nhìn tương lai, audit lệnh và báo cáo
nhất quán trên SPY/QQQ. Không gọi chiến lược “tốt” chỉ vì một backtest thắng.

## Giai đoạn 3 — Universe ETF và walk-forward

Universe nghiên cứu cố định: SPY, QQQ, IWM, DIA, TLT, GLD, XLF, XLK, XLE, XLV.
Mỗi ETF có snapshot, ngày bắt đầu thực tế và checksum riêng; không điền dữ liệu trước niêm yết.

Walk-forward dùng các fold thời gian liên tiếp; mỗi fold chỉ dùng quá khứ để chuẩn bị tham số/model
và đánh giá trên đoạn kế tiếp. Báo cáo hiện dispersion theo tài sản/fold, turnover, exposure,
tác động 0/5/10 bps và buy-and-hold tương ứng.

**Cổng G3:** mọi kết quả truy ngược được về snapshot/run; walk-forward không shuffle thời gian,
không dùng observation tương lai và tái lập được khi chặn mạng.

## Giai đoạn 4 — Machine Learning Lab

ML là một loại strategy mới; không thay engine hoặc audit.

- Feature set đầu tiên được cố định từ bộ chỉ báo G2.
- Nhãn: forward return 20 phiên lớn hơn round-trip cost 10 bps.
- Baseline: always-long, ba rule strategy và Logistic Regression.
- Challenger: HistGradientBoostingClassifier.
- LightGBM chỉ được thêm nếu challenger hiện tại thiếu chất lượng hoặc tốc độ theo phép đo cụ thể.
- Vị thế long khi xác suất vượt threshold chọn chỉ từ train fold; ngược lại cash.
- Imputation, scaling, model và threshold đều fit riêng trong từng train fold.
- Đánh giá cả log loss, Brier/calibration và metric trading sau chi phí.

Không dùng random split, feature expansion hàng trăm biến, LSTM, Transformer hoặc reinforcement
learning ở giai đoạn này. Không chọn model chỉ theo Sharpe cao nhất.

**Cổng G4:** challenger phải tốt hơn Logistic Regression và rule baseline trên đa số fold theo metric
dự báo, không làm xấu đáng kể drawdown/turnover sau chi phí, và ổn định khi đổi nhẹ threshold/chi phí.
Không qua cổng thì giữ ML ở research-only.

## Giai đoạn 5 — AI Research Copilot

Hai khả năng được mở tuần tự:

1. **Giải thích:** AI chỉ nhận evidence packet gồm cấu hình, metric, audit, indicator tại tín hiệu
   và giao dịch liên quan. Mọi con số có tham chiếu tới artifact.
2. **Idea-to-template:** AI chuyển mô tả thành typed schema của SMA, RSI/Bollinger hoặc Donchian/ATR.
   Người dùng xem quy tắc chuẩn hóa trước khi tạo run; schema validation quyết định điều được chạy.

AI không nhận quyền thực thi Python hoặc thay đổi run đã đóng băng. Chưa cấu hình AI vẫn dùng được
toàn bộ workbench. Tin tức/sentiment và nhiều agent là nhánh sau: mỗi event phải có nguồn,
`published_at`, `available_at` và snapshot; agent output chỉ là feature có phiên bản.

**Cổng G5:** lời giải thích đúng run và không bịa số liệu trong bộ câu hỏi biết trước;
provider lỗi không làm mất backtest; idea-to-template không thể thoát khỏi allowlist schema.

## Giai đoạn 6 — Paper Trading

Paper trading dùng cùng Strategy interface, execution assumptions và audit, chạy theo lịch sau phiên:

```text
Cập nhật snapshot → tính signal → order intent → risk gate
                  → fill giả lập → ledger → reconciliation
```

Risk gate: allowlist tài sản, long-only, không đòn bẩy, cap notional/vị thế/ngày,
giá stale thì từ chối, idempotency key, kill switch, audit log và không retry lệnh mù.
Run nghiên cứu và paper account tách riêng.

**Cổng G6:** shadow ít nhất 8 tuần thị trường; không có duplicate/missing order không giải thích;
tiền mặt/vị thế đối soát mỗi phiên; lỗi dữ liệu hoặc trạng thái dừng tạo lệnh mới.

## Giai đoạn 7 — Giao dịch thật cần quyết định riêng

Roadmap này không tự kích hoạt broker integration. Trước khi xây cần chọn broker, vốn,
mandate rủi ro và quy trình phê duyệt. Bản live đầu yêu cầu người dùng xác nhận order intent.
Chỉ cân nhắc tự động hóa sau khi G6 đạt và có runbook/rollback riêng.

## Kiến trúc đích

```text
Web UI tiếng Việt
  ├── Library / Builder / Compare / Notes
  ├── Indicator & trade inspector
  └── AI explanation panel
             ↓
FastAPI application
  ├── Contract + validation
  ├── Experiment/run service
  ├── Dataset catalog
  └── Evidence builder / AI adapter
             ↓
SQLite metadata + immutable files
  ├── DatasetSnapshot
  ├── Experiment + Run + lineage
  ├── ResearchNote + AIExplanation
  └── ModelRun + PaperAccount ở giai đoạn sau
             ↓
Sequential worker
  ├── Rule strategy / ML strategy
  ├── Vibe-Trading adapter
  ├── Independent audit
  └── Report/artifact writer
```

API nội bộ: `/api/datasets`, `/api/experiments`, `/api/runs`,
`/api/runs/{id}/results`, `/api/runs/{id}/notes`, `/api/runs/{id}/explanations`.
ML và paper trading chỉ thêm namespace sau khi qua cổng trước đó.

## Hợp đồng dữ liệu cốt lõi

- **DatasetSnapshot:** ID, symbol, calendar, source, price semantics, range, retrieval time,
  row count, checksums, status và lỗi.
- **Experiment:** title, hypothesis, strategy family/version, typed parameters, dataset ID,
  learning/evaluation windows, capital/costs, holdout state và parent ID.
- **Run:** frozen experiment, engine/code/dependency versions, trạng thái, artifact manifest,
  audit status và timestamps.
- **RunResult:** metrics, equity reference, trades/fills, indicator evidence, benchmark và warnings.
- **ModelRun:** feature/label hashes, folds, preprocessing/model/threshold, predictions và metrics.
- **AIExplanation:** run ID, question, evidence references, answer, provider/model/usage và timestamp.
- **PaperAccount:** mandate, cash/positions, signal/order/fill ledger, reconciliation và halt state.

Server đổi ID thành path nội bộ; API không nhận filesystem path hoặc mã Python từ browser.
Secret chỉ đọc phía server và không nằm trong run artifact.

## Tiêu chuẩn nghiên cứu xuyên suốt

- Tín hiệu cuối phiên chỉ khớp từ phiên kế tiếp; mỗi strategy có truncated-history test.
- Snapshot chỉ `ready` sau validation/checksum; run không tự tải dữ liệu mới.
- Data, feature, label, model, threshold, chi phí và code đều có version/hash.
- Evaluation đã xem không được tái sử dụng như dữ liệu chưa nhìn thấy.
- Mỗi strategy có đối chứng trên cùng dữ liệu và giả định.
- Báo cáo tách metric tính bằng code khỏi diễn giải AI.
- Không kết luận từ một mã, fold hoặc metric; hiển thị thất bại và dispersion.
- Không paper/live khi audit lỗi, data stale, artifact thiếu hoặc model chưa qua cổng.

## Các release

| Release | Kết quả bàn giao |
|---|---|
| 0.1 | Workbench web cho SMA, SPY/QQQ, library, compare và notes |
| 0.2 | RSI/Bollinger, Donchian/ATR và indicator inspector |
| 0.3 | Universe 10 ETF và walk-forward evaluator |
| 0.4 | Logistic Regression, HistGradientBoosting; LightGBM có điều kiện |
| 0.5 | AI giải thích và idea-to-template |
| 0.6 | Paper trading với ledger, reconciliation và risk gate |
| 1.0 | Chỉ cân nhắc sau paper; broker/live có spec và phê duyệt riêng |

Kế hoạch chi tiết release 0.1 và task list nằm tại [plan.md](plan.md) và [todo.md](todo.md).
