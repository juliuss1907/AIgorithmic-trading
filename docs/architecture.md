# Kiến trúc và quy tắc thí nghiệm

## Phạm vi

Release 0.1 hỗ trợ hợp đồng SPY/QQQ, dữ liệu ngày và chiến lược SMA crossover, giữ vị thế mua
hoặc tiền mặt. Pilot hiện tại là SPY SMA 20/50 với vốn giả lập 10.000 USD.
Không tối ưu, không short, không đòn bẩy, không đặt lệnh thật. Web app chỉ bind loopback và hiện
là workbench cục bộ để quản lý snapshot, tạo thí nghiệm và đọc lại bằng chứng.

## Luồng dữ liệu

```text
Yahoo Finance → snapshot ID + CSV gốc + giá đã điều chỉnh + manifest
                       ↓ kiểm tra lịch/checksum → catalog SQLite
              SignalEngine: SMA 20 > SMA 50
                       ↓ mục tiêu ở cuối phiên
              Vibe-Trading: mở cửa phiên kế tiếp
                       ↓
              Lệnh khớp + vốn từng phiên
                       ↓ đối chiếu tiền mặt/số lượng độc lập
              Báo cáo tiếng Việt + biểu đồ + dấu vết
                       ↓ whitelist artifact theo ID
              Web library → chỉ tiêu/trade/note SQLite
```

`fetch` là tác vụ mạng tách biệt; từ web nó được đưa vào bảng `dataset_jobs` riêng để trạng thái tải
không lẫn với backtest. Cùng một worker ưu tiên dataset job rồi mới claim run job. Snapshot chỉ được
công bố `ready` sau khi đủ file và qua validation.
Đăng ký lặp cùng nội dung là idempotent. `run` dùng snapshot đã chọn và gọi trực tiếp
`GlobalEquityEngine.run_backtest` của Vibe-Trading, qua loader bộ nhớ nhỏ.
Không gọi loader dự phòng hoặc benchmark bên ngoài. Không cần nạp cấu hình broker/AI.

Web library index các run hoàn chỉnh vào SQLite nhưng không chép hoặc sửa kết quả. Artifact chỉ được
phục vụ qua ID đã đăng ký và được đối chiếu checksum mỗi lần đọc. Research note ở bảng riêng nên restart
không làm mất ghi chú và không thay đổi provenance/summary.

Web API chỉ enqueue cấu hình đã validate và đóng băng dataset ID; nó không chạy backtest trong request.
Worker riêng claim một job bằng transaction SQLite, chạy tuần tự, tạo report/audit rồi mới đăng ký run và
đánh dấu `completed`. Run đang `running` khi worker khởi động lại được chuyển thành `interrupted`; retry
luôn có job/output ID mới. Mỗi event `queued/started/completed/failed/interrupted` là một dòng JSON có
`request_id` và `entry_point` trong `state/job-logs/<job-id>.jsonl` đồng thời được in ra stdout.
Dataset job áp dụng cùng quy tắc restart/retry và ghi vào `state/dataset-job-logs/`; lỗi tải hoặc thiếu
phiên không tạo bản ghi `ready` và không tác động snapshot đã tồn tại.

Các câu hỏi vận hành mà telemetry phải trả lời: job nào đang chờ/chạy; job lỗi ở loại lỗi nào; một job
đã tạo ra run nào; và worker restart đã ngắt những job nào. Không ghi toàn bộ config hay dữ liệu giá vào log.

## Tín hiệu và khớp lệnh

- Tính SMA bằng giá đóng cửa, chỉ dùng cửa sổ lùi. Chưa đủ 50 phiên: mục tiêu bằng 0.
- SMA 20 > SMA 50: mục tiêu bằng 1; thấp hơn hoặc bằng: mục tiêu bằng 0.
- Engine tự dịch tín hiệu một phiên. Code chiến lược **không** gọi `shift(1)` nữa.
- Khi chuyển sang mua, dùng vốn khả dụng; giữ nguyên số lượng cho đến tín hiệu bán.
- Giá mua = mở cửa × (1 + trượt giá); giá bán = mở cửa × (1 − trượt giá).
- Vòng cuối được thanh lý bắt buộc tại đóng cửa ngày cuối, trừ trượt giá. Cả hai phương án dùng cùng quy ước.
- Giai đoạn 2015–2021 và 2022–2025 đều bắt đầu lại từ tiền mặt. Dữ liệu 2014 và phần lịch sử
  trước mỗi giai đoạn chỉ tính chỉ báo, không đóng góp lợi nhuận hoặc giao dịch.
- Buy-and-hold có mục tiêu 1 từ trước ngày bắt đầu, nên vào ở mở cửa phiên đầu của giai đoạn.

## Giá và chi phí

Yahoo trả OHLC, adjusted close và corporate actions. Dữ liệu gốc được giữ nguyên trong CSV.
Chuyển OHLC thành giá tổng hợp bằng `OHLC × Adj Close / Close`; volume không đổi.
Cổ tức được phản ánh ngầm qua chuỗi điều chỉnh, không ghi có tiền mặt thêm lần nữa.

Cách này phù hợp cho thí nghiệm so sánh đơn giản với cùng cách xử lý ở hai phía.
Đơn vị vị thế ở đây là đơn vị giá điều chỉnh, không phải cổ phiếu có giá USD lịch sử thực tế.
Chưa mô hình hóa thời điểm nhận/tái đầu tư cổ tức hoặc lịch sử các lần nhà cung cấp sửa dữ liệu.
Chỉ báo 20/50 được tính trên giá điều chỉnh, không sử dụng dữ liệu tương lai trong cửa sổ rolling.

Commission giả định 0. Ba kịch bản trượt giá cố định là 0, 5, 10 bps mỗi lệnh; chưa phải chi phí
được hiệu chuẩn cho một broker. Tiền mặt không hưởng lãi. Không tính thuế hoặc chi phí dữ liệu/AI vào lợi nhuận.

## Đo lường và đối chiếu

Đối chứng là một ca engine độc lập, cùng dữ liệu/vốn/chi phí/lịch bắt đầu và kết thúc.
Không sử dụng benchmark mặc định của engine cho kết luận chính.

- Lợi nhuận: vốn cuối / vốn đầu − 1.
- CAGR: `(vốn cuối / vốn đầu) ** (252 / số phiên đánh giá) − 1`.
- Drawdown: mức thấp nhất của vốn / đỉnh vốn trước đó − 1, có đưa vốn đầu vào đỉnh ban đầu.
- Vòng giao dịch: số lần mua rồi bán, gồm vòng bị chốt sổ cuối kỳ.
- Thời gian có vị thế: tỷ lệ phiên có vị thế sau giao dịch mở cửa; phiên thanh lý ở đóng cửa vẫn được tính.
- Thời gian giữ trung bình: số phiên giữa mua và bán theo engine; một giao dịch vào/ra cùng phiên là 0.

Audit kiểm tra target thực hiện khớp tín hiệu phiên trước, giá khớp khớp công thức chi phí,
không âm tiền mặt/short, vốn từng phiên khớp sổ tiền mặt + số lượng × đóng cửa,
và vốn cuối khớp vốn đầu + tổng P&L. Tính lại này không dùng chỉ tiêu lợi nhuận của engine.

## Tái lập và lỗi

`uv.lock` cố định toàn bộ phụ thuộc; Python dùng nhánh 3.12; engine cố định 0.1.15.
Mỗi thư mục chạy giữ cấu hình, bản sao code, lockfile và checksum. Dữ liệu tải một lần và kiểm checksum trước mỗi lần chạy.
Lịch XNYS kiểm tra thiếu, thừa và trùng phiên; dữ liệu không hữu hạn hoặc OHLC sai bị từ chối.

`compare` so byte của provenance, summary và fills/equity/audit từng ca. Không so timestamp
trong run card của engine hoặc metadata của hình. Nếu code/config/dữ liệu đổi, compare từ chối.
Các thư mục kết quả đã tồn tại không bị ghi đè; lần chạy lỗi có thể để lại thư mục bằng chứng chưa hoàn chỉnh.

## Nguồn giao diện đã dùng

- [Vibe-Trading 0.1.15: engine và vòng thực thi](https://github.com/HKUDS/Vibe-Trading/blob/v0.1.15/agent/backtest/engines/base.py)
- [Engine chứng khoán Mỹ](https://github.com/HKUDS/Vibe-Trading/blob/v0.1.15/agent/backtest/engines/global_equity.py)
- [Hợp đồng SignalEngine của runner](https://github.com/HKUDS/Vibe-Trading/blob/v0.1.15/agent/backtest/runner.py)
