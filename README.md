# Phòng thử nghiệm trading

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

Sau đó mở `http://127.0.0.1:8000`. App tự nhập các run hoàn chỉnh trong `runs/` vào
`state/lab.sqlite3`. Chỉ ghi chú được thay đổi; summary, provenance và artifact được kiểm checksum
trước khi phục vụ. App không nhận đường dẫn file tùy ý từ URL.

API `POST /api/jobs` nhận đúng schema của `experiment.json` và trả job ID ngay. Worker chạy riêng,
mỗi lần chỉ xử lý một job:

```bash
uv run --frozen python -m lab.jobs
```

Trạng thái xem tại `GET /api/jobs/{job_id}`. Nếu worker dừng giữa chừng, lần khởi động tiếp theo
đánh dấu job cũ là `interrupted`; retry tạo job ID và thư mục output mới thay vì ghi tiếp kết quả cũ.
Log JSONL theo job nằm trong `state/job-logs/`.

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
