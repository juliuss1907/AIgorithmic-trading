# Kế hoạch Release 0.1: Phòng nghiên cứu trading cá nhân

Roadmap từ rule-based đến ML, AI và paper trading nằm tại [roadmap.md](roadmap.md).

Trạng thái: **đã chốt hướng mặc định web app + AI giải thích**; chưa bắt đầu triển khai release 0.1.
Ngày: 2026-09-14. Tiếp nối thí nghiệm SPY đã hoàn thành.

## Mục tiêu và giả định

Xây một ứng dụng giúp Julius biến giả thuyết trading thành thí nghiệm có thể kiểm chứng:
chọn quy tắc, chạy dữ liệu lịch sử, xem từng giao dịch, so sánh kết quả và ghi lại điều đã học.
Thành công là hoàn thành vòng nghiên cứu mà không cần sửa code hoặc gõ lệnh cho mỗi lần chạy.

Đã biết: người dùng mới cả trading và lập trình, chọn chứng khoán Mỹ, muốn dùng thử công cụ rồi xây.
Mặc định đề xuất cho bản này: một người dùng, chạy trên máy cá nhân, giao diện và giải thích tiếng Việt.

Hướng mặc định là **web app + AI giải thích kết quả**. Model, endpoint và ngân sách AI chưa được chọn;
không tự mua dịch vụ hoặc dùng một tài khoản API chưa được chỉ định.

## Bản đầu người dùng nhận được

### Vòng sử dụng

1. Vào thư viện, mở lại thí nghiệm SPY hiện tại hoặc tạo một thí nghiệm mới.
2. Viết lý do thử, chọn dữ liệu, SMA nhanh/chậm, thời gian và vốn mô phỏng.
3. Đọc bản tóm tắt bằng tiếng Việt: mua lúc nào, bán lúc nào, khớp giá nào, chi phí bao nhiêu.
4. Bấm chạy; xem trạng thái và lỗi cụ thể nếu dữ liệu hoặc phép tính không hợp lệ.
5. Xem đường vốn, drawdown, đối chứng mua–nắm giữ và từng vòng mua/bán.
6. Yêu cầu AI giải thích một kết quả/giao dịch, rồi lưu ghi chú cho thí nghiệm kế tiếp.

### Phạm vi đề xuất

- Dữ liệu ngày cho SPY và QQQ; mỗi thí nghiệm chỉ một tài sản. Đây là phạm vi kiểm thử kỹ thuật.
- Hai kiểu chiến lược: SMA crossover với tham số nhập được, buy-and-hold làm đối chứng.
- Long/cash, không đòn bẩy; giữ nguyên cách điều chỉnh giá và quy ước thực thi đã kiểm chứng.
- Ba kịch bản trượt giá 0/5/10 bps mỗi chiều; commission 0 được hiển thị là giả định.
- Giai đoạn tìm hiểu và đánh giá riêng; mỗi phiên bản ghi nhận giả thuyết trước khi chạy.
- Thư viện lần chạy, nhân bản cấu hình, ghi chú và so sánh hai thí nghiệm tương thích.
- AI giải thích khi người dùng yêu cầu, dựa trên bằng chứng của lần chạy đã hoàn thành.

Để sau v1: tự sinh/chạy Python, đọc tin tức mạng, nhiều agent tranh luận, ML dự báo, tối ưu hàng loạt,
phân bổ nhiều tài sản, dữ liệu intraday, paper trading liên tục, broker và giao dịch thật, triển khai nhiều người dùng.
Các phần này cần thí nghiệm và quyết định riêng; không suy ra hiệu quả từ backtest SMA hiện tại.

## Các phần kế thừa và cần sửa

Kế thừa Vibe-Trading 0.1.15, loader kiểm tra lịch, checksum, tín hiệu có tính nhân quả,
kiểm toán số dư và các chỉ tiêu trong `lab/`. Không fork toàn bộ UI/agent runtime của Vibe-Trading.

Cần sửa trước khi mở rộng:

- Tách các chỗ gắn cố định `SPY.US`, tên CSV, thư mục dữ liệu và đường dẫn cấu hình.
- Báo cáo hiện có nội dung cố định SMA 20/50, 10.000 USD và kỳ đánh giá: phải sinh từ cấu hình thực tế.
- Không chạy nhiều backtest bằng thread trong web process: `redirect_stdout` và matplotlib có trạng thái toàn cục.
- Kiểm toán đang giả định một tài sản, long/cash: giữ phạm vi này cho v1.
- `compare` hiện yêu cầu provenance giống hệt; kiểm tra hồi quy sau refactor cần so số liệu thí nghiệm gốc,
  cho phép code hash thay đổi có giải thích. Kiểm tra tái lập giữa hai lần chạy cùng bản code vẫn giữ chặt.

Thư mục `data/` và `runs/initial/`, `runs/replay/` của pilot tiếp tục được đọc như bằng chứng gốc.
Đưa pilot vào thư viện bằng bước đăng ký có thể chạy lặp an toàn; không di chuyển/ghi đè dữ liệu cũ.

## Kiến trúc đề xuất

**Python 3.12 + FastAPI + Jinja2 + JavaScript nhỏ + SQLite + Vibe-Trading.**
FastAPI/Jinja2 đã có trong môi trường qua phụ thuộc; khai báo trực tiếp khi dùng cho ứng dụng.
Giữ uv.lock. Giao diện render ở server, form gửi JSON và polling trạng thái; chưa cần frontend build riêng.
Biểu đồ tương tác nhẹ bằng SVG/JavaScript; báo cáo PNG/Markdown vẫn là đầu ra tải về.

```text
Giao diện web tiếng Việt
        ↓
FastAPI: cấu hình, thư viện, so sánh, giải thích
        ├── SQLite: bản nháp, lần chạy, trạng thái, ghi chú
        ├── Snapshot/artifact bất biến trên filesystem
        └── Worker riêng, xử lý từng công việc
                  ↓
            lab → Vibe-Trading → audit → báo cáo

AI nhận gói bằng chứng của lần chạy hoàn thành và trả lời giải thích
```

Web chạy loopback, một instance. Worker lấy công việc từ SQLite, mỗi thời điểm một backtest;
web request trả ngay ID công việc. Tách tải dữ liệu thành công việc có trạng thái; replay không tải lại.
Khi khởi động lại, công việc bị dở đánh dấu interrupted; người dùng tạo lần chạy mới để thử lại.
Chỉ hiển thị completed khi báo cáo và audit đã thành công. Lỗi có thông báo ngắn và log cục bộ.
Các route ghi nhận chỉ request từ chính app; không mở endpoint chạy shell hoặc nhận đường dẫn filesystem tùy ý.

### Dữ liệu và giao diện tối thiểu

- **DatasetSnapshot:** mã tài sản, khoảng ngày, nguồn, thời điểm tải, cách điều chỉnh và checksum.
- **ExperimentDraft:** tiêu đề, giả thuyết, snapshot, chiến lược/tham số, kỳ đánh giá và giả định vốn/chi phí.
- **Run:** bản cấu hình đóng băng, snapshot ID, phiên bản engine/code, trạng thái, artifact và liên kết bản gốc nếu nhân bản.
- **ResearchNote:** run ID và ghi chú người dùng.
- **AIExplanation:** run ID, câu hỏi, nội dung trả lời, tham chiếu bằng chứng, model/thời điểm/usage nếu có.

API nội bộ theo tài nguyên: `/api/datasets`, `/api/experiments`, `/api/runs`,
`/api/runs/{id}/results`, `/api/runs/{id}/notes`, `/api/runs/{id}/explanations`.
Tạo run trả ID + trạng thái; web kiểm tra trạng thái qua GET.
Server tự giải quyết ID sang tệp được phép. Schema response của kết quả dùng chung cho UI và gói bằng chứng AI.
Wire schema chi tiết sẽ chốt trong task hợp đồng trước khi nối form và worker.

### Nguyên tắc kiểm chứng

- Snapshot mới chỉ sẵn sàng sau khi đủ lịch phiên, giá hợp lệ và checksum hoàn tất. Lần tải lỗi không thành dataset sử dụng được.
- Thay tham số hoặc kỳ dữ liệu tạo run mới. Kết quả và giả thuyết cũ không bị sửa theo run mới.
- So sánh hai chiến lược chỉ khi cùng snapshot, kỳ, vốn và chi phí; khác điều kiện thì chỉ xem riêng, không xếp hạng chung.
- Phân biệt giai đoạn đánh giá đã xem. Nhân bản sau khi xem kết quả không biến dữ liệu đó thành holdout mới.
- AI nhận số liệu, giả định, audit và giao dịch liên quan; không tự tính lại P&L bằng văn bản.
- Kết luận AI có liên kết bằng chứng để kiểm tra; thiếu bằng chứng thì nói thiếu. Giao diện tách số liệu engine và diễn giải AI.
- Chưa cấu hình AI vẫn chạy toàn bộ nghiên cứu. API AI lỗi không làm mất kết quả backtest.

## Thứ tự triển khai và nghiệm thu

Chi tiết công việc, phụ thuộc và kiểm tra tại [todo.md](todo.md).

1. Chuẩn hóa lõi và snapshot; chứng minh thí nghiệm SPY cũ giữ nguyên số liệu.
2. Mở thư viện web để đọc bằng chứng và ghi chú của pilot hiện tại.
3. Tạo/chạy thí nghiệm từ form; lưu trạng thái và cấu hình từng lần.
4. Bổ sung QQQ và so sánh các biến thể với cùng điều kiện.
5. Thêm AI giải thích sau khi chốt model/endpoint/ngân sách.
6. Kiểm tra trọn luồng, restart và hướng dẫn khởi động một lệnh.

**V1 đạt khi:** từ trình duyệt có thể chọn dữ liệu, mô tả giả thuyết, chạy SMA với tham số hợp lệ,
đối chiếu buy-and-hold, mở một giao dịch để kiểm tra, ghi chú và mở lại sau restart.
Khi AI đã được cấu hình, người dùng nhận được giải thích có nguồn từ chính run đó.
Số liệu pilot không thay đổi ngoài dung sai serialization đã nêu; audit và replay đều đạt.

## Nguồn kỹ thuật

- [Thí nghiệm và đánh giá hiện tại](../docs/evaluation.md).
- [FastAPI: Jinja2 templates](https://fastapi.tiangolo.com/advanced/templates/).
- [FastAPI: lưu ý về tác vụ tính toán nền](https://fastapi.tiangolo.com/tutorial/background-tasks/).
- [Python 3.12: SQLite](https://docs.python.org/3.12/library/sqlite3.html).
