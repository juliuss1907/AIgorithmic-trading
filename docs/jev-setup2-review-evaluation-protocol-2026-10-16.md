# Giao thức đánh giá Jev A v2, chốt trước collect

Giao thức này được commit trước lần gọi Jev thật đầu tiên. Không chọn lại ngưỡng
sau khi xem kết quả 2026. Nghiên cứu offline từng route, không phải danh mục ba slot.

- Câu hỏi: `setup2-review-questions-v2`; chỉ bỏ đúng câu về RSI mà Julius yêu cầu.
- Dữ liệu: bản sao nghiên cứu và funding công khai đã kiểm ở đợt 1, kết thúc
  `2026-10-14T00:00:00Z`. Không backfill thêm cho đợt này.
- Tuning: từ đầu route đến hết 2025-12-31, tức trước `2026-01-01T00:00:00Z`.
  Test: từ thời điểm đó tới cutoff. Mỗi cửa sổ bắt đầu flat, vốn 1.000 USD/route;
  giữ lịch sử trước cửa sổ để làm ấm chỉ báo. Đóng vị thế ở cuối mỗi cửa sổ.
- Grid duy nhất: `t_skip ∈ {0.6,0.7}`, `t_exh ∈ {0.6,0.7}`,
  `t_reduce ∈ {0.5,0.6}`, `t_dis ∈ {0.5,0.6}`: đúng 16 tổ hợp, có cấu hình mặc định.
  Không đổi encoder, luật, hệ số cỡ, stop, phí, funding hay ngưỡng khác.
- Chọn tổ hợp có **tổng net PnL tuning lớn nhất trên 10 route**. Nếu bằng nhau,
  chọn bộ ngưỡng lớn hơn theo thứ tự từ điển `(t_skip,t_exh,t_reduce,t_dis)`.
  Không có ngưỡng tối thiểu hoặc điều kiện phụ được thêm sau khi xem kết quả.
- Ghi riêng toàn bộ điểm tuning và cấu hình thắng cùng SHA256 vào artifact khóa,
  rồi mới chạy replay và đọc PnL test. Không dùng test để phá hòa hay chọn lại.
- Báo cáo rule-only/A mỗi kỳ và mỗi route: số lệnh, net PnL, DD, lợi nhuận lệnh
  trung bình, winrate. Tổng vốn 10.000 USD; DD tổng là đường tổng equity của 10
  tài khoản độc lập, cập nhật theo thời gian và giữ giá trị gần nhất. Đây không
  phải mô phỏng cạnh tranh slot. HYPE Spot không có lệnh vẫn nằm trong tổng vốn.
- CI 95% cho chênh lệch lợi nhuận trung bình/lệnh A trừ rule-only: 10.000 bootstrap,
  seed `20261016`, lấy mẫu lệnh có hoàn lại, riêng từng route và từng nhánh,
  giữ nguyên số lệnh từng nhóm; percentile 2,5/97,5%. Giả định độc lập giữa lệnh
  có thể đánh giá thấp bất định khi tín hiệu tương quan. CI chứa 0 thì không
  kết luận A có ích, dù điểm PnL tốt hơn. Không tính zero giả cho lệnh bị veto.
- Nhóm VETO/REDUCE: ghi PnL phản thực theo lệnh rule-only ghép được; tín hiệu mới
  sau phân kỳ dùng mô phỏng một lệnh bắt đầu flat, đánh dấu riêng và không cộng
  thành PnL danh mục. Không tối ưu ngưỡng từ các phản thực này.
- Calibration trên lệnh rule-only thực sự: đích là `net_known_pnl < 0`; đối chiếu
  P(skip), P(exhaustion), bin cố định `[0,.2),[.2,.4),[.4,.6),[.6,.8),[.8,1]`.
  Brier so với dự báo hằng bằng tỷ lệ lỗ tuning, khóa trước test; tỷ lệ lỗ thực
  tế từng kỳ chỉ là thống kê mô tả. Đây là proxy lỗ lệnh, không phải nhãn chuẩn
  cho sự kiện đảo chiều trước continuation của câu hỏi exhaustion.
- Cache thật phải đủ toàn bộ state v2, không chứa fixture giả trong tập dùng;
  lưu hash file cache, hash tập câu trả lời, model trả về và result_id từng replay.
- Chi phí: trần 1 USD cộng dồn cả retry/resume. Trước mỗi request đặt chỗ theo
  context tối đa 262.144 token × giá đã xác minh 0,042 USD/triệu input token;
  output miễn phí. Hoàn phần dư chỉ khi response báo cost hoặc token hợp lệ.
  Response không có usage và lần gọi bị ngắt giữ nguyên đặt chỗ. Dừng trước khi
  tổng đặt chỗ/chi phí đã ghi cộng request tiếp theo vượt trần. Đây là giới hạn
  theo giá công bố, không thay thế hóa đơn của nhà cung cấp.

Giới hạn bắt buộc: dữ liệu lịch sử có thể đã có trong huấn luyện model; 2026 cùng
thị trường và chế độ luật đã được chọn hậu nghiệm; số lệnh và số state độc lập nhỏ;
không coi phép chia theo thời gian này là bằng chứng triển khai live. Không merge
vào `feature/slot-runtime`, không deploy, không chạm soak trước đánh giá 28/10.
