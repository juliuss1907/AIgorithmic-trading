# Đánh giá sau thí nghiệm đầu tiên

Ngày thực hiện: 2026-09-14. Engine: Vibe-Trading 0.1.15, Python 3.12.13.

## Kết luận về công cụ

**Tiếp tục dùng Vibe-Trading cho các thí nghiệm nhỏ qua lớp CLI hiện tại.**
Ba tiêu chí của kế hoạch đã được kiểm tra trong phạm vi thí nghiệm này:

| Tiêu chí | Bằng chứng | Kết luận |
|---|---|---|
| Kiểm tra được phép tính | Engine xuất lệnh/vốn; sổ tiền mặt độc lập đối chiếu mọi phiên và lệnh | Đạt cho SPY long/cash |
| Tái lập được | Hai lần chạy 12 ca, 38 tệp số liệu/provenance giống nhau; lần thứ hai chặn kết nối socket trong Python | Đạt trên cùng môi trường/snapshot |
| Có đường dùng cho người mới | Ba lệnh fetch/run/compare, báo cáo tiếng Việt, công thức và giao dịch mẫu | Có hướng dẫn; cần phản hồi của người dùng về độ dễ hiểu |

Chưa đánh giá trải nghiệm Web UI, chất lượng agent tự sinh chiến lược hoặc khả năng giao dịch thật.
Việc gọi engine trực tiếp giúp kiểm tra phép tính mà không phụ thuộc model AI. Bản cài này có 196
package ngoài dự án trong lockfile; môi trường tương đối lớn cho một thí nghiệm. Chưa có lý do phải
tự viết lại engine khi công cụ hiện tại vẫn đạt mục tiêu kiểm chứng.

## Kết luận về chiến lược

Với trượt giá 5 bps mỗi chiều:

| Giai đoạn | Vốn cuối SMA | Vốn cuối mua–nắm giữ | Drawdown SMA | Drawdown mua–nắm giữ |
|---|---:|---:|---:|---:|
| 2015–2021 | 22.325,70 USD | 26.188,78 USD | −12,44% | −33,72% |
| 2022–2025 | 10.826,25 USD | 15.119,91 USD | −28,16% | −24,49% |

Trong giai đoạn đầu, SMA đổi một phần lợi nhuận lấy drawdown thấp hơn. Ở giai đoạn đánh giá,
SMA vừa lợi nhuận thấp hơn vừa drawdown sâu hơn mua–nắm giữ. Chưa có bằng chứng từ thí nghiệm
này để chọn SMA 20/50 thay cho đối chứng hoặc chuyển nó sang chạy tiền thật.

AI có thể giúp giải thích các chuỗi giao dịch này; chưa có phép so sánh nào chứng minh thêm AI
dự báo hoặc nhiều agent sẽ cải thiện kết quả.

## Bằng chứng đã lưu

- [Báo cáo đầy đủ](../runs/initial/report.md), [biểu đồ](../runs/initial/equity.png).
- [Cấu hình và nguồn](../runs/initial/provenance.json), [số liệu](../runs/initial/summary.json).
- [Dữ liệu manifest](../data/manifest.json).
- Lệnh xác minh: `uv run --frozen python -m lab compare runs/initial runs/replay`.
- 13 tests đạt: chỉ báo, không đọc tương lai, calendar, dữ liệu lỗi, giá khớp phiên sau,
  chi phí, chốt sổ, cash-only và tái lập.
- Có cảnh báo deprecation NumPy/pandas từ thư viện lịch; không có test lỗi.

Đã đối chiếu riêng 12 lệnh đầu (ba vòng mỗi giai đoạn): cộng trực tiếp 20/50 giá đóng cửa
trong CSV bằng `math.fsum`, không gọi hàm rolling của chiến lược, rồi tính giá khớp từ mở cửa.
Ví dụ ba vòng đầu giai đoạn đánh giá:

| Ngày lệnh | Hành động | Phiên tạo tín hiệu | SMA20 | SMA50 | Giá khớp sau trượt giá |
|---|---|---|---:|---:|---:|
| 2022-01-03 | Mua | 2021-12-31 | 439,254765 | 435,958469 | 448,383800 |
| 2022-01-26 | Bán | 2022-01-25 | 435,925459 | 436,161168 | 414,474576 |
| 2022-04-04 | Mua | 2022-04-01 | 414,480878 | 414,436807 | 427,897052 |
| 2022-05-03 | Bán | 2022-05-02 | 411,420319 | 411,734548 | 391,508098 |
| 2022-08-02 | Mua | 2022-08-01 | 371,063019 | 370,622796 | 388,006621 |
| 2022-09-19 | Bán | 2022-09-16 | 381,180331 | 382,427313 | 363,658132 |

Tất cả lệnh mua có SMA20 > SMA50 ở phiên trước; các lệnh bán có quan hệ ngược lại.
Ngày đầu giai đoạn có thể mở vị thế từ một tín hiệu đã hình thành trong dữ liệu warmup.

## Bước tiếp theo có giá trị

Ưu tiên cùng đọc ba giao dịch đầu của năm 2022, đối chiếu quy tắc với kết quả trong báo cáo.
Chỉ chọn tính năng mới sau khi biết điều gì khó hiểu trong vòng nghiên cứu này.
Nếu sau đó thử một giả thuyết khác, ghi trước lý do và tiêu chí đánh giá; không tiếp tục chỉnh tham số
trên giai đoạn đánh giá đã xem rồi coi kết quả là kiểm định độc lập.
