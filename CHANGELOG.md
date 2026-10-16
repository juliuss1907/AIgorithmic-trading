# Changelog

File này ghi lại các thay đổi đáng chú ý của dự án, bắt đầu từ ngày 2026-10-06. Các thay đổi trước đó xem trong `git log` và các file `docs/*-research.md`.

Mỗi mục gồm ngày, tóm tắt, commit liên quan và link sang tài liệu chi tiết. Mục mới nhất nằm trên cùng.

## 2026-10-16 — State và câu hỏi Jev cho review Setup-2 offline

- Thêm encoder 14 token cố định, không tên coin/ngày/giá/chữ số; chỉ dùng H4 đã đóng,
  đặc trưng Setup-2, profile và funding đã công bố. Ngưỡng kỹ thuật có phiên bản,
  chốt trước khi xem kết quả lệnh; thiếu dữ liệu thành token unknown.
- Thêm ba câu hỏi nguyên tử cho lệnh giữ nhiều ngày, parser nghiêm ngặt và nhãn A
  với hệ số tối đa ×1. Chưa gọi Jev thật; runtime và rule giữ nguyên.
- Cache nghiên cứu riêng ghi thêm, cấm UPDATE/DELETE và kiểm checksum khi đọc;
  fingerprint chỉ ghép tập câu trả lời thực sự dùng, kể cả khóa thiếu, nên thêm
  câu trả lời không liên quan không đổi danh tính replay.
- Lớp phủ replay Setup-2 mặc định tắt; khi bật chỉ giảm/giữ cỡ, veto ghi
  `jev_review_veto`, gắn phiên bản/ngưỡng/hash câu trả lời vào fingerprint. Ghép
  lệnh luật đơn độc theo rule/nến. Tín hiệu bị veto/reduce mới xuất hiện sau khi
  đường vị thế phân kỳ được bổ sung mô phỏng một lệnh độc lập bắt đầu flat;
  vẫn ghi rõ thiếu lệnh trong baseline và không cộng giả định này thành PnL danh mục.
  Chỉ sửa ba file replay bảo vệ đã được duyệt.
- Thêm `aigt jev-review collect --dry-run` để quét mọi tín hiệu và khử trùng state,
  không đọc credential hay gọi mạng. `--execute` là bước riêng, phải khớp hash
  dry-run đã được duyệt; gọi qua credential/transport hiện có, có rate limit,
  retry hữu hạn, circuit breaker và chạy tiếp khóa thiếu. Chỉ kiểm bằng provider giả.
- Thêm paired replay đọc cache và công cụ dựng dữ liệu nghiên cứu từ bản sao G5/
  nguồn công khai, cùng công cụ so checksum toàn bộ output với checkout gốc.
- Review bổ sung test response HTTP bị cắt giữa chừng: chuyển thành lỗi mạng
  đã che nội dung, áp retry/circuit hữu hạn; không làm rơi receipt thu thập.

## 2026-10-16 — Đo hiệu chỉnh của Jev và cỡ mẫu tín hiệu lịch sử

- Chỉ đọc database VPS và kết quả G4. Replay G4 có 340 lệnh đóng trên 10 route trong khoảng 30 tháng (2–3 lệnh/tuần).
- 3.880 mẫu Jev (1 mỗi coin mỗi 15 phút, từ 115.164 câu trả lời): Brier của xác suất hướng kém hơn đoán theo tỉ lệ nền ở 1h, 4h và 24h; Jev quá tự tin. Khi RSI < 30, nhịp hồi Jev chọn đúng sau 4h (lên 70%) nhưng mất sau 24h. Chi tiết: [Jev calibration](docs/jev-calibration-2026-10-16.md).

## 2026-10-15 — Key Binance Demo lên VPS (chuẩn bị G5)

- Key Demo cũ (30/9) không đọc được cả Spot lẫn Futures nên `connect` từ chối Clear/Replace; Julius đổi tên file cũ thành `binance-demo.json.old-20260930` (chờ xoá, mục H1 trong delete request) rồi nhập key mới bằng `aigt connect bnb demo`.
- Trên VPS: tạo `state/execution` và `state/execution-secrets` (700, 1000:1000), chép `binance-demo.json` (600, 1000:1000). Container `demo-execution` chưa bật; không configure/activate, không đặt lệnh. Còn chờ: cài tài khoản Demo (One-way, Single-asset, Isolated, ≥ 500 USDT mỗi ví), soak 28/10, deploy `199f800` và G5/G6 duyệt riêng.

## 2026-10-15 — Chốt kiểm chứng Jev advisory

- Toàn bộ trên `80e9dfd`: **1808 qua, 1 lỗi migration có sẵn được cho phép**, 23 cảnh báo. Parity và slot qua; không skip/xfail. Review Standards/Spec không còn finding mở.
- 10/10 ca tham chiếu giữ `result_id` và summary; 99 hash source bảo vệ, payload mặc định và hash candidate giữ nguyên. Báo cáo: [Jev advisory](docs/jev-advisory-verification-2026-10-15.md), có commit/file, sai khác kế hoạch và manifest checksum.
- Chỉ commit local; không đổi VPS, key, active set hay soak; G5/G6 vẫn duyệt riêng.

## 2026-10-15 — Ổn định checksum của fixture A/B

- Toàn bộ test lần xác nhận phát hiện lỗi hash ngẫu nhiên ở `test_compact_ab`, tái hiện cả trên gốc `cad8779`: connection ghi chưa được dọn khiến WAL checkpoint sau lúc chốt checksum, dù dump logic không đổi.
- Chỉ checkpoint/truncate WAL trong fixture trước khi đóng băng input; thêm test dọn connection rồi kiểm hash. Không đổi mã replay hay bỏ/nới assertion bảo toàn checksum.

## 2026-10-15 — Bảo toàn audit advisory khi stop lỗi

- Review độc lập phát hiện nhánh stop bảo vệ thất bại chưa lưu `slot_selection` và làm mất kết quả ứng viên. Lưu audit cùng reservation/failure và trả kết quả Jev cả khi portfolio phải pause, có test Spot/Perp gây lỗi stop.
- Truyền chế độ advisory tường minh vào bước đặt lệnh, không suy chế độ từ sự có mặt của trường ý kiến Jev.

## 2026-10-15 — Quy trình Demo slot dùng Jev tư vấn

- Bổ sung quyết định của Julius vào ADR-007, checklist G5/G6 và Demo runbook: configure với `--jev-mode advisory`, kiểm status và đường lui về `gate` khi paused + flat.
- Giữ nguyên lịch sử diễn tập 15/10; không đổi active set/candidate/soak, không triển khai hay kích hoạt giao dịch trong đợt này.

## 2026-10-15 — Ý kiến Jev trong Telegram

- Tin soak nêu rõ tín hiệu đến từ luật; dòng Jev ghi đồng ý hoặc phản đối, không còn báo không vào lệnh vì Jev.
- Tin Demo đọc ý kiến tại thời điểm đặt lệnh từ reservation bất biến trong journal; giữ nguyên chống gửi trùng và retry khi Telegram lỗi.

## 2026-10-15 — Runtime slot dùng luật và ghi ý kiến Jev theo nến

Nhánh: `feature/jev-advisory`; chỉ làm local.

- Advisory đọc câu trả lời primary đúng route/rule/nến, không đòi provenance hay tuổi 45 giây; thiếu/lỗi thì chờ tối đa 120 giây từ lúc đóng H4 rồi vẫn xếp hạng theo luật. Giữ kiểm champion/soak, giá lệch 1% từ giá đóng H4, spread 0,1%, filters, stop, đối soát và giới hạn danh mục.
- Định danh entry theo `setup2:{rule_id}:{bar}`, bền qua restart và câu trả lời muộn; audit ghi `jev` trong kết quả, từng bước lựa chọn và route khi reserve lệnh. Kiểm lại giá/rủi ro trước khi đóng vị thế để xoay, giá trước submit.
- Hai điểm phụ cần để hỗ trợ đúng đường mới: mở riêng giới hạn `OrderIntent.source_id` từ 128 lên 160 ký tự để chứa rule ID dài tối đa; truyền đúng dấu âm của vị thế short cho coordinator **chỉ trong advisory**. Test nhiều vị thế Perp ban đầu chỉ mở được một do dấu dương bị hiểu là flip, nay mở/xoay được; nhánh gate và per-route cũ giữ nguyên.
- 39 test runtime/source/slot/G5 qua. Hai đối chứng H4 và 8 ca slot 4/5 coin năm 2026 tái chạy giữ nguyên 10 `result_id` và summary; dữ liệu/replay engine không đổi. Không thay tham số/hash rule, active set, heartbeat hay cách lưu `challenger_allowed`.

## 2026-10-15 — Cờ Jev advisory riêng cho allocation Demo slot

Nhánh: `feature/jev-advisory` từ `cad8779`; chỉ làm local.

- Thêm `DemoAllocation.jev_mode` và `configure --jev-mode gate|advisory`; mặc định gate không xuất thêm trường khi serialize, giữ payload cũ từng byte. Advisory chỉ hợp lệ với `setup2_slots_v1`, không nằm trong active set hay tham số rule.
- Cổng advisory chấp nhận thiếu/khác ý kiến Jev nhưng vẫn đòi luật nổ và gọi coordinator rủi ro với cách tính cỡ lệnh cũ; cổng mặc định và profile cũ giữ hành vi. Hàm thuần `jev_advice` dùng cùng logic lý do của cổng cũ.
- Kiểm thử tập trung: 46 test allocation/CLI/cổng và hồi quy cổng cũ, Setup-2 Perp, active set qua. Chưa triển khai hay đổi trạng thái Demo/VPS.

## 2026-10-15 — Chuẩn bị G5/G6 Binance Demo cho runtime slot ADR-007

Nhánh: `prep/g5-demo`, worktree `system-trading-g5-prep` từ `18f15da`. Chỉ chuẩn bị và diễn tập local; VPS chỉ đọc, không push/deploy/kết nối key hay gửi lệnh Demo. Chi tiết, lệnh và bằng chứng: [checklist G5/G6](docs/runbooks/slot-runtime-demo-rollout-checklist.md#chạy-thử-g5-trên-database-tạm-2026-10-15).

- Đối chiếu `--help` và code cho connect, leverage, scan/venue, configure, preflight, activate, run/once, status, pause, flatten. Checklist sửa thứ tự venue → leverage → configure, đường source/journal/key trong container, tách cổng G5 và G6; ghi rõ `--once` có thể đặt lệnh, cách đưa key lên VPS với quyền 0700/0600 và UID:GID 1000:1000. Hai bind directory trên VPS hiện chưa có.
- Đọc ID/hash campaign hiện tại để điền 9 route của đợt đầu; mốc đánh giá chung an toàn 2026-10-28 04:11Z, Perp cần funding snapshot mới. Giữ active set cả 10 route: configure paused được khi HYPE Spot chưa có soak đạt, chỉ activate 9 route; test chứng minh HYPE Spot bị từ chối không làm đổi portfolio đã active 9 route. Không sửa Jev, cách dùng `rule_allows_answers` hay tham số/hash rule.
- Chép backup 2,17 GB và manifest về `~/.local/state/aigorithmic-trading/g5-rehearsal/`, verify integrity và SHA-256 `6d25bf92…caefeaa5`; dựng active set/candidate trên bản sao thứ hai. CLI thật dừng do thiếu key. Với boundary fixture, configure ghi paused, gross/Perp/margin 100%/50%/50%, giới hạn lỗ 3%/15%, cap 166,67 USDT; preflight/activate dùng source bản sao dừng vì chưa có champion, không tạo evaluation giả.
- Scan công khai 9 route (thêm HYPE Spot để kiểm cấu hình đủ), đọc Demo exchangeInfo và tính cỡ lệnh 166,67/66,67 USDT: đều vượt minimum ở multiplier 1/0,40; ETH Perp minimum 20 USDT, các route khác 5. Tại 0,40, NEAR Perp tròn xuống giảm 2,875%, ZEC khoảng 1,1–1,2%. Multiplier không có sàn 0,40 nên vốn/cỡ nhỏ hơn vẫn có thể `rejected_quantity`.
- Xác nhận Compose nhận đúng 3 biến Telegram, lần đầu chỉ ghi BASELINE; kiểm bằng fixture, không gửi Telegram thật. Worker ID và active-set ID trên VPS giữ nguyên; checksum backup và số evaluation của bản sao không đổi sau diễn tập.
- Kiểm thử cuối: **1.748 qua, 1 lỗi có sẵn được cho phép** `test_perp_collection_migration`, 23 cảnh báo deprecation; không skip/xfail. Hai lỗi tìm được (help argparse và thiếu mẫu Hermes) được sửa ở các mục dưới. Còn chờ key/quyền giao dịch, mode/leverage thật, soak đạt, quyết định Jev và G5/G6 của Julius.

## 2026-10-15 — Khôi phục file mẫu cài đặt Hermes trong checkout sạch

Nhánh: `prep/g5-demo`. Phát hiện khi chạy toàn bộ pytest cho chuẩn bị G5.

- Gói `integrations/hermes/trading-ops` thiếu file mẫu cài đặt vì `.gitignore` bỏ mọi `.env.*`; thêm ngoại lệ đúng đường dẫn và mẫu công khai chỉ có URL loopback, hai trường token để trống. Không đọc cấu hình hay token runtime, không cài/bật Hermes trên VPS.
- Test distribution có sẵn tái hiện lỗi thiếu file trên worktree mới; kiểm lại gói cài đặt sau khi bổ sung.

## 2026-10-15 — Sửa trợ giúp CLI cho bước chuẩn bị G5

Nhánh: `prep/g5-demo`, worktree riêng; chưa push hay deploy.

- Sửa `active-set init/switch --help` bị lỗi định dạng do ký tự `%` trong mô tả `--waive-net-return`; không đổi tham số, hash rule hay cổng Jev.
- Kiểm thử hồi quy cho cả hai lệnh: tái hiện lỗi trước khi sửa, kiểm trợ giúp in được sàn −5% sau khi sửa.

## 2026-10-15 — Tín hiệu soak đầu tiên; cổng Jev chặn mọi lệnh Setup-2

Nhánh: `feature/slot-runtime`. Chỉ đọc dữ liệu trên VPS, không sửa code, không deploy.

- **Tín hiệu soak đầu tiên:** nến H4 đóng 2026-10-15 04:00Z, ETH, HYPE, ZEC Perp cùng ra SHORT; bot Telegram gửi cả 3 tin ngay lần đầu. Jev từ chối cả ba (Strong Buy 0,49 / Buy 0,37 / Strong Buy 0,42).
- **Phát hiện:** trên 115.164 câu trả lời Jev `perp_intraday` (25/09–15/10), cổng `perp_setup2_entry` qua 0 lần ở ngưỡng 0,85 và ở sàn 0,69; khi RSI14 < 30, Jev chọn mua 99,4%. Jev nhận snapshot 1h chung, không biết luật đã ra tín hiệu, và trả lời theo kiểu đảo chiều. Nếu giữ nguyên, Demo sẽ không vào lệnh nào. Soak và mốc 2026-10-28 không bị ảnh hưởng. Chi tiết và các lựa chọn: [docs/jev-setup2-confirmation-2026-10-15.md](docs/jev-setup2-confirmation-2026-10-15.md).

## 2026-10-14 — Thông báo Telegram cho tín hiệu soak và lệnh Binance Demo

Nhánh: `feature/slot-runtime`. Julius chọn: báo cả soak và Demo, hiện mức thoát thay cho target, khối lượng soak theo % túi, dùng bot của timer BTC paper.

- **Soak (`intraday/trade_alerts.py`, `setup2_store.run_setup2_observation`):** mỗi lần luật Setup-2 nổ và Jev trả lời, worker xếp một tin vào `notification_outbox` (một tin cho mỗi rule, thị trường và nến): coin, Spot LONG / Perp SHORT, Jev xác nhận hay từ chối, giá vào tham chiếu (giá đóng nến), khối lượng `1/3 túi × min(1; 0,02/ATR%)`, stop ban đầu (ATR14×3, tối đa 5%), mức kênh thoát 10 nến. Soak không đặt lệnh, không theo dõi thoát, và không áp giới hạn 3 vị thế, PnL gate hay xoay vòng.
- **Demo (`execution demo run --multi`):** sau mỗi chu kỳ, runtime đọc journal và gửi một lần cho mỗi sự kiện: vào lệnh (khối lượng khớp, giá trung bình, stop đầu tiên, mức kênh thoát), dời stop, thoát lệnh (lý do: kênh thoát, chạm stop, xoay vòng, giới hạn lỗ...) kèm PnL (Perp: PnL sàn đã trừ phí; Spot: PnL theo giá). Lần chạy đầu chỉ ghi mốc, không gửi lại lịch sử; gửi lỗi thì thử lại tối đa 5 lần. Bảng mới `execution_notifications` trong journal Demo.
- **Setup-2 không có chốt lời cố định:** tin nhắn hiện stop và mức kênh thoát thay cho target.
- **Deploy `236e767` và bật Telegram:** web trước, worker sau; 0 luồng chết. Julius nhờ điền `.env.intraday` trên VPS (sao lưu trước ở `~/.local/state/aigorithmic-trading/backups/env-intraday-20261014T045739Z.bak`): bot `@aigorithmic_trading_bot`, chat riêng của Julius, `TELEGRAM_ENABLED=true`; worker được tạo lại, tin thử đã gửi. Token đã xuất hiện trong hội thoại, nên thu hồi bằng `/revoke` và thay token mới.
- **Cấu hình:** dùng `TELEGRAM_ENABLED`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` của `.env.intraday`. Service `demo-execution` nhận đúng ba biến này (không nhận cả file env). Lỗi gửi hay lỗi đọc journal không làm dừng worker hay runtime.

## 2026-10-14 — Cổng G4: 10/10 route qua replay gate, soak 14 ngày bắt đầu; luồng nền tự khởi động lại

Nhánh: `feature/slot-runtime`, VPS chạy `fcd6136` (Julius duyệt push, deploy, đóng campaign ETH Spot và bắt đầu soak).

- **G4, replay gate (Julius duyệt):** backfill 10 route, tạo candidate, loại 7 candidate cũ còn mở (`--retire-open`), gắn funding đến 2026-10-14T00:00Z. Kết quả gate v2: 9/10 qua — ETH Perp +5,44%, NEAR Perp +5,03% / Spot +9,38%, SOL Perp +4,54% / Spot +1,53%, ZEC Spot +15,09%, ZEC Perp −1,69% (miễn trừ lãi ròng), HYPE Perp −4,26% (miễn trừ lãi ròng, cách sàn −5% còn 0,74 điểm), HYPE Spot 0 lệnh (miễn trừ số lệnh). Danh sách id trên VPS: `~/.local/state/aigorithmic-trading/adr007/`.
- **ETH Spot bị chặn:** campaign gate v2 cũ `cb13b4cb…` vẫn active dù candidate của nó đã bị loại. `4394683` sửa `--retire-open` để đóng campaign này và thêm `aigt setup2 retire-campaign` cho campaign mồ côi.
- **Deploy `4394683`:** web healthy, `doctor` ok, active set giữ nguyên. Sau khi worker lên, `_asset_rule_bootstrap_loop` vẫn chết vì `database is locked` ở một lần ghi thường (`claim_scheduler_run`), không phải ở khởi tạo schema. Đã khởi động lại riêng worker: 0 luồng chết.
- **ETH Spot:** Julius chạy `aigt setup2 retire-campaign --symbol ETH --market spot` (đóng `cb13b4cb…` lúc 04:06Z). Replay lại lần đầu `deferred` vì nến 4h mới nhất cũ 4,1 giờ (ngưỡng 14.700 giây); sau `setup2 backfill` (thêm 1 nến 4h, 16 nến 15m) thì qua: 36 lệnh, +1,98%, DD 5,26%, eval `8053585e…`.
- **Soak 14 ngày:** `start-soak` cho cả 10 route với eval replay đã qua, campaign bắt đầu 2026-10-14 04:09–04:10Z (sớm nhất có thể đánh giá: 2026-10-28 04:10Z). Kết quả từng route lưu ở `~/.local/state/aigorithmic-trading/adr007/soak-*.out` trên VPS.
- **Heartbeat soak:** đếm theo dòng `setup2_observations` của candidate, mỗi nến H4 một dòng, từ `started_at` của campaign (`provider_error`/`gate_error` không tính). 5 route Perp đã ghi nến 04:00Z (trước giờ bắt đầu soak nên chưa tính); 4 route Spot đầu tiên sẽ ghi từ slot 08:00Z.
- **HYPE Spot không ghi được heartbeat:** snapshot Spot 4h cần ít nhất 35 nến đóng cho mỗi khung 4h, 8h, 1d trên Binance Spot; HYPE Spot mới có 20 nến 1d (niêm yết 2026-09-24), nên route báo `error:StaleMarketData` mỗi slot cho tới khoảng 2026-10-29. Vì độ phủ heartbeat tính từ `started_at`, campaign HYPE Spot hiện tại sẽ không đạt 95% và phải bắt đầu lại sau mốc đó (hoặc sửa code). Jev cũng không xác nhận được lệnh Spot HYPE trong khoảng này. **Julius chọn:** không sửa code, bắt đầu lại soak HYPE Spot sau 2026-10-29 (sớm nhất xong khoảng 2026-11-12); các bước ghi trong checklist rollout, giai đoạn 4.
- **Deploy `fcd6136` (bản sửa luồng nền):** web trước, worker sau; `doctor` ok, 0 luồng chết, 0 `database is locked`.
- **Sửa:** mọi loop nền của worker chạy qua `_supervised_loop`; khi loop ném lỗi, worker ghi `{"background_loop": {"loop", "status": "restarting", "code"}}`, chờ 30 giây rồi chạy lại loop thay vì để luồng chết (`fcd6136`).

## 2026-10-14 — Deploy bản sửa khởi tạo database và LLM tự host; cổng G3 đã xong

Nhánh: `feature/slot-runtime`, VPS chạy `35b9aca`.

- **Cổng G3 (2026-10-13 14:00Z, Julius duyệt):** `active-set init` ghi version 1 (`c3d57eda19cd`), profile `setup2_slots_v1`, 5 coin với neo 2024-01-01 (HYPE 2025-06-01), proxy giá HYPE, ngưỡng 400 và hai miễn trừ. Từ 14:01:32Z vòng Jev 30 giây ghi `skipped_inactive` cho mọi coin.
- **LLM tự host:** profile LLM OpenAI/Anthropic-compatible nhận HTTP tới máy chủ cục bộ hoặc nội bộ (`localhost`, IP private, `host.docker.internal`); worker và admin có `host.docker.internal` trỏ về VPS; `aigt connect` in mã lỗi preflight. Container admin gọi được gateway ở `http://host.docker.internal:20128/v1` (401 khi không có key).
- **Sửa lỗi luồng nền chết khi khởi động:** mỗi `IntradayStore` ghi đều chạy lại DDL và migration nên các luồng của worker tranh khoá; nay schema chỉ khởi tạo một lần cho mỗi tệp trong mỗi tiến trình, có thử lại khi tiến trình khác giữ khoá. Sau deploy: 0 luồng chết, 0 `database is locked` ở worker và web (lần deploy trước có 2 đến 3 luồng chết mỗi lần khởi động).
- **Sau deploy:** `doctor` ok, `status` ok (paper, `execution_enabled: false`, schema 23), `/healthz` 200, active set giữ nguyên.
- **LLM đang dùng:** `anthropic/claude-sonnet-5.5` qua OpenRouter (profile `llm-openai-compatible-3ba62fbf`); chu kỳ phân tích đầu tiên sau deploy báo `schema_validation` (đầu ra của model không khớp schema). Không ảnh hưởng luật Setup-2 hay Jev.

## 2026-10-13 — Deploy mã ADR-007 lên VPS (cổng G2)

VPS `julius-vps`, clone `~/AIgorithmic-trading`, Julius duyệt push và deploy. Chỉ deploy mã: **chưa** `active-set init`, chưa soak, chưa đụng Demo.

- **Trước deploy:** VPS chạy `75e1d9e` (nhánh `fix/hyperliquid-frame-checksum`, không có trong `main` hay nhánh này). Đã gộp vào `feature/slot-runtime` (`846e744`, xung đột chỉ ở phần import; 1732 test qua, 1 lỗi có sẵn) để deploy không gỡ mất bản sửa nóng.
- **Trạng thái trước deploy:** worker soak, web healthy, schema v23, BTC paper không vị thế và đang tạm dừng, Demo chưa từng cấu hình.
- **Sao lưu:** `~/.local/state/aigorithmic-trading/backups/intraday-20261013T134644506616Z.sqlite3` (2,17 GB, SHA-256 `6d25bf92…caefeaa5`, `backup verify` ok). Chưa chép ra ngoài VPS.
- **Deploy:** checkout `846e744`, build lại `worker`, `web`, `admin`, rồi `up -d --wait worker web` lúc 2026-10-13 13:53Z. `doctor` ok, `status` ok (paper, `execution_enabled: false`, schema 23), `/healthz` và `/api/active-set` trả 200, active set rỗng (chế độ legacy).
- **Sự cố khi khởi động:** worker và web cùng khởi tạo database nên tranh khoá (`database is locked`). Web tự lên sau 2 lần khởi động lại; nhưng ba luồng nền của worker (`_news_loop`, `_asset_rule_bootstrap_loop`, `_gate_automation_loop`) **chết** vì mở database trước vòng lặp và không thử lại (lỗi có sẵn). Đã khởi động lại riêng worker khi web đã ổn: không còn luồng chết, vòng tin tức chạy lại.
- **Cần sửa sau:** mở database bên trong vòng lặp có thử lại (hoặc đặt busy timeout), và deploy theo thứ tự web trước, worker sau.
- **Ghi nhận:** Jev báo `insufficient_credits` (OpenRouter hết credit), nên mọi lượt soak `perp_intraday` là `provider_error`. Cần nạp credit trước cổng G4.

## 2026-10-13 — Ngoại lệ gate cho ZEC Perp và HYPE Perp (phụ lục ADR-007)

Nhánh: `feature/slot-runtime`. Gate theo từng route loại ZEC Perp (−1,7% trong 30 tháng) và HYPE Perp (−4,4% trong 13 tháng); Julius chọn giữ cả 10 route bằng một ngoại lệ có phạm vi.

- **Mã:** `CoinPlan.waive_net_return` (thị trường của coin), `active-set init/switch --waive-net-return COIN:MARKET`, `GateEvidence.net_return_waived`. Gate chỉ bỏ tiêu chí lãi ròng > 0, còn lỗ từ 5% trở lên vẫn bị loại (`NET_RETURN_WAIVER_FLOOR_PCT`); số lệnh, DD < 15%, độ phủ dữ liệu vẫn áp dụng; mỗi miễn trừ để lại `waived_nonpositive_net_return` trong báo cáo.
- **Kiểm thử:** 3 test mới (`tests/test_net_return_waiver.py`).
- **Chạy thử lần cuối với đúng lệnh triển khai:** cả 10 route qua gate, 3 route qua nhờ miễn trừ (ZEC Perp và HYPE Perp ở lãi ròng, HYPE Spot ở số lệnh). HYPE Perp chỉ cách chặn dưới 0,64 điểm.
- **Sửa diễn đạt:** bỏ cách viết "Spot ×5 và Perp ×3" như một cấu hình; cấu hình vẫn là 5 coin, cả hai thị trường.

## 2026-10-13 — Chạy thử quy trình triển khai trên database tạm (ADR-007)

Nhánh: `feature/slot-runtime`. Chạy đúng các lệnh của checklist trên một database tạm với dữ liệu công khai (chỉ đọc), không chạm VPS hay Demo. Kết quả: [checklist](docs/runbooks/slot-runtime-demo-rollout-checklist.md).

- **Lỗi tìm thấy và đã sửa:** backfill Spot HYPE bị từ chối vì nến M15 đầu tiên (11:00) muộn hơn nến H4 đầu (08:00) ba giờ; `setup2.sync` nay dò nến đầu tiên thật của Spot. Có test.
- **CLI:** `active-set init/switch --anchor [COIN=]YYYY-MM-DD` cho phép neo khác nhau theo coin.
- **Gate replay v2 trên dữ liệu thật:** với neo 2025-06-01 cho cả 5 coin, 4 trong 10 route bị loại (ETH Spot −2,6%, NEAR Perp −0,7%, ZEC Perp −2,8%, HYPE Perp −4,4%); với neo 2024-01-01 cho 4 coin cũ, 7 trong 8 route qua, chỉ ZEC Perp bị loại (−1,7%). HYPE Spot qua nhờ miễn trừ (0 lệnh).
- **Điều cần Julius quyết:** neo theo coin, và chấp nhận rằng ZEC Perp và HYPE Perp không kích hoạt được theo quy tắc hiện tại (không có đường ngoại lệ).
- **Quy trình Perp:** phải chạy `replay funding fetch` phủ đến ranh giới H4 gần nhất rồi truyền `--funding-id`, nếu không gate báo `funding_coverage_incomplete`.

## 2026-10-13 — Spot HYPE trên proxy giá Perp, ngưỡng 400 nến, miễn trừ gate lệnh (phụ lục ADR-007)

Nhánh: `feature/slot-runtime`. Julius muốn Spot HYPE vào Demo cùng các coin khác thay vì chờ tới 2027-01-02. Chi tiết và số đo: phụ lục của [ADR-007](docs/decisions/007-slot-rotation-five-coin-runtime.md).

- **Đo trước khi làm (dữ liệu công khai):** từ ≥ 400 nến làm nóng, bộ lọc xu hướng gần như không đổi (EMA200 trong 0,1%); Perp so với Spot HYPE lệch 0,057% trung bình, tối đa 0,125%; ghép giá Perp làm EMA200 lệch 0,02% (trung vị); volume Spot chỉ bằng ~5% Perp nên **volume và volume profile không bao giờ được ghép**.
- **Active set (`CoinPlan`):** thêm `warmup_bars` (400 đến 600, mặc định 600), `spot_price_proxy_until` và `waive_min_trades`; payload cũ giữ nguyên từng byte. `slots_plan()` và `active-set init/switch --warmup COIN=N --spot-proxy COIN=ISO --waive-min-trades COIN:spot`.
- **Chuỗi ghép (`proxied_bars`):** nến H4 Spot trước mốc lấy OHLC từ Perp, volume 0; M15 không ghép. Bộ đánh giá không thể sinh lệnh vào khi cửa sổ volume MA20 chạm nến proxy (`proxy_volume_warmup`) hoặc profile chưa đủ 20 ngày Spot thật (`profile_incomplete`). `sync` chỉ tải Spot từ mốc, trail stop và replay dùng cùng chuỗi.
- **Gate:** `GateEvidence.required_bars`, `min_trades_waived`; cửa sổ replay bắt đầu tại neo + ngưỡng của coin; với miễn trừ, 0 lệnh qua, 1 đến 5 lệnh vẫn bị loại nếu lãi ròng ≤ 0 hoặc DD ≥ 15%; mọi miễn trừ để lại `waived_*` trong số liệu và nhãn `setup2_spot_price_proxied_from_perp`. Miễn trừ tự hết hiệu lực khi Spot đủ ngưỡng nến thật.
- **Kiểm thử:** 11 test mới (`tests/test_spot_proxy.py`); toàn bộ suite 1515 qua, 1 lỗi có sẵn.
- **Đo trên dữ liệu công khai:** bộ đánh giá live trên chuỗi Spot HYPE ghép (2.882 nến proxy + 116 nến Spot) không có lệnh vào Spot; nến mới nhất không qua bộ lọc EMA200, EMA50, DMI, volume (HYPE đang dưới các đường trung bình); Perp short đã kích hoạt 38 lần, lần gần nhất 2026-10-12 20:00Z.
- **Điều cần nói thẳng:** replay của Spot HYPE gần như không có lệnh (volume và profile chỉ có từ 2026-09-24, lệnh vào chỉ có thể từ 2026-10-14 08:00Z), nên route qua gate bằng bằng chứng quy trình (soak 14 ngày, heartbeat, giới hạn rủi ro) chứ không bằng lệnh đóng như các route khác. Gỡ proxy khi đủ 600 nến Spot thật (2027-01-02).

## 2026-10-13 — Runtime slot 1/3 túi, 5 coin (ADR-007)

Nhánh: `feature/slot-runtime`. Julius chốt thiết kế 4 coin, slot 1/3 túi, Donchian 25 và yêu cầu xây hệ thống, thêm HYPE thành 5 coin, triển khai lên Binance Demo, Jev vẫn xác nhận. Quyết định: [ADR-007](docs/decisions/007-slot-rotation-five-coin-runtime.md). **Đã làm xong phần mã và kiểm thử; chưa push, chưa deploy, chưa chạm Demo/VPS.** Checklist triển khai: [slot-runtime-demo-rollout-checklist](docs/runbooks/slot-runtime-demo-rollout-checklist.md).

- **Profile quy tắc `setup2_slots_v1`:** Donchian 25/10 (khoá trong hợp đồng quy tắc), stop chặn 5%, evaluator live nhận `profile`. `setup2_v1` và hash quy tắc cũ không đổi.
- **Tập coin hoạt động:** tối đa 5 coin cho profile mới (3 cho `setup2_v1`), split 50/50, mỗi coin một chế độ; `slots_plan()` và `active-set init --profile setup2_slots_v1 --coin COIN:MODE --anchor YYYY-MM-DD`. HYPE chạy `perp` cho tới khi đủ nến H4 Spot.
- **Phân bổ:** `DemoAllocation.profile = setup2_slots_v1`, `slot_cap()` = 1/3 vốn thực hiện của túi × hệ số ATR, chặn bởi vốn rảnh của túi; stop 5% cho cả Spot lẫn Perp; chính sách cổng bỏ giới hạn net-delta một-parent (cạnh túi 50% và 3 slot đã đủ).
- **Vòng lặp thực thi (`_slots_cycle`):** mỗi nến H4 thu quan sát của mọi route, thoát Donchian trước, xếp hạng ứng viên một lần cho cả danh mục (cổng PnL, giới hạn 3 vị thế, một vị thế mỗi coin, xoay vị thế hơn > 5 điểm ADX), **chỉ đóng vị thế yếu sau khi ứng viên mới đã được luật và Jev xác nhận**; ghi `slot_selection` vào journal.
- **Mã dùng chung với nghiên cứu:** logic chọn lệnh tách thành `replay_v2/donchian_rotation_select.py` (cùng `adx_of`, `volume_ratio`, `trend_strength`); engine nghiên cứu và runtime gọi cùng một hàm.
- **Hồi quy nghiên cứu (cửa sổ 2026):** 18/18 ca `rotation` giống từng byte `result_id` so với commit ngay trước refactor; 8/8 ca `capacity` giống lần chạy trước, cả sau khi tách hàm trợ giúp; ca `rotation-window` chỉ khác ở trường `slot_divisor` thêm vào `variant`.
- **Kiểm thử:** 11 test runtime mới (xếp hạng, cổng PnL, xoay vị thế và Jev loại, tái đầu tư, profile quy tắc), 5 test active set và phân bổ, 5 test profile quy tắc và parity với nghiên cứu cho cửa sổ 25.
- **Stage 0 (5 coin trên 3 vị thế, BTC thay HYPE, 8 ca, đều tái hiện ca 4 coin):** Donchian 25, cả cửa sổ: +96,6% / DD 11,4% / 24 lần xoay vị thế so với +103,3% / 9,8% / 8 lần của 4 coin; lợi nhuận trên DD 8,5 so với 10,5. 2026 không phân biệt được (+22,0% so với +22,3%). Thêm coin thứ năm làm số lần xoay tăng gấp ba và giảm khoảng 2 đơn vị lợi nhuận/DD ở cửa sổ đầy đủ: là một lựa chọn, không phải cải thiện. BTC không phải HYPE.
- **Quyết định về coin thứ năm (2026-10-13):** Julius giữ HYPE sau khi thấy kết quả Stage 0. Demo liệt kê cả 5 coin ở Spot và USD-M Perp (`TRADING`); tín hiệu Spot HYPE chỉ có từ 2027-01-02 (600 nến H4 từ 2026-09-24), nên HYPE chạy `perp` trước rồi chuyển sang `both` bằng `active-set switch`.
- **Còn lại trước khi triển khai (Julius duyệt):** sao lưu và deploy, `active-set init`, soak, cấu hình và kích hoạt Demo.
- **Chưa kiểm chứng:** HYPE (không có lịch sử Spot, không có số liệu nghiên cứu), hành vi thật của Binance Demo với ZEC và HYPE, và chênh lệch live so với replay do Jev và khớp lệnh.

## 2026-10-13 — Giới hạn mỗi túi bằng số coin (đăng ký trước)

Nhánh: `research/rotating-slots`. Nghiên cứu offline; thiết kế và dự đoán nằm trong [rotating-positions-research](docs/rotating-positions-research.md).

- **Lý do:** lần chạy trước cho thấy giới hạn 3 vị thế mỗi túi không bao giờ chạm trần; với 3 coin nâng giới hạn lên bằng số coin chính là ca đã chạy, nên chỉ rổ 4 coin có gì mới (4 mỗi túi, tối đa 8 vị thế).
- **Thêm:** `slot_divisor` (mặc định `max_positions`, ca cũ không đổi), universe `rotation-capacity` (4 thiết kế × chi phí thường/×2) và verdict `evaluate_capacity`. Slot tính bằng 1/`max_positions` nên nâng giới hạn 3 → 4 cũng thu nhỏ vị thế từ 1/3 xuống 1/4 túi; `slot_divisor` tách hai tác động.
- **Kiểm thử:** 3 test mới; 252 test Donchian hiện có vẫn qua.
- **Kết quả (4 coin có ZEC, 16 ca cả hai cửa sổ, đều tái hiện):**
  - Nâng giới hạn lên 4 mỗi túi với slot giữ 1/3 chỉ thêm +1,5 điểm lợi nhuận và +1,1 điểm DD (+90,2% / 10,5% so với +88,7% / 9,4%); 2026 hoà (+22,9% so với +22,8%).
  - Yêu cầu của Julius (4 mỗi túi, slot 1/4): +68,8% / DD 8,2%, thấp hơn đối chứng vì vị thế nhỏ hơn. Chỉ đổi cỡ (3 mỗi túi, slot 1/4): +68,9% / 7,3%, cùng lợi nhuận trên DD với đối chứng (9,50 so với 9,44).
  - Cỡ slot là tham số thực sự; giới hạn bằng số coin biến thiết kế thành trọng số cố định theo coin (không xoay vị thế). Giữ 3 vị thế mỗi túi, slot 1/3.
  - Hai dự đoán trượt: lợi nhuận của `THIRD` (dự +95–115%, ra +90,2%) và `4S` (dự trong ±15%, ra −22%).

## 2026-10-12 — Donchian 25/20 và giới hạn 3 vị thế mỗi túi (đăng ký trước)

Nhánh: `research/rotating-slots`. Chỉ nghiên cứu offline; thiết kế, 30 ca mỗi cửa sổ, đối chứng và dự đoán nằm trong [rotating-positions-research](docs/rotating-positions-research.md).

- **Thêm:** cửa sổ vào Donchian 25 và 20 (thoát giữ 10) và `limit_scope='sleeve'` (tối đa 3 vị thế mỗi túi, một coin được giữ cả Spot lẫn Perp); universe `rotation-window`; verdict `evaluate_window` kèm số vị thế mở đồng thời tối đa.
- **Code:** `OBSERVED_PAIRS` thêm `(25,10)`; `RotationConfig.entry_window` và `limit_scope`; xoay vị thế chỉ trong cùng túi khi giới hạn theo túi. Cấu hình cũ giữ nguyên kết quả.
- **Kiểm thử:** 5 test mới (cửa sổ 25, giới hạn theo túi, 6 vị thế, xoay trong cùng túi, verdict cửa sổ).
- **Kết quả (60 ca, cả cửa sổ và chỉ 2026, đều tái hiện):**
  - Giới hạn 3 vị thế mỗi túi cho kết quả **giống hệt** giới hạn 3 tổng ở mọi ca; số vị thế mở đồng thời tối đa luôn là 3 (không bao giờ 4 hay 6), vì các coin chạy cùng chiều nên tín hiệu bị chặn luôn nằm cùng túi.
  - Cửa sổ vào ngắn hơn không phải cải thiện: 3 coin +73,2% → +73,1% (25) → +68,6% (20); 4 coin có ZEC +88,7% → +103,3% → +94,2%, nhưng đó là do ZEC và NEAR, còn ETH kém dần (Perp ETH +61 → +27 → +2). 2026 không phân biệt được (±1,3 điểm).
  - Số lệnh chỉ tăng 2–5%, thấp hơn dự đoán 15–40%.
  - Kết luận: giữ cửa sổ vào 30; giới hạn theo túi không thêm gì so với 3 vị thế tổng.

## 2026-10-12 — Nghiên cứu xoay vị thế: 2 túi, cổng PnL, stop 5%

Nhánh: `research/rotating-slots`. Chỉ nghiên cứu offline, không đụng runtime hay VPS. Thiết kế đăng ký trước tại [rotating-positions-research](docs/rotating-positions-research.md).

- **Yêu cầu của Julius:** thêm HYPE, 50/50 Spot/Perp hai túi riêng, tái đầu tư, bỏ trọng số cố định theo coin, tối đa 3 vị thế mở, vị thế mới phải đóng một vị thế yếu nhất, chỉ vào lệnh khi sổ lệnh đang lãi, stop 5% mỗi vị thế.
- **Phát hiện khi dựng thử nghiệm:** với 3 coin và một vị thế mỗi coin, giới hạn 3 vị thế không bao giờ chặn được, nên xoay vị thế chỉ có nghĩa với coin thứ 4. HYPE chưa có lịch sử trong dữ liệu đã đóng băng (Perp từ 2025-05-30, Spot từ 2026-09-24) nên dùng ZEC làm coin đại diện thứ 4.
- **Code:** `RotationBook`, `RotationConfig`, nhánh xoay vị thế trong engine, stop cứng trong `ATRTrail` (mặc định không đổi), verdict `donchian_rotation_verdict`, universe `rotation`. 10 test mới; 243 test Donchian liên quan vẫn qua.
- **18 ca**, tiêu chí cơ học và dự đoán đã ghi trước khi chạy. **Kết quả:** không ca nào qua tiêu chí.
  - Thiết kế đúng ý (không giới hạn vốn mỗi vị thế): +152,7% nhưng DD 19,2% và 20 lần dừng lỗ ngày; bản 4 coin +153,5% / DD 21,6% / 28 lần dừng.
  - Bản slot 1/3 túi: +73,2% / DD 9,9% (3 coin) và +88,7% / DD 9,4% (4 coin).
  - Lợi nhuận trên mỗi đơn vị DD gần như bằng nhau (7,1 đến 8,0): phần tăng thêm là do cỡ lệnh, không phải edge mới.
  - Xoay vị thế gần như không hoạt động (6 lần trong 4,75 năm); giới hạn 3 vị thế làm giảm lợi nhuận (+184,7% khi bỏ giới hạn).
  - Stop 5% giúp (+152,7% so với +131,3% khi bỏ), cổng PnL gần như trung tính.
  - Dự đoán sai một điều: `ROT` không thua `FIXED5050` về lợi nhuận mà gần gấp đôi.
- **Chạy lại chỉ cửa sổ 2026 (tài khoản mới 1.000 USDT):** `ROT` +18,0% / DD 11,4% (50/50 cố định +14,2% / 10,3%), `ROT-SLOT` +15,5% / 9,5%, 4 coin `ROT` +34,0% / DD 14,6%, `ROT-SLOT` +22,8% / 9,4%. Ba ca qua tiêu chí cơ học, nhưng đây là một đoạn của cửa sổ đã thấy, chênh lệch nhỏ so với nhiễu (53–82 lệnh), stop 5% từ có ích thành tốn 7–10 điểm, và phần hơn ở 4 coin là của ZEC. Xoay vị thế chỉ hoạt động 1–2 lần.
- **Sự cố:** ổ đĩa đầy 100% làm study dừng ở ca 9; đã chạy `uv cache clean` (Julius duyệt) và `--resume`.

## 2026-10-12 — H1-SLOWREGIME: bộ lọc chế độ chậm tính hoàn toàn trên H1 (đăng ký trước)

Nhánh: `research/h1-slowregime` (từ `research/wide-basket-slots`). Julius hỏi liệu mọi ứng viên H1 đều tính chỉ báo ở H1. Không: `H1-REGIME` lấy EMA200/50 và ADX/DMI14 từ nến H4 (ứng viên lai). Julius chọn thêm bản thuần H1.

- **Ứng viên:** EMA800/EMA200 và ADX/DMI56 trên nến H1 (cùng độ dài thời gian với EMA200/50 và ADX14 của H4); Donchian, volume, profile và stop giữ như H1 baseline. Ngưỡng ADX 20 kế thừa, không tối ưu. Dự đoán ghi trước: ít lệnh hơn và tốt hơn baseline, gần bản lai, dưới H4.
- **Code:** `adx_period` trong `indicator_series` (mặc định 14, ATR và volume MA không đổi), cặp EMA (200,800), `warmup_hours` theo cấu hình, `build(..., warmup_hours)` (các chuỗi khác giữ warmup cố định), cắt dữ liệu theo từng cấu hình trong `run_study`, `decode_bundle(m15_warmup)`, collector `--m15-warmup-hours`, `--only` và `--universe h1-slowregime`.
- **Phạm vi:** chỉ cửa sổ 2026 (manifest 2022 chỉ có 484 giờ M15 trước ngày bắt đầu): rổ ETH-NEAR-SOL 8 ca và rổ 30 coin 6 ca.
- **Kiểm thử:** 6 test mới (suite 1457 pass).
- **Kết quả (commit `852787d`):** rổ ETH-NEAR-SOL 2026 — `H1-SLOWREGIME` −0,73%, DD 4,69%, 60 lệnh, không lệnh nào giữ quá 48 giờ, trượt 4/5 tiêu chí; bản lai `H1-REGIME` +8,12%, H4 +14,23%. Rổ 30 coin — `H1-SLOWREGIME` −6,52%, DD 28,17%, 347 lệnh (bản lai −6,63%, 832 lệnh), vẫn trượt mọi tiêu chí, H4 +23,40%. Dự đoán "tốt hơn baseline, gần bản lai" chỉ đúng ở phần ít lệnh hơn; trên rổ 3 coin nó kém baseline. Kết luận: lợi thế của bản lai không đến từ "chế độ chậm" mà từ nến H4; mọi ca H1 vẫn thoát bằng kênh và stop H1 nên mất đuôi dài.
- **Ghi chú kỹ thuật:** lần chạy đầu lệch dataset checksum của ca H1 vì metadata tái tạo H4 đếm trên lịch sử dài hơn (kết quả không đổi); đã sửa và chạy lại, 6 ca tham chiếu trùng từng byte. Ở rổ rộng, chỉ config payload có thêm hai khoá mặc định nên config checksum khác, còn summary và ba journal trùng từng byte.

## 2026-10-11 — Họ Setup-2 trên rổ 30 coin, 5 slot chung (đăng ký trước)

Nhánh: `research/wide-basket-slots` (từ `research/setup2-h1`). Julius muốn thử các ứng viên trên rổ 30 coin top, tối đa 5 vị thế mở cùng lúc, thêm bước chọn coin. Chi tiết: [wide-basket-slots-research](docs/wide-basket-slots-research.md).

- **Thiết kế:** cửa sổ 2026-01-01 đến cuối dữ liệu mới thu (RAM không đủ cho 2022–2026 với 30 coin); top 30 theo khối lượng USDT-M 90 ngày, có cả Spot lẫn Perp (thiên lệch sống sót được ghi rõ); 5 slot chung, mỗi slot 20% quỹ vốn thực hiện nhân hệ số ATR, Spot long và Perp short đếm chung, mỗi coin một vị thế, không chia 60/40.
- **Chọn coin:** xếp hạng ADX rồi tỷ lệ volume; đối chứng `H4-random` chọn ngẫu nhiên có seed. **Jev không được phát lại** vì trạng thái của Jev dùng sổ lệnh, OI, long/short, sentiment mà Binance không có lịch sử; nhật ký `selection` ghi mọi ứng viên và điểm để ghép câu trả lời shadow về sau.
- **Code:** `SlotBook` (một quỹ vốn, slot cố định), cổng slot trong engine (gom hai thị trường, xếp hạng, chặn `slot_limit`, một coin một slot), `WideSlotConfig`/`wide_cases` (14 ca), `donchian_wide_data` (chọn rổ, thu từng coin nghiêm ngặt, manifest), `--universe wide-slots`, verdict kèm thống kê slot và tập trung coin. Đường mặc định không đổi.
- **Kiểm thử:** 13 test mới; toàn bộ suite pass (1451).
- **Kết quả (commit `bad14c1`, report `donchian-wide-slots-20261011`; dữ liệu 01/01–11/10/2026, thu hôm nay, loại QNT và LSK do lệch H4/M15):** H4 xếp hạng ADX +23,40% nhưng **DD 30,59%** (rổ 3 coin: +14,23%, DD 10,46%), 328 lệnh, 7 lần dừng lỗ ngày, chi phí ×2 còn +9,85%. Chọn ngẫu nhiên (10 seed bổ sung, không đăng ký trước): trung bình +6,2%, độ lệch chuẩn 7,1, từ −3,7% đến +18,3%; xếp hạng ADX cao hơn cả 10 lần rút (khoảng 2,4 độ lệch chuẩn) nhưng DD không đổi (~30–33%). Cả năm biến thể H1 trượt mọi tiêu chí (−32% đến +4%, DD 36–52%). Trên 30 coin, 15 coin dương, 5 coin tốt nhất kiếm +428 trên tổng +234; tài khoản +236 tới 22/05 rồi đi ngang.
- **Kết luận:** H1 đóng. Rổ rộng chưa dùng được vì DD; điều đáng thử tiếp là xếp hạng với slot nhỏ hơn hoặc trần phơi nhiễm để đưa DD vào giới hạn. Jev vẫn chưa được đo, chỉ có nhật ký `selection` để ghép shadow sau.

## 2026-10-11 — Chạy lại họ H1 trên cửa sổ 2026

Nhánh: `research/setup2-h1`. Julius muốn xem riêng dữ liệu từ đầu 2026. Thêm `donchian_adx_study --start` (cắt nến đã đóng băng sau khi giải mã, tài khoản mới 1.000 USDT, các dòng trước ngày bắt đầu chỉ làm nóng chỉ báo) và quy tắc chia nửa cửa sổ ở điểm giữa (2026-05-18) cho cửa sổ ngắn. Dữ liệu dừng ở 2026-10-02 (hết manifest), không phải hôm nay.

- **Kết quả (commit `1c67af1`):** H4 +14,23%, DD 10,46%, 65 lệnh, chi phí ×2 còn +11,38%. `H1-ADX30` +15,50%, DD 9,80%, 140 lệnh, qua cả năm tiêu chí theo công thức. `H1-REGIME` +8,12% nhưng DD thấp nhất (7,36%) và Spot, Perp đều dương. `H1-GEOM` +4,63% với DD 21%; baseline +8,12%; EMA 50/20 +6,84%.
- **Không đổi gì:** ADX30 hơn H4 12,7 USDT trong khi sai số chuẩn của chênh lệch cỡ 150 USDT; nó là một trong năm ứng viên chọn sau khi toàn cửa sổ cho −13%, kiếm tiền bằng lệnh ngắn (chỉ 2 lệnh giữ > 48 giờ, so với 38 của H4), 2,5 lần chi phí. Giữ nguyên ADR-005 (H4).

## 2026-10-11 — Vòng hai H1: bốn ứng viên một khái niệm (đăng ký trước)

Nhánh: `research/setup2-h1`. Sau khi H1 baseline thua, Julius muốn thử EMA 50/20 và thêm các setup H1 khác. Chi tiết, dự đoán và tiêu chí: mục "Second round" trong [setup2-h1-research](docs/setup2-h1-research.md).

- **Chẩn đoán từ nhật ký lệnh:** lãi của Setup-2 nằm ở đuôi dài. H4 có 240 lệnh giữ > 48 giờ (giá gộp +1.968, phí 113) còn 183 lệnh ≤ 48 giờ mất 1.030; H1 chỉ có 36 lệnh sống quá 48 giờ vì stop ATR×3 trên H1 chỉ bằng nửa khoảng giá của H4. Cỡ EMA không phải đòn bẩy chính.
- **Bốn ứng viên, mỗi cái đổi một khái niệm so với H1 baseline:** `H1-GEOM` (stop ATR×6, thoát Donchian 40), `H1-REGIME` (EMA200/50 + ADX20 lấy từ nến H4 đã đóng), `H1-EMA50-20` (câu hỏi của Julius), `H1-ADX30`. Dự đoán ghi trước, năm tiêu chí giữ nguyên.
- **Code:** `ATRTrail(multiple=3)`, `indicator_series`/`entry_filters` nhận cặp EMA (khoá cũ giữ nguyên với cặp mặc định), `stop_atr_multiple`, cặp thoát (30,40), cổng chế độ H4 gate theo nến đã đóng, chuẩn bị đặc trưng lười theo cấu hình, `--universe h1-family`, verdict theo họ kèm hồ sơ thời gian giữ lệnh.
- **Kiểm thử:** 11 test mới; toàn bộ suite pass (một lần `test_compact_ab` fail rồi pass khi chạy lại, không tái hiện được trong 8 lần chạy riêng).
- **Kết quả (commit `9b85a29`, report `donchian-setup2-h1-family-20261011`):** ca H4 và H1 baseline trùng từng byte với các report trước. Không ứng viên nào đạt. `H1-GEOM` tốt nhất: +63,26% (H4 +81,75%), DD 24,70%, 1.205 lệnh, chi phí 703 so với 201 USDT, chi phí ×2 còn −1,57%; số lệnh giữ > 48 giờ tăng từ 36 lên 500. `H1-REGIME` −17,21%, `H1-ADX30` −12,96%, `H1-EMA50-20` −33,70% (đúng dự đoán: EMA nhỏ hơn không giúp, thậm chí tệ hơn). **Kết luận: giữ H4.** Giả thuyết chẩn đoán (stop quá sát cắt đuôi dài) đúng, nhưng H1 vẫn cần gấp ba số lệnh nên chi phí quyết định.

## 2026-10-11 — Nghiên cứu Setup-2 ở khung H1 (đăng ký trước)

Nhánh: `research/setup2-h1` (từ `feature/three-coin-runtime`). Julius hỏi có nên đổi sang khung nhỏ hơn khi dữ liệu ít. Đã trả lời là không đổi Setup-2 vì lý do đó; Julius chọn nghiên cứu H1 như một ứng viên riêng. Chi tiết và tiêu chí: [setup2-h1-research](docs/setup2-h1-research.md).

- **Không đụng gì đang chạy:** ADR-005 và rule hash `bf6fccc9…`, runtime, VPS, pin `replay-v2.2` / `gate-v2.1` giữ nguyên.
- **Ứng viên:** đúng rule Setup-2 nhưng mọi cửa sổ đếm theo nến H1 (Donchian 30/10, EMA200/50, ADX14, volume MA20, profile 120 nến). Bốn ca: H4 đối chứng và H1, mỗi ca ở chi phí thường và gấp đôi.
- **Dữ liệu:** nến H1 gộp chính xác từ M15 đã lưu, không tải thêm, không tạo nến giả. Gộp M15 thành H4 trùng native H4 ở mọi nến Spot đầy đủ; Perp lệch ở bốn cửa sổ đã kiểm toán, ngoài đó thì dừng. Giờ ngừng giao dịch ngày 2023-03-24 vẫn để trống.
- **Tiêu chí ghi trước khi chạy** và do `donchian_h1_verdict` tính: lợi nhuận không thấp hơn H4, DD dưới 15%, chi phí gấp đôi vẫn dương, Spot và Perp đều dương, cả hai nửa cửa sổ (chia tại 2024-10-29) đều dương. Đạt cũng chỉ là căn cứ đề xuất paper-test prospective mới, không bật gì.
- **Code:** `bar_interval` trong engine (mặc định H4 giữ nguyên từng byte, config và `result_id` cũ không đổi), `donchian_hourly.py`, `donchian_h1_verdict.py`, `--universe h1`.
- **Kiểm thử:** 15 test mới; 184 test `donchian_*` và parity Setup-2 vẫn pass.
- **Kết quả (commit `b42b56c`):** ca đối chứng H4 trùng từng byte với kết quả đã công bố (+81,75%, DD 11,58%, 423 lệnh). H1 thua: −26,89%, DD 45,92%, 1.725 lệnh, phí+trượt giá 515 so với 201 USDT; gấp đôi chi phí còn −64,97%. Giá gộp vẫn dương (+250) nhưng bị chi phí nuốt; Spot −93, Perp −176. Cả 5 tiêu chí đều trượt. **Kết luận: giữ H4**, không đề xuất ADR-007.

## 2026-10-10 — Công cụ A/B offline: Jev nhận số hay nhận ký tự

Nhánh: `feature/three-coin-runtime`. Câu hỏi của Julius sau khi đọc bài của alex saint: có nên đổi trạng thái Jev từ số sang ký tự. Chi tiết: [compact-state-ab](docs/compact-state-ab.md).

- **Phát hiện:** hệ thống đã có `compact_v1` (12 token danh mục) và A/B shadow, nhưng **VPS chưa từng chạy một lần gọi compact nào** (115.141 lần numeric, 0 lần compact), vì nhánh shadow nằm ở paper worker còn VPS chạy chế độ soak.
- **Vì sao không chờ A/B trực tiếp:** với Setup-2, Jev chỉ được gọi vài lần mỗi tuần, nên 1.000 cặp sẽ mất hàng chục năm. Chi phí cũng không còn là lý do để đổi.
- **Công cụ mới `intraday/replay_v2/compact_ab.py`:**
  - Chạy lại compact và numeric trên snapshot đã lưu, cùng model, cùng thời điểm, rồi chấm bằng kết quả giá 15 phút đã trưởng thành.
  - Mở source ở chế độ chỉ đọc; cuộc gọi ghi vào scratch store riêng.
  - Có `--dry-run`, `--max-calls`, `--max-usd`.
  - Dùng đúng tiêu chí sẵn có (độ chính xác không kém quá 2pp, Brier không kém quá 0,02, giảm ≥15% token, ≥1.000 cặp), bỏ tiêu chí 14 ngày vì chỉ áp cho lúc thu thập; thêm điều kiện khoảng tin cậy bootstrap.
- **Chưa chạy thật:** cần nạp credit OpenRouter và chạy trong container admin của VPS (có secrets); tốn khoảng $0,2–0,3. Chạy thật chờ Julius duyệt.
- **Kiểm thử:** 7 test mới.

## 2026-10-10 — Runtime 3 coin, đợt C: Perp short theo Setup-2

Nhánh: `feature/three-coin-runtime`. Đã làm xong, chưa deploy.

- **Đổi so với kế hoạch: không nâng schema lên v24.**
  - Rule Perp Setup-2 nằm trong scope `perp_intraday` có sẵn, với `entry_profile=setup2_v1`, giống cách làm cho Spot.
  - Tập coin vẫn kiểm soát qua scope logic `perp_4h`, nên vòng Jev 30 giây vẫn tắt.
  - Không cần rebuild bảng nào, mọi container vẫn đọc v23. Lý do và các đánh đổi ghi trong ADR-006.
- **Gate:** `perp_setup2_entry` chỉ mở short khi rule bắn tín hiệu và Jev trả lời SELL hoặc STRONG_SELL. `rule_allows_answers` cũng yêu cầu SELL. LLM không được chọn profile này.
- **Worker:** job `asset_perp_setup2_<coin>` chạy mỗi 4 giờ, chỉ cho coin đang hoạt động hoặc coin trong watch. Mỗi lần đồng bộ nến Perp, đánh giá một nến, và chỉ gọi Jev khi coin đang hoạt động và setup đạt đủ filter.
- **Replay:** nhánh short có trailing stop tối đa 10% phía trên giá vào, tính funding ở mức nến, bắt buộc Isolated 1x, dừng ở 3% hoặc 15%.
- **Demo:**
  - Short Setup-2 cần Isolated 1x và thoát theo Donchian, không dùng tín hiệu Take Profit của Jev.
  - Trailing stop trên sàn được thay theo thứ tự **đặt stop mới rồi mới hủy stop cũ**, và chỉ được siết lại.
- **Kiểm thử:** 6 test mới. 1415 pass, 1 fail có sẵn từ trước.

## 2026-10-10 — Runtime 3 coin, đợt B: Spot chạy theo Setup-2

Nhánh: `feature/three-coin-runtime`. Đã làm xong, chưa deploy. Theo [ADR-006](docs/decisions/006-three-coin-runtime.md).

- **Evaluator live `intraday/setup2.py`:**
  - Dùng lại nguyên các hàm chỉ báo, Donchian, bộ lọc và volume profile của phần research, cùng `ATRTrail`.
  - Chỉ báo tính trên chuỗi H4 bắt đầu từ một mốc cố định (anchor).
  - Trailing stop tính lại hoàn toàn từ nến đã lưu, giới hạn 10% so với giá vào, nên khởi động lại không làm mất trạng thái.
  - Test parity trên dữ liệu ngẫu nhiên: live trùng research ở mọi chỉ báo, profile, cờ, bộ lọc, hệ số size và mọi lần cập nhật trailing stop, cho cả long lẫn short.
- **Profile rule `setup2_v1`:**
  - Thêm vào `SpotRuleParameters`. Mặc định không ghi ra JSON, nên hash của rule cũ không đổi.
  - Khóa ở 30/10 và ATR14. LLM không được chọn profile này; đường Donchian cũ từ chối chạy rule Setup-2.
- **Dữ liệu và soak:**
  - `setup2_store`: lưu nến Spot/Perp H4/M15 (không cho sửa), mỗi nến H4 có một dòng observation làm heartbeat.
  - Jev chỉ được gọi khi coin đang hoạt động và setup đạt đủ filter.
  - Lệnh `aigt setup2 backfill|evaluate|candidate`.
- **Replay và gate v2:**
  - Engine có nhánh Setup-2: một lượt tính chỉ báo, quy mô 1/3 của phần 60/40, trailing stop giới hạn 10%, dừng ở 3% hoặc 15%.
  - Gate: cần 600 nến từ anchor và DD < 15%; soak cần 14 ngày và heartbeat ≥95%, không cần 6 setup; không so champion khi khác profile.
  - Không đổi pin version.
- **Thực thi Demo:**
  - Tín hiệu Spot đọc từ nến đã lưu.
  - Stop gốc trên sàn là trailing stop, chỉ siết lại. Thiếu dữ liệu thì giữ stop cũ, hoặc stop 10%.
- **Danh sách `watch`:** tối đa 3 coin ứng viên chạy soak theo rule, không gọi Jev và không trade, để có bằng chứng khi đổi coin. Thêm bằng `aigt active-set switch --watch COIN`.
- **Kiểm thử:** 1409 pass, 1 fail có sẵn từ trước.

## 2026-10-10 — Runtime 3 coin, đợt A: tập coin hoạt động và giới hạn theo tỷ lệ chia vốn

Nhánh: `feature/three-coin-runtime`. Quyết định ghi tại [ADR-006](docs/decisions/006-three-coin-runtime.md): xây runtime trước khi paper-test ADR-005 có kết quả. Làm 3 đợt A → B → C, mỗi đợt chỉ deploy khi Julius duyệt riêng.

### Đợt A (đã làm, chưa deploy)

- **Tập coin hoạt động** (`intraday/active_set.py`, `aigt active-set show|init|switch`):
  - Mỗi lần đổi là một version mới, chỉ ghi thêm, có actor và reason. Mỗi coin có chế độ, tỷ trọng Spot/Perp và mốc tính chỉ báo; tỷ lệ chia vốn 60/40.
  - **Chưa có version nào thì hệ thống chạy y như cũ.**
- **Khi đã có version:**
  - Chỉ scope theo rule của 3 coin được gọi Jev.
  - Coin khác, kể cả BTC, vẫn ghi dữ liệu thị trường và ghi `skipped_inactive` cho mỗi slot H4.
  - `perp_intraday` (Jev 30 giây) và `spot_daily` tắt cho mọi coin.
  - Auto-proposal LLM, review độ tin cậy hằng tuần và gate tuần bỏ qua các scope không hoạt động.
- **Điều kiện để đổi tập coin:**
  - Demo phải đang tạm dừng và không còn vị thế; BTC paper cũng phải tạm dừng và không còn vị thế.
  - Coin mới cần ≥600 nến H4 liền mạch và bằng chứng gate Setup-2. Bằng chứng này sẽ có từ đợt B và C, nên hiện tại chưa thêm được coin mới.
- **Giới hạn rủi ro theo tỷ lệ chia vốn:**
  - Cấu hình Demo `profile=setup2_v1` gắn với một version của tập coin; tạo bằng `execution demo configure --from-active-set`.
  - Gross 100%, Perp 40%, margin 40% ở 1x; dừng khi lỗ 3%/ngày hoặc drawdown 15%.
  - Khi tập coin đổi version, Demo tự tạm dừng việc vào lệnh mới; stop bảo vệ vẫn giữ.
  - Cấu hình legacy giữ nguyên giới hạn cũ và JSON cũ.
- **Web và readiness:**
  - Thêm `/api/active-set`; `/api/portfolio` lấy limits từ tập coin; `/api/assets` có trường `active_set`.
  - Readiness có thêm `active_set` cùng các nhãn `history_not_ready` và `no_backtest_evidence`.
- **Kiểm thử:** 16 test mới. 1397 pass, 1 fail có sẵn từ trước (`test_perp_collection_migration`).

## 2026-10-09 — Chọn rổ ETH-NEAR-SOL và đóng băng paper-test mới (ADR-005)

Nhánh: `feature/donchian-basket3`. Julius chọn rổ ETH/NEAR/SOL, chia đều 1/3, áp rule Setup-2. Rổ này xếp thứ 2 trong backtest 10 rổ: +81,75%, DD 11,58%, PF 1,47; khi chi phí gấp đôi vẫn +57,4%.

- **[ADR-005](docs/decisions/005-eth-near-sol-prospective-paper.md):**
  - Hash rule `bf6fccc9…`, trùng với case ETH-NEAR-SOL trong backtest.
  - Mốc đóng băng **2026-10-10 00:00 UTC**.
  - Tiêu chí giữ như ADR-003.
- **ADR-003 (NEAR/SOL/ZEC) dừng trước lần chạy đầu tiên.** ADR-004 ghi nhận rổ ban đầu.
- **`donchian_prospective`:**
  - Chạy rổ ETH-NEAR-SOL bằng `BasketConfig` / `BasketStressConfig`.
  - Thư mục kết quả đổi tên thành `eth-near-sol-prospective-<YYYYMMDDTHHMMZ>{-inputs,}`.
  - Tin Telegram mở đầu bằng `ETH-NEAR-SOL paper:`.
- **Timer:** giữ tên unit `setup2-prospective.*` để không phải xoá file unit cũ, chỉ đổi phần mô tả. Lần chạy đầu vẫn là thứ Hai 12/10 lúc 12:00 giờ Việt Nam.

## 2026-10-09 — Backtest 10 rổ 3 coin với rule Setup-2

Nhánh: `feature/donchian-basket3`. Julius muốn cả hệ thống chỉ chạy 3 coin, giữ rule Setup-2 (A4 + ADX20) và cho Jev xác nhận tín hiệu. Thứ tự là backtest trước rồi mới làm runtime. Chi tiết xem [donchian-basket3](docs/donchian-basket3.md).

### Đã làm

- **Config rổ mới:** `BasketConfig` / `BasketStressConfig` (chi phí x2), áp rule Setup-2 lên 3 coin trong BTC/ETH/NEAR/SOL/ZEC, tỷ trọng chia đều.
- **Chạy runner:** thêm universe `baskets` cho runner ADX, kèm case đối chứng Setup-2.
- **Xếp hạng:** module `donchian_basket_analysis`.
- **Không đổi engine, runtime hay VPS.**

### Kết quả

Dữ liệu 2022-01 → 2026-10, input đóng băng 5 coin.

- **Cả 10 rổ đều có lãi** ở cả hai giai đoạn và cả khi chi phí gấp đôi: lãi +52,8% đến +84,2%, DD 9,9–13,0%, PF 1,35–1,51.
- **Top 3 theo lãi/DD:**
  - BTC-NEAR-SOL: +84,2%, DD 10,6%.
  - ETH-NEAR-SOL: +81,8%, DD 11,6%.
  - ETH-NEAR-ZEC: +66,2%, DD 9,9%.
- **NEAR/SOL/ZEC chia đều xếp thứ 5:** +65,9%, DD 11,3%.
- **5 rổ đứng đầu đều có NEAR.**
- Case đối chứng Setup-2 trùng từng byte với lần kiểm tra ngày 07/10.
- **Lưu ý:** chọn 1 trong 10 rổ trên dữ liệu đã thấy là thiên lệch chọn mẫu, không phải bằng chứng OOS. Nếu đổi khỏi NEAR/SOL/ZEC thì cần ADR mới với mốc đóng băng mới.

### Quyết định vận hành (ADR-004)

Ghi tại [ADR-004](docs/decisions/004-three-coin-system.md), chưa triển khai:
- **Tối đa 3 coin cho toàn hệ thống:** soak, Jev và trade đều chỉ chạy trên 3 coin này.
- **Rule:** Setup-2 tạo tín hiệu, Jev xác nhận.
- **Mỗi coin tùy chỉnh được:** chế độ (Spot, Perp hoặc cả hai) và tỷ trọng riêng.
- **Đổi coin, tỷ trọng hay chế độ:** chỉ khi portfolio đã tạm dừng và không còn vị thế mở.
- **Đổi coin nhanh, không cần backtest:** coin mới cần ≥600 nến H4, soak ≥14 ngày và qua gate v2; được gắn nhãn `no_backtest_evidence`.

### Kiểm thử

- 11 test mới.
- Toàn bộ suite: 1384 pass, 1 fail có sẵn từ trước trên `main` (`test_perp_collection_migration`). Test này pass hôm 07/10 và fail từ 09/10, nhiều khả năng phụ thuộc ngày chạy, không liên quan tới thay đổi này.

## 2026-10-07 — Timer hằng tuần cho paper-test Setup-2

Nhánh: `integrate/donchian-research`. Julius hay tắt máy nên muốn chạy paper-test trên cloud.

- **Không chạy được trên cloud.** Đã thử với routine Claude cloud: sau khi mở mạng cho `api.binance.com` và `fapi.binance.com`, Binance vẫn trả HTTP 451 (restricted location) cho mọi endpoint. GitHub Actions cũng sẽ gặp lỗi tương tự.
- **Chạy trên máy bằng timer.** Mỗi lần chạy, paper-test replay lại toàn bộ cửa sổ từ mốc đóng băng, nên máy tắt hay lỡ một tuần cũng không mất dữ liệu.
- **Lệnh mới `donchian_prospective scheduled`:**
  - Tự chạy `collect` rồi `evaluate` tới cây nến H4 mới nhất, ghi vào `setup2-prospective-<YYYYMMDDTHHMMZ>{-inputs,}`.
  - Bỏ qua nếu mốc đó đã chấm, hoặc nếu chưa có nến H4 nào sau mốc đóng băng.
  - Gặp thư mục dở dang thì dừng, không xoá.
- **Timer systemd user:** thứ Hai lúc 12:00 giờ Việt Nam, `Persistent=true`. Cài bằng `make install-prospective-timer`; file nằm trong `deploy/systemd/setup2-prospective.*` và `scripts/prospective-timer.sh`.
- **Báo qua Telegram:** dùng chung bot của timer paper BTC (`paper-alerts.env`).
  - Mỗi lần chấm mới gửi 1 tin: verdict, số ngày, số lệnh, winrate, PnL (kèm PnL khi chi phí gấp đôi), DD, PF.
  - Chạy lỗi thì gửi lỗi; lần chạy bị bỏ qua thì không gửi gì.
  - Gửi tin thất bại không làm hỏng lần chạy.
- **Gộp vào `main`:** fast-forward `main` lên `integrate/donchian-research`, không có xung đột.
- **Không đổi gì ở rule, hash rule, tiêu chí hay runtime.** Thêm 5 test cho `scheduled` và phần báo tin.

## 2026-10-07 — Gộp nghiên cứu ADX vào engine và đổi paper-test sang Setup-2

Nhánh: `integrate/donchian-research` (dựa trên `feature/a4-prospective-paper`). Julius đổi rổ coin của paper-test sang **NEAR/SOL/ZEC** với ADX20, tỷ trọng NEAR 30 / SOL 40 / ZEC 30. Đây chính là Setup-2 của nghiên cứu ADX.

### Gộp chuỗi nhánh nghiên cứu (`3ecd3c9`)

- Merge `feature/donchian-adx-setups` vào nhánh tích hợp. Nhánh này đã chứa sẵn `donchian-oos-2022-2024`, `donchian-adx-2022-2026` và `donchian-adx-five`.
- Chỉ có 1 xung đột: cách đặt tên phí. Mình thống nhất về `spot_fee`/`spot_slip` vì nhánh cũng dùng chúng cho cost stress; `EquityBook` lấy phí từ config.
- Bỏ qua 2 file đang sửa dở, chưa commit trong worktree `system-trading/donchian-adx`.
- Kiểm chứng:
  - 1370 test pass.
  - 20 case Donchian filter 703 ngày trùng từng byte với lần chạy ngày 06/10.
  - Setup-1…5 trùng toàn bộ summary và journal với kết quả gốc; chỉ `result_id` đổi theo evaluator v1.1.

### Paper-test Setup-2 (ADR-003)

- [ADR-003](docs/decisions/003-setup2-prospective-paper.md) thay thế ADR-002.
  - Hash rule `a9c7140a…`, mốc đóng băng **2026-10-08 00:00 UTC**.
  - Tiêu chí giữ như cũ: ≥ 120 ngày và ≥ 60 lệnh; PF ≥ 1,2; DD ≤ 12%; lãi ròng > 0 ở cả chi phí chuẩn lẫn chi phí gấp đôi.
  - Dự kiến đủ mẫu vào khoảng tháng 6/2027.
- Lịch sử Setup-2, 2022–2026 (dữ liệu đã nhìn trước): +66,40%, DD 10,85%, 419 lệnh, winrate 42,2%, PF 1,40.
- `donchian_prospective.py`:
  - Chạy Setup-2.
  - Chi phí gấp đôi được replay thật bằng `cost_multiplier=2`, không còn là ước tính.
  - `collect` tự tải cả warmup; nếu dữ liệu bị thiếu thì báo lỗi.
  - Cửa sổ không bắt đầu đúng mốc đóng băng chỉ được tính là kiểm tra quy trình (`not_prospective`).
- Đã chạy thử trên dữ liệu thật trước mốc (04/10 → 07/10): tải đủ warmup NEAR/SOL/ZEC, không thiếu nến, mất 9 giây.

## 2026-10-06 — Paper-test prospective cho A4-Donchian30-10

Nhánh: `feature/a4-prospective-paper`. Chọn A4-Donchian30-10 làm ứng viên, vì đây là họ setup duy nhất có cả bằng chứng trong mẫu (703 ngày: +15,9%, DD 8,5%) lẫn ngoài mẫu (2022–24: +34,9%, DD 10,5%; chi phí gấp đôi vẫn +24,9%). Kết luận OOS là Inconclusive, nên **không bật** mà chỉ paper-test trên dữ liệu mới.

### Thay đổi

- **ADR-002 đóng băng rule trước khi thấy dữ liệu mới** (`852849d`): [docs/decisions/002-a4-donchian-prospective-paper.md](docs/decisions/002-a4-donchian-prospective-paper.md).
  - Hash rule `4a8eae77…`, mốc đóng băng 2026-10-02 00:00 UTC.
  - Tiêu chí chốt trước: ≥ 120 ngày **và** ≥ 60 lệnh; profit factor ≥ 1,2; DD ≤ 12%; lãi ròng > 0 cả khi chi phí gấp đôi (ước tính).
- **Module mới `intraday/replay_v2/donchian_prospective.py`:**
  - `collect`: giữ warmup từ bundle đóng băng, chỉ tải dữ liệu công khai sau mốc.
  - `evaluate`: replay đúng engine Donchian filter, kiểm tra replay hai lần, rồi chấm theo tiêu chí.
- **Không đổi gì ở runtime, gate hay lệnh.**

### Kiểm chứng

- Config đóng băng replay trên 703 ngày cho kết quả trùng từng byte (`result_id`, summary, 3 journal) với case A4-Donchian30-10 đã công bố.
- `uv run pytest -q`: 1257 passed (10 test mới, gồm trọn luồng collect → evaluate trên dữ liệu giả).
- Lần chạy đầu: dữ liệu đến 2026-10-06 12:00 UTC (4,5 ngày), 0 lệnh. Có một breakout BTC Spot bị bộ lọc volume profile chặn. Kết luận: `insufficient_sample`.
- Report nằm ở `donchian-prospective-20261006/` và `donchian-prospective-20261006-inputs/`.

### Sửa ngày

Mục dọn code bên dưới trước đây ghi nhầm ngày 2026-10-07; ngày đúng là 2026-10-06. Các thư mục kiểm tra `*-20261007-refactor-check*` vẫn giữ tên cũ, nhưng thực tế cũng được tạo ngày 2026-10-06.

## 2026-10-06 — Dọn code trùng lặp từ code review (không đổi kết quả)

Nhánh: `feature/weekly-gate-automation`. Đây là các mục 5–10 còn lại của `/code-review`. Không tăng VERSION vì output không đổi.

### Thay đổi

- **`journal_hash` dùng chung** (`ef352c4`). Hàm chuyển sang `metrics.py` và thay vòng SHA-256 viết tay ở 3 study (historical, trailing-cadence, mixed portfolio).
- **Decode bundle M15 của Donchian filter một lần** (`e9e293c`). Bản sao frozen chỉ cần khớp fingerprint với input đã kiểm tra. Thời gian chạy giảm từ 4 phút 38 giây xuống 3 phút 48 giây.
- **Mixin `PerpSleeveRisk`** (`45cd323`). `record_perp` và `enforce_perp_risk` trước đây giống hệt nhau ở `IntradayBook` và `ShortReserveBook`, giờ dùng chung một chỗ.
- **Helper EMA50 dùng chung** (`1ef1ac6`). File mới `indicators.py` có `ema50_trend` và `trend_side`.
  - Các nơi dùng: daily filter của portfolio, `daily_directions` (trước tính EMA hai lần), trend H4/H8 và study equity.
  - `donchian_filters` giữ vòng riêng, vì EMA50 ở đó chạy chung vòng với EMA200 và ADX Wilder.
- **`EquityBook` dùng lại `PortfolioBook.close`** (`87b59b3`). Phí và slippage đọc qua `fee_rate`/`slip_rate`; các trường journal của equity giữ nguyên. `EquityBook.enter` vẫn riêng vì logic khác.

### Không làm

- **Mục 10 (ghi Perp curve hai lần):** không đổi.
  - Hai dòng `parent_checkpoint` và `perp_risk` không trùng nhau, vì dòng sau ghi trạng thái lock *sau* bước đánh giá.
  - Bỏ đi một dòng sẽ mất thông tin và làm đổi sidecar đã công bố.
  - Thay vào đó, code có thêm comment giải thích hai stage.

### Kiểm chứng

- `uv run pytest -q`: 1247 passed, gồm 2 test mới cho EMA50, so với thuật toán cũ.
- Chạy lại offline 8 study vào các thư mục `*-20261007-refactor-check*`: equity, historical baseline 24 tháng, trailing-cadence 703 ngày, short-reserve, intraday-timeframes, hold-margin, single-sleeve, Donchian filter.
- Kết quả: 46 `result_id`, 46 summary, 123 journal và 28 sidecar đều **trùng từng byte** với report ngày 2026-10-06. Riêng equity được so với bản gốc ngày 2026-10-03.
- Study portfolio-research không có CLI chạy lại offline. Đường code `daily_trend` của nó đã được kiểm tra qua historical baseline và test.

## 2026-10-06 — Sửa ledger lỗ ngày và chạy lại các study

Nhánh: `feature/weekly-gate-automation`. Nguồn: 4 lỗi logic do `/code-review` tìm ra trong ledger backtest dùng chung (`intraday/replay_v2/`).

### Sửa lỗi

- **Resume sau daily halt không xóa lỗ của ngày mới** (`5b4c4d2`). Trước đây `MixedBook.maybe_resume` reset `day_start` theo equity hiện tại. Khoản lỗ phát sinh đầu ngày UTC mới, trước khi book về flat, vì thế bị xóa. Giờ nếu lỗ ngày mới đã ≥ 3% thì book tiếp tục halt tới 0h hôm sau. `FilterBook` dùng chung logic này.
- **Warmup `mark15m` tải giá mark** (`1ee1273`). Trước đây prefix warmup luôn tải giá trade. `extend()` giờ cũng từ chối ghép prefix khác loại giá, symbol hoặc interval với snapshot cha.
- **Khôi phục kiểm tra bảo toàn cash** (`844f783`). `MixedBook.enter_batch` raise lỗi khi cash xuống dưới `-1e-18`, cùng ngưỡng sai số mà bước đối soát journal đang dùng.
- **Mốc lỗ ngày neo theo giá mark lúc 0h ở mọi engine** (`e0b851d`). Áp dụng cho cả portfolio lẫn sleeve Perp, trong các engine historical, intraday và short-reserve. Trước đây chỉ Donchian filter làm đúng, nên ngưỡng 3%/ngày bị đo trên các mốc khác nhau giữa những study được đem ra so sánh.

### Version evaluator

Commit `29865ec`:

- `historical-mixed-quant` lên v1.3.
- Các evaluator lên v1.1: các hằng phụ trong `historical_mixed.py`, intraday-timeframes, short-reserve, hold-margin, single-sleeve, Donchian filter.
- `mixed-portfolio-research` lên v2.

### Chạy lại 13 study

Commit `de1bdcf`. Mọi study chạy offline từ input frozen (SHA256 giống hệt), và mọi biến thể pass kiểm tra replay hai lần.

- **Không đổi về kinh tế:** Donchian filter (20 ca), short-reserve, hold-margin, intraday A/C, Spot single-sleeve, các preset 24 tháng, trailing-cadence 703 ngày. Chỉ các chỉ số mô tả "ngày Perp tệ nhất" và "giveback" lệch dưới 0,005 điểm %.
- **Thay đổi nhẹ:** lợi nhuận ngày của Perp ở hai biến thể này nằm sát ngưỡng −3%, nên mốc 0h mới làm điểm dừng lỗ dịch một tick 15 phút.
  - Intraday B: +28,16% → +28,10%.
  - Perp-only single-sleeve: −50,13% → −50,57%, số trade 770 → 772.
- **Không chạy lại:** study recorded-Jev có 0 trade nên không bị ảnh hưởng.

Report mới nằm trong `~/.local/state/aigorithmic-trading/reports/*-20261006-rerun*`. Bằng chứng cũ giữ nguyên.

Chi tiết: [perp-intraday-timeframes](docs/perp-intraday-timeframes-research.md), [single-sleeve](docs/single-sleeve-research.md), [perp-short-reserve](docs/perp-short-reserve-research.md), [hold-margin](docs/hold-margin-research.md), [donchian-filter](docs/donchian-filter-research.md), [mixed-portfolio](docs/mixed-portfolio-research.md). Mỗi file có mục "Rerun after daily-loss fixes — 2026-10-06".

### Kiểm thử

- `uv run pytest -q`: 1245 passed.
- Có 5 regression test mới, mỗi bản sửa đều có test riêng.

### Còn mở

Các mục 5–10 của code review (dọn code trùng lặp, cải thiện hiệu năng) để làm ở PR sau.
