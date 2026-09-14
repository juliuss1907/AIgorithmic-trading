# Công việc tổng thể: hệ thống nghiên cứu và giao dịch thuật toán

Trạng thái: sẵn sàng triển khai theo thứ tự. Roadmap các release sau nằm tại [roadmap.md](roadmap.md).

## 1. Tách cấu hình thí nghiệm khỏi pilot — M

**Phụ thuộc:** không.
**Nội dung:** nhận cấu hình và đường dẫn rõ ràng, giữ nguyên adapter engine một tài sản;
báo cáo lấy tên/tham số/vốn/kỳ từ cấu hình. Chốt typed request/result dùng chung cho CLI và web.

- [x] CLI hiện tại vẫn chạy được; schema từ chối cấu hình không hỗ trợ trước khi tạo run.
- [x] SPY pilot có cùng ngày/giá/số lượng giao dịch và vốn trong dung sai serialization 1e-8 tương đối.
- [x] Thay tham số hoặc vốn làm nhãn và thuyết minh đổi đúng, không còn chữ cố định sai.

**Kiểm tra:** `uv run --frozen pytest -q`; chạy lại pilot, đối chiếu số liệu và audit.
**Vùng tệp:** `lab/experiment.py`, `lab/report.py`, module hợp đồng mới và tests tương ứng; tối đa 5 tệp chính.

## 2. Quản lý snapshot có phiên bản — M

**Phụ thuộc:** 1.
**Nội dung:** quản lý dataset bằng ID; đăng ký dữ liệu SPY hiện có mà không sửa nguồn.

- [x] Snapshot được nhận diện bằng metadata/checksum, không gắn vào một tên CSV cố định.
- [x] Đăng ký lại pilot không sinh bản trùng; snapshot thiếu dữ liệu hoặc checksum sai không dùng được.
- [x] Tải mới hoàn tất rồi mới công bố ready; replay chỉ đọc snapshot đã chọn.

**Kiểm tra:** test đăng ký lặp, lỗi mạng/CSV/checksum và replay không mạng.
**Vùng tệp:** `lab/data.py`, module lưu dataset, tests dữ liệu và hợp đồng; tối đa 4 tệp chính.

## Checkpoint A

- [x] Đạt hồi quy pilot và toàn bộ tests lõi.
- [x] Code/report hỗ trợ cấu hình rõ ràng mà không đổi quy ước tính toán.

## 3. Thư viện web đọc lại pilot — M

**Phụ thuộc:** 1–2.
**Nội dung:** FastAPI + Jinja2, SQLite metadata; trang thư viện và chi tiết kết quả bằng tiếng Việt.

- [x] Mở được pilot đã có, xem chỉ tiêu, đường vốn và danh sách giao dịch.
- [x] Ghi chú gắn đúng run và còn sau restart; phần số liệu/provenance không thể sửa từ trang này.
- [x] Bằng chứng có đường dẫn theo ID; không trả về tệp tùy ý ngoài artifact đã đăng ký.

**Kiểm tra:** API read/write notes, mở trang bằng browser, kiểm tra cả trường hợp thiếu artifact.
**Vùng tệp:** module web, store SQLite, template thư viện/chi tiết và tests; giới hạn 5 vùng/tệp chính.

## 4. Chạy một cấu hình qua worker — M

**Phụ thuộc:** 3.
**Nội dung:** job bền vững trong SQLite, worker riêng chạy lần lượt, endpoint tạo và xem trạng thái.

- [ ] Request tạo run trả ID ngay; job đóng băng cấu hình và snapshot trước khi chạy.
- [ ] completed chỉ sau audit/report; lỗi không tạo kết quả thành công giả.
- [ ] Restart ghi nhận interrupted, thử lại bằng run mới và không trộn output của hai lần chạy.

**Kiểm tra:** integration job thành công/thất bại/restart, gửi hai job, kiểm tra tách stdout/artifact.
**Vùng tệp:** worker, store, routes run, entrypoint và tests; tối đa 5 tệp chính.

## 5. Form tạo thí nghiệm từ đầu đến kết quả — M

**Phụ thuộc:** 4.
**Nội dung:** form giả thuyết, SMA nhanh/chậm, kỳ/vốn; màn hình xem lại quy tắc trước khi bấm chạy.

- [ ] Người dùng tạo và chạy từ trình duyệt mà không sửa JSON hoặc dùng terminal.
- [ ] Lỗi tham số/thời gian/warmup hiển thị tại trường liên quan; run lưu đúng nội dung đã xác nhận.
- [ ] Trang trạng thái tự cập nhật và dẫn tới kết quả; nhấn gửi lặp không vô tình tạo hai job.

**Kiểm tra:** browser flow hợp lệ/sai dữ liệu/refresh, request deduplication và hợp đồng UI/API.
**Vùng tệp:** form template, JS/CSS, routes và tests; tối đa 5 tệp chính.

## Checkpoint B

- [ ] Từ web hoàn thành một thí nghiệm SPY mới và kiểm tra một giao dịch.
- [ ] Tắt/mở lại app vẫn thấy lịch sử; CLI và pilot regression vẫn đạt.

## 6. Thêm dữ liệu QQQ qua giao diện — M

**Phụ thuộc:** 2, 5.
**Nội dung:** chọn mã từ SPY/QQQ, tải dữ liệu thành job; mỗi thí nghiệm vẫn chỉ một tài sản.

- [ ] Tải/đăng ký QQQ có nguồn, lịch, checksum và trạng thái rõ ràng.
- [ ] Engine dùng đúng symbol và đúng dữ liệu; không sao dữ liệu SPY sang nhãn khác.
- [ ] Mạng lỗi hoặc lịch chưa đủ không tạo snapshot ready; không làm mất snapshot trước.

**Kiểm tra:** kiểm tra phân biệt dữ liệu hai mã, failure injection và một backtest QQQ có audit.
**Vùng tệp:** loader, worker dataset, routes, template và tests; tối đa 5 tệp chính.

## 7. Nhân bản và so sánh hai thí nghiệm — M

**Phụ thuộc:** 5.
**Nội dung:** nhân bản cấu hình thành giả thuyết mới; so sánh hai run cùng điều kiện.

- [ ] Bản nhân có liên kết cha và ghi chú mới; không sửa kết quả bản gốc.
- [ ] Chỉ so sánh chung khi khớp snapshot/kỳ/vốn/chi phí; lệch điều kiện được giải thích rõ.
- [ ] Nhãn giai đoạn đã xem được giữ khi nhân bản; không tự gọi lại nó là holdout mới.

**Kiểm tra:** matrix điều kiện tương thích, browser compare và lineage sau restart.
**Vùng tệp:** service compare, routes, template, store và tests; tối đa 5 tệp chính.

## 8. Đóng gói cách chạy và nghiệm thu Release 0.1 — S

**Phụ thuộc:** 6–7.
**Nội dung:** lệnh khởi động app/worker, hướng dẫn tiếng Việt và smoke test toàn vòng.

- [ ] Một lệnh khởi động trên loopback, log và trạng thái worker dễ tìm.
- [ ] Browser flow tạo → chạy → xem → so sánh → ghi chú → mở lại hoạt động; tests lõi/API đạt.
- [ ] README nói đúng phạm vi, giả định giá/chi phí và cách sao lưu SQLite + snapshot + artifact.

**Kiểm tra:** full pytest, browser smoke và restart với dữ liệu có sẵn; không cần deploy cloud.
**Vùng tệp:** entrypoint, README và smoke tests.

## Checkpoint G1 — Release 0.1

- [ ] Browser hoàn thành create → run → inspect trade → compare → note.
- [ ] Restart giữ lịch sử; pilot/CLI/audit/replay vẫn đạt.
- [ ] Một lệnh khởi động app và có hướng dẫn sao lưu DB/snapshot/artifact.

## Release 0.2 — Rule Strategy Lab

### Task 9: Indicator registry và trade evidence — M

**Phụ thuộc:** G1. Tạo registry cho SMA, RSI, Bollinger, Donchian, ATR và bộ feature phân tích.

- [ ] Công thức có golden tests; warmup không sinh tín hiệu giả.
- [ ] Mỗi fill liên kết indicator của phiên tín hiệu và phiên khớp.
- [ ] Thay dữ liệu tương lai không đổi feature/tín hiệu quá khứ.

**Kiểm tra:** unit/golden/truncated-history tests và một run thật có audit.

### Task 10: RSI/Bollinger strategy — M

**Phụ thuộc:** 9.

- [ ] Entry/exit đúng rule trong roadmap và được mô tả đúng trên UI.
- [ ] Dùng chung benchmark, cost matrix, audit và report với SMA.
- [ ] Run detail giải thích được entry/exit từ indicator evidence.

**Kiểm tra:** strategy tests, full-run audit và browser flow.

### Task 11: Donchian/ATR strategy — M

**Phụ thuộc:** 9.

- [ ] Donchian breakout không đưa close hiện tại vào ngưỡng so sánh.
- [ ] ATR được version và hiển thị; sizing v0.2 vẫn all-in để giữ một biến thay đổi.
- [ ] Dùng chung benchmark, cost matrix, audit và report.

**Kiểm tra:** off-by-one tests, full-run audit và browser flow.

### Checkpoint G2

- [ ] Ba strategy chạy trên SPY/QQQ với cùng chuẩn evidence.
- [ ] UI không cho chạy tổ hợp ngoài typed schema.

## Release 0.3 — Universe và Walk-forward

### Task 12: Universe 10 ETF — M

**Phụ thuộc:** G2. Thêm IWM, DIA, TLT, GLD, XLF, XLK, XLE, XLV vào SPY/QQQ.

- [ ] Snapshot/symbol không lẫn và không tạo dữ liệu trước inception.
- [ ] Lỗi một symbol không làm hỏng snapshot ready khác.
- [ ] UI hiện range/freshness trước khi chọn.

**Kiểm tra:** isolation, partial-failure, checksum và date-boundary tests.

### Task 13: Walk-forward evaluator — M

**Phụ thuộc:** 12.

- [ ] Fold theo thời gian; `train_end < evaluation_start`; không shuffle.
- [ ] Aggregate truy về từng fold/run/snapshot.
- [ ] Report dispersion, turnover, exposure, costs và benchmark.

**Kiểm tra:** leakage/boundary tests và synthetic folds có kết quả biết trước.

### Checkpoint G3

- [ ] Chạy rule strategy qua universe/folds và tái lập không mạng.
- [ ] Drill-down từ aggregate tới trade hoạt động.

## Release 0.4 — Machine Learning Lab

### Task 14: Feature và label pipeline có version — M

**Phụ thuộc:** G3.

- [ ] Feature đúng bộ G2; label là forward return 20 phiên > 10 bps.
- [ ] Dòng thiếu đủ horizon bị loại, không điền nhãn giả.
- [ ] Preprocessing chỉ fit train fold; schema/output có hash.

**Kiểm tra:** hand-calculated labels, future-mutation và train-only preprocessing tests.

### Task 15: Logistic Regression baseline — M

**Phụ thuộc:** 14.

- [ ] Lưu probability/threshold theo fold và tái lập với config cố định.
- [ ] Threshold chỉ chọn từ train; signal vẫn khớp phiên kế tiếp.
- [ ] So log loss/Brier/calibration và trading metrics với always-long/rules.

**Kiểm tra:** deterministic, threshold leakage và engine integration tests.

### Task 16: HistGradientBoosting challenger — M

**Phụ thuộc:** 15.

- [ ] Dùng cùng feature/fold/metric với baseline.
- [ ] Model card ghi data/hash/config/limitations.
- [ ] Promotion gate trả pass/fail và nguyên nhân bằng code.

**Kiểm tra:** deterministic replay, baseline comparison, threshold/cost stress.

### Task 17: LightGBM decision spike — S

**Phụ thuộc:** 16; chỉ chạy khi có thiếu hụt chất lượng/tốc độ đo được.

- [ ] Đăng ký vấn đề và tiêu chí giữ/bỏ trước khi thử.
- [ ] Dùng cùng contract/folds, không mở rộng feature hoặc tune hàng loạt.
- [ ] Không qua tiêu chí thì không thêm dependency vào sản phẩm.

**Kiểm tra:** benchmark cùng dữ liệu và decision record.

### Checkpoint G4

- [ ] ML qua promotion gate hoặc được giữ rõ ở research-only.
- [ ] Audit/leakage/replay lỗi thì model không đi paper.

## Release 0.5 — AI Research Copilot

### Task 18: AI giải thích theo evidence — M

**Phụ thuộc:** G1 và quyết định provider/model/budget.

- [ ] Evidence packet giới hạn, đúng run và không chứa secret/path tùy ý.
- [ ] Answer tham chiếu metric/trade/indicator; thiếu dữ liệu thì nói thiếu.
- [ ] Provider lỗi không ảnh hưởng backtest; lưu model/usage khi có.

**Kiểm tra:** known-answer/mock/timeout tests và một smoke call thật khi đã cấu hình.

### Task 19: Idea-to-template — M

**Phụ thuộc:** 10–11, 18.

- [ ] AI chỉ tạo schema SMA, RSI/Bollinger hoặc Donchian/ATR.
- [ ] Người dùng review rule chuẩn hóa trước khi tạo run.
- [ ] Output ngoài allowlist bị từ chối; không thực thi Python.

**Kiểm tra:** schema fuzz/invalid-output tests và browser review flow.

### Checkpoint G5

- [ ] AI giải thích đúng bộ trade đã đối chiếu thủ công.
- [ ] Tắt AI vẫn sử dụng được toàn bộ workbench.

## Release 0.6 — Paper Trading

### Task 20: Paper account và ledger — M

**Phụ thuộc:** G3 cho rule hoặc G4 cho ML được chọn.

- [ ] Cash/position/order/fill ledger đối soát được mỗi phiên.
- [ ] Cùng strategy contract và next-session semantics với backtest.
- [ ] Research run và paper state tách biệt.

**Kiểm tra:** multi-day simulation, restart và reconciliation tests.

### Task 21: Risk gate và kill switch — M

**Phụ thuộc:** 20.

- [ ] Allowlist, long-only, no leverage, size/daily caps và stale-price denial.
- [ ] Order intent có idempotency key; retry không tạo duplicate.
- [ ] Breach hoặc reconciliation lỗi dừng order mới và lưu reason.

**Kiểm tra:** breach matrix, stale data, duplicate/restart và halt tests.

### Task 22: Shadow 8 tuần và readiness review — M

**Phụ thuộc:** 21.

- [ ] Theo dõi expected/actual signal-order-fill và mọi mismatch.
- [ ] Không có missing/duplicate order không giải thích; daily reconciliation đạt.
- [ ] Có quyết định tiếp tục/sửa/dừng; không tự mở broker thật.

**Kiểm tra:** báo cáo vận hành hàng tuần và final readiness review.

### Checkpoint G6

- [ ] Paper chạy đủ 8 tuần thị trường và mọi risk/reconciliation gate.
- [ ] Live trading vẫn tắt; broker/capital/approval cần spec riêng.
