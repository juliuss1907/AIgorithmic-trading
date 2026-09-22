# Phòng thử nghiệm trading

## Intraday BTCUSDT — paper isolated 3×

Package `intraday/` là hệ thống mới, độc lập với bot Donchian hằng ngày trong `lab/`.
Nó thu dữ liệu public của Binance USD-M, tạo snapshot 20 feature, lấy quyết định từ
provider typed, chạy deterministic risk gate rồi mới mô phỏng perpetual fill. V1 cố định
`BTCUSDT`, one-way, isolated 3×, tối đa hai tranche và **không có code đặt lệnh thật**.
Hyperliquid được thu thập như evidence liên thị trường ở chế độ `shadow`: WebSocket cho
L2 book, REST 30 giây cho funding/OI/mark/oracle. Mất dữ liệu DEX không chặn Binance.

Kiểm tra cấu hình an toàn và chạy một vòng stub:

```bash
uv run --frozen python -m intraday doctor
uv run --frozen python -m intraday run --once
uv run --frozen python -m intraday cross-venue-status
```

`stub` mặc định trả `Hold`. Muốn kiểm tra toàn bộ đường paper fill có chủ đích:

```bash
uv run --frozen python -m intraday run --once --direction Buy
```

Chạy worker liên tục và dashboard cục bộ trong hai terminal:

```bash
uv run --frozen python -m intraday run
uv run --frozen python -m intraday serve
```

Mở `http://127.0.0.1:8081`. Dữ liệu riêng nằm tại
`state/intraday/intraday.sqlite3`; không dùng chung paper account với `lab/`.
Thiết kế và các launch gate nằm ở
[docs/crypto-intraday-system-design.md](docs/crypto-intraday-system-design.md); thao tác
credential/activation nằm trong
[docs/intraday-provider-runbook.md](docs/intraday-provider-runbook.md).

Triển khai Docker cần tạo `.env.intraday` từ `.env.example`, thay control token, rồi:

```bash
docker compose --env-file .env.intraday -f deploy/intraday/compose.yaml up --build -d
```

Port dashboard chỉ publish trên loopback. Jev/LLM thật vẫn bị khóa cho tới khi hoàn tất
live preflight và activation có audit. Jev được phép tạo quyết định cho **paper account**;
LLM chỉ tạo report/thesis và rule candidate, còn candidate phải qua replay 90 ngày rồi
challenger tối thiểu 14 ngày/30 closed trades. Không có đường đặt lệnh thật.
Cross-venue overlay cũng không thể chuyển sang `active` chỉ bằng sửa `.env`: SQLite phải
có evaluation record `promote` sau tối thiểu 14 ngày, coverage 95%, 100 quyết định khác
Hold và 30 closed trades. Replay baseline-vs-overlay dùng:

```bash
uv run --frozen python -m intraday cross-venue-replay \
  --output-dir state/intraday/cross-venue-replay
uv run --frozen python -m intraday cross-venue-evaluate \
  --evidence evidence/cross-venue-evaluation.json
```

### Kết nối Jev và LLM qua CLI

Secret được lưu trong TOML ngoài Git/SQLite, phải thuộc current user và có mode `0600`.
Không có tham số `--api-key`; nhập ẩn tại prompt hoặc pipe qua stdin có chủ đích:

```bash
uv run --frozen python -m intraday provider add jev-openrouter \
  --role jev --kind openrouter-decisions --model typesafe/jev-1.13
uv run --frozen python -m intraday provider test jev-openrouter
uv run --frozen python -m intraday provider activate jev jev-openrouter

uv run --frozen python -m intraday provider add llm-main \
  --role llm --kind openai-compatible \
  --base-url https://api.openai.com/v1 --model YOUR_MODEL
uv run --frozen python -m intraday provider test llm-main
uv run --frozen python -m intraday provider activate llm llm-main
uv run --frozen python -m intraday provider list
```

`test` thực hiện một request nhỏ có tính phí. `activate` chỉ chấp nhận preflight thành
công trong 10 phút gần nhất. Assignment mới được worker đọc ở tick 5 giây kế tiếp.
Jev lỗi/timeout/circuit-open luôn thành `Hold`; hard-risk exit vẫn chạy. LLM lỗi giữ
thesis/champion hợp lệ gần nhất. Dashboard tại `/api/providers` và `/api/analysts` chỉ
trả metadata đã che; mutation cần Bearer control token và `Idempotency-Key`.

Với Docker, tạo file trước để bind mount không biến nó thành directory:

```bash
install -d -m 700 state/provider-secrets
install -m 600 /dev/null state/provider-secrets/provider-secrets.toml
docker compose --env-file .env.intraday -f deploy/intraday/compose.yaml \
  --profile admin run --rm admin \
  provider add jev-openrouter --role jev --kind openrouter-decisions \
  --model typesafe/jev-1.13 --database /app/state/intraday/intraday.sqlite3 \
  --secrets-file /run/provider-secrets/provider-secrets.toml
```

Service `admin` mount secret read-write để quản lý profile; `worker` mount read-only;
`web` không mount file này. Sau khi thêm profile, chạy `provider test` rồi `provider
activate` bằng cùng mẫu `docker compose ... run --rm admin`.

News worker hiện allowlist RSS của SEC, CFTC, Fed, CoinDesk, Decrypt và Cointelegraph.
The Block, Wu Blockchain và Binance announcements được hiện là `disabled` kèm lý do
thay vì dùng scraper hoặc nguồn mirror không được xác minh.

Hệ thống hiện là **bot thuật toán BTCUSDT + Jev paper-active + LLM có gate**. Jev đề xuất
hướng đi nhưng deterministic risk kernel mới có quyền tạo paper fill; LLM chỉ tạo evidence
và candidate bị giữ sau replay/promotion. Binance integration chỉ dùng public market-data API;
dự án không có endpoint giao dịch thật.

## Kết quả BTC hiện tại

Ba chiến lược mặc định đã chạy trên 3.059 nến thật (2017-08-17 → 2025-12-31), tám fold 2018–2025,
với target tối đa 50%, taker fee 10 bps và slippage 0/5/10 bps. Cả ba qua điều kiện số năm có lãi
và stress-return, nhưng trượt trần drawdown 20%:

| Chiến lược | Năm có lãi | Stress return gộp | Drawdown tệ nhất | Gate |
|---|---:|---:|---:|---|
| Donchian 20/10 + ATR14 | 5/8 | +375,21% | −21,73% | Không qua |
| RSI14 + Bollinger20/2 | 6/8 | +56,95% | −23,83% | Không qua |
| SMA 20/50 | 6/8 | +222,73% | −42,18% | Không qua |

Gate baseline ngày 2026-09-15 vẫn được giữ nguyên làm bằng chứng `stay_cash`. Một giả thuyết mới đã
được đăng ký trước và chạy ngày 2026-09-16: size chỉ được tính lúc vào lệnh theo volatility 20 ngày,
risk target 20%/năm, trần 50%, rồi giữ nguyên tới lúc thoát.

| Chiến lược + risk overlay | Năm có lãi | Stress return gộp | Drawdown tệ nhất | Gate |
|---|---:|---:|---:|---|
| Donchian 20/10 + ATR14 | 5/8 | +373,03% | −18,06% | **Qua** |
| RSI14 + Bollinger20/2 | 6/8 | +20,34% | −23,55% | Không qua |
| SMA 20/50 | 6/8 | +337,20% | −27,78% | Không qua |

Gate mới đã khóa chọn **Donchian** và candidate contract đã được gắn với run/checksum bất biến. Holdout
2026-01-01 → 2026-08-31 sau đó đạt **+7,67% return** với **−7,98% max drawdown** ở mức 5 bps, nên qua
cổng đã đăng ký trước. Promoted paper account đã được tạo từ đúng contract này và chạy một chu kỳ mỗi
ngày lúc **09:00 Việt Nam**; account hiện giữ cash, không có quyền đặt lệnh thật. Xem
[runbook BTC](docs/btc-paper-runbook.md)
và [kế hoạch hiện tại](tasks/plan.md).

Mở dashboard bằng một lệnh:

```bash
make web
```

Lệnh mặc định hiện gate baseline. Để xem gate volatility v1:

```bash
LAB_PROMOTION_STATE=state/btc-promotion-entry-vol20-v1.sqlite3 make web
```

Sau đó vào `http://127.0.0.1:8000/paper`. Chạy toàn bộ kiểm tra bằng `make test`.
Timer paper có thể kiểm tra bằng `make paper-timer-status`.
Telegram summary được hỗ trợ nhưng mặc định tắt cho tới khi có secret file ngoài repository; xem
[runbook BTC](docs/btc-paper-runbook.md#telegram-alerts).

## Pilot SPY trước đây

Thí nghiệm đầu tiên: chiến lược SPY SMA 20/50, dùng engine **Vibe-Trading 0.1.15**.
Bạn có thể đọc quy tắc, xem từng lệnh, so sánh với mua rồi nắm giữ và chạy lại từ cùng dữ liệu.

**Trạng thái:** đã chạy trên dữ liệu thật, kiểm tra số dư độc lập và tái lập thành công.
Web library cục bộ cho phép đọc chỉ tiêu, đường vốn, từng fill và lưu research note.
Đây là môi trường nghiên cứu; phần tính toán chạy bằng code cố định và chưa đặt lệnh thật.

## Bắt đầu từ kết quả

- [Báo cáo tiếng Việt và các giao dịch mẫu](runs/initial/report.md)
- [Biểu đồ vốn](runs/initial/equity.png)
- [Đánh giá Vibe-Trading và kết luận thí nghiệm](docs/evaluation.md)
- [Quy tắc, cách tính và kiến trúc](docs/architecture.md)

Ở mức trượt giá **5 bps = 0,05% mỗi lệnh**, vốn đầu mỗi giai đoạn là 10.000 USD:

| Giai đoạn | SMA 20/50 | Mua–nắm giữ | Drawdown SMA | Drawdown mua–nắm giữ |
|---|---:|---:|---:|---:|
| 2015–2021 | +123,26% | +161,89% | −12,44% | −33,72% |
| 2022–2025 | +8,26% | +51,20% | −28,16% | −24,49% |

Lợi nhuận trong bảng là tổng cả giai đoạn, không phải mỗi năm. Giá điều chỉnh có phản ánh cổ tức;
đây là mô phỏng đơn vị giá tổng lợi nhuận, không phải số dư tài khoản broker thực tế.

## Chạy trong workspace hiện tại

Môi trường `.venv/` và dữ liệu `data/` đã được tạo. Từ thư mục dự án:

```bash
uv run --frozen python -m lab run --output runs/my-first-run
uv run --frozen python -m lab compare runs/initial runs/my-first-run
uv run --frozen pytest -q
```

Mở `runs/my-first-run/report.md` để đọc kết quả. Mỗi lần chạy cần tên thư mục mới;
lệnh từ chối ghi đè lần chạy cũ. `compare` báo lỗi nếu nguồn, cấu hình, dữ liệu hoặc kết quả khác nhau.

Mở thư viện web trên loopback:

```bash
uv run --frozen uvicorn lab.web:app --host 127.0.0.1 --port 8000
```

Sau đó mở `http://127.0.0.1:8000`. Chọn **Thí nghiệm mới**, viết giả thuyết, chọn snapshot,
SMA/vốn/giai đoạn rồi review rule trước khi đưa vào hàng đợi. App tự nhập các run hoàn chỉnh trong `runs/` vào
`state/lab.sqlite3`. Chỉ ghi chú được thay đổi; summary, provenance và artifact được kiểm checksum
trước khi phục vụ. App không nhận đường dẫn file tùy ý từ URL.

Để thêm QQQ, mở **Dữ liệu** rồi chọn **Thêm QQQ**. Release 0.1 cố định khoảng tải
`2014-01-01` đến hết `2025-12-31`, giống pilot SPY. Trang trạng thái chỉ công bố snapshot sau khi
đã kiểm đủ lịch XNYS và checksum; khi hoàn tất, QQQ tự xuất hiện trong form thí nghiệm.

Trên trang chi tiết run, chọn **Nhân bản thí nghiệm** để giữ nguyên dataset, kỳ, vốn và chi phí,
viết giả thuyết mới rồi đổi tham số SMA. Khi worker hoàn tất, trang job dẫn thẳng tới so sánh cha–con.
Bạn cũng có thể chọn **So sánh run** từ thư viện để đặt hai run bất kỳ cạnh nhau. Nếu điều kiện khác,
app vẫn hiện số liệu tham khảo nhưng ghi rõ **Không xếp hạng** và không tính delta.

API `POST /api/jobs` nhận đúng schema của `experiment.json`; `POST /api/dataset-jobs` nhận yêu cầu
tải SPY/QQQ. Cả hai trả job ID ngay. Worker chạy riêng, xử lý dataset job trước rồi đến backtest job:

```bash
uv run --frozen python -m lab.jobs
```

Trạng thái xem tại `GET /api/jobs/{job_id}` hoặc `GET /api/dataset-jobs/{job_id}`. Nếu worker dừng
giữa chừng, lần khởi động tiếp theo đánh dấu job cũ là `interrupted`; retry tạo job ID và output mới
thay vì ghi tiếp kết quả cũ. Log JSONL nằm trong `state/job-logs/` và `state/dataset-job-logs/`.
API đọc `GET /api/compare?left_id=…&right_id=…` trả điều kiện khác nhau và delta từng case khi hợp lệ.

## Cài lại trên máy khác

Cần `uv` và Python 3.12. `uv sync` có thể tải Python phù hợp và tạo môi trường riêng trong dự án.

```bash
uv sync --frozen --python 3.12
uv run --frozen python -m lab fetch
uv run --frozen python -m lab run --output runs/initial
uv run --frozen python -m lab run --output runs/replay
uv run --frozen python -m lab compare runs/initial runs/replay
```

`fetch` là bước tải dữ liệu từ Yahoo Finance. Không cần API key hay tài khoản broker.
Dữ liệu được kiểm tra đầy đủ trước khi đăng ký `ready`; kết quả lệnh trả về dataset ID theo nội dung.
Các lần tải giống nhau không tạo bản ghi trùng. Replay chỉ đọc snapshot đã chọn hoặc snapshot duy nhất
khớp symbol/khoảng thời gian, không tự gọi mạng.

`data/` và `runs/` là dữ liệu cục bộ, không nằm trong Git. Muốn tái lập đúng kết quả ở bảng trên,
hãy mang theo **toàn bộ `data/` hiện tại**, gồm catalog, manifest và các CSV/snapshot.
Tải mới có thể cho giá đã được nhà cung cấp điều chỉnh lại; không hứa giống snapshot cũ.
Giữ `runs/` để lưu cấu hình, bản sao mã nguồn, lockfile và các bằng chứng liên quan;
giữ `state/lab.sqlite3` nếu muốn mang theo ghi chú web.

## Đọc một giao dịch cùng AI

Mở `runs/initial/evaluation/5bps/sma/fills-exact.csv`. Vòng đầu là:

- Mua ngày 2022-01-03: 22,30 đơn vị, giá điều chỉnh sau trượt giá khoảng 448,383800.
- Bán ngày 2022-01-26: giá điều chỉnh sau trượt giá khoảng 414,474576.
- Lỗ: `22,30 × (414,474576 − 448,383800) ≈ −756,18 USD`.

Bạn có thể hỏi: “Dùng dữ liệu và mã trong dự án, giải thích vì sao giao dịch đầu của năm 2022
được mở và đóng. Cho mình xem SMA ở phiên trước, giá mở cửa phiên thực hiện và cách tính lỗ.”
Đây là vai trò AI của bản đầu: giúp hiểu giả thuyết và bằng chứng. Không cần chạy agent trong ứng dụng.

## Các tệp quan trọng

| Tệp | Dùng để làm gì |
|---|---|
| `experiment.json` | Ghi giả thuyết 20/50, hai giai đoạn, vốn và ba mức chi phí |
| `lab/strategy.py` | Quy tắc tạo mục tiêu mua/giữ tiền mặt |
| `lab/data.py` | Tải, điều chỉnh, kiểm tra lịch và checksum dữ liệu |
| `lab/datasets.py` | Catalog SQLite và snapshot ID theo nội dung |
| `lab/dataset_jobs.py` | Hàng đợi tải dữ liệu, retry và log trạng thái |
| `lab/experiment.py` | Gọi engine, kiểm tra từng lệnh và tính chỉ tiêu |
| `lab/report.py` | Viết báo cáo và biểu đồ từ kết quả đã lưu |
| `lab/web.py` | Web library/API cục bộ để đọc bằng chứng và ghi chú |
| `lab/store.py` | Index run/artifact bất biến và research note SQLite |
| `runs/initial/provenance.json` | Nguồn dữ liệu và dấu vết của lần chạy |
| `runs/initial/*/*/*/audit.json` | Kết quả đối chiếu số dư của từng ca |

Engine sinh thêm báo cáo trong `artifacts/`. Đối chứng chính của thí nghiệm là lần chạy
`buy-hold` riêng, không phải cột benchmark mặc định của engine.

## Học tiếp

Đọc và hiểu thí nghiệm này trước khi đổi tham số. Mỗi thay đổi nên có một giả thuyết viết ra trước.
Nếu dùng kết quả 2022–2025 để sửa chiến lược, không gọi đó là giai đoạn chưa nhìn thấy nữa.
Xem [đánh giá công cụ](docs/evaluation.md) để biết phần nào đã được kiểm chứng và phần nào chưa.
