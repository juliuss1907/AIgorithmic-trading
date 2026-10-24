# HYPE Spot: sửa chặn Setup-2 vì thiếu lịch sử context

Ngày 2026-10-23. Nhánh `fix/hype-spot-stale`, gốc `4c1ce71951c3c198cb6464f801da9b4395a5b56a`.
Chỉ sửa và kiểm thử local. Không truy cập VPS, không deploy, không đổi active set, campaign hay ADR-007.

## Nguyên nhân và test tái hiện

`_run_registered_asset_cycles` gọi `MultiTimeframeSpotCache.snapshot` trước `setup2_store.sync` và
`run_asset_lifecycle_observation`. Builder snapshot cũ bắt buộc ≥35 nến đóng cho cả 4h, 8h và 1d.
Vì vậy HYPE Spot có đủ H4 nhưng chưa đủ ngày vẫn ném `StaleMarketData` trước khi đồng bộ H4/M15.
Đây là phụ thuộc của snapshot cũ, không phải yêu cầu của luật Setup-2:
`setup2_store.evaluate` đọc H4 (kể cả proxy giá được cấu hình) và M15 từ `setup2_candles`,
không đọc `context_8h_*` hoặc `context_1d_*` của snapshot.

`tests/test_setup2_spot_context.py` dựng route HYPE Spot có proxy giá Perp, ngưỡng 400, profile
`setup2_slots_v1`, H4 mới và chỉ 29 nến context. Test tái hiện riêng cả thiếu 8h và thiếu 1d.
Trước sửa, hai ca thất bại vì danh sách gọi fetch của sync là `[]`
(receipt RED: `/tmp/hype-spot-stale-red.txt`). Sau sửa, test chạy sync và evaluator thật trên
SQLite tạm; chỉ thay nguồn tải bằng nến giả lập, không gọi Binance hay Jev.

Test lưu H4 tới nến mở `2026-10-13T20:00Z`, M15 tới cùng ranh giới, rồi xác minh sync bù đầy đủ
hai chuỗi từ `2026-10-14T00:00Z` tới `2026-10-23T12:00Z`, ghi đúng một observation ở H4 hiện tại,
scheduler `success`, không có fill và không sửa plan hay tham số rule. Chạy lại cùng slot không
nhân đôi observation; sync tiếp theo trả `4h: 0`, `15m: 0`.

## Lựa chọn sửa và cách thể hiện chất lượng

Chọn **B có giới hạn theo route**: `allow_short_context=False` là mặc định của builder/cache.
Collector chỉ bật khi rule Spot thuộc một profile Setup-2 và route được active set cho phép
hoặc được watch. Route legacy và route Setup-2 ngoài active/watch vẫn kiểm tra nghiêm như trước.

- Với 1–34 nến context 8h/1d, vẫn kiểm tra thời điểm đóng, độ mới và độ dài interval.
  Cả sáu trường context tương ứng là `None`, freshness `candles_8h`/`candles_1d` là `false`,
  quality flag là `candles_8h_insufficient_history`/`candles_1d_insufficient_history`.
- Snapshot vẫn được lưu, và worker ghi JSON `spot_context`, `status: degraded`, các flag cùng
  `snapshot_id`. Sự thiếu lịch sử được biểu diễn rõ bằng bằng chứng và cảnh báo này;
  không ghi nó thành lỗi thực thi H4 khi Setup-2 vẫn chạy được.
- Thiếu hoàn toàn một chuỗi, H4 dưới 35 nến, nến cũ/chưa đóng/sai interval, book không hợp lệ
  hoặc hết hạn, lỗi sync và lỗi evaluator vẫn đi qua cơ chế `scheduler_runs.status=error` hiện có.
- Khi evaluation và ghi observation hoàn tất, job `asset_spot_4h_hypeusdt` là `success`, kể cả
  kết quả `skipped_no_setup`. Heartbeat Setup-2 vẫn tính bằng observation, không bằng log cảnh báo.
  Hành vi Jev lỗi vẫn theo cơ chế đã có của `run_setup2_observation`.
- Khi context đạt 35 nến, snapshot giống hệt chế độ strict: cùng features, freshness, flags và ID.
  Test đối chiếu toàn bộ snapshot. Chưa có context thì Jev nhận `None` và quality flags;
  chất lượng tư vấn có thể khác, còn quyết định của evaluator Setup-2 giữ nguyên.

Không đổi `intraday/setup2.py`, `setup2_store.py`, hợp đồng rule, `EVALUATOR_VERSIONS`, schema,
luật/proxy/warm-up/risk hoặc các tệp replay. Thay đổi chỉ ở collector và builder/cache snapshot.

Năm tệp thay đổi: `intraday/__main__.py` chọn opt-in và ghi cảnh báo; `intraday/spot_signal.py`
biểu diễn context ngắn; `tests/test_setup2_spot_context.py` kiểm hồi quy; `CHANGELOG.md` và báo cáo này.

## Kiểm thử

Lượt ngày 23/10, nhóm liên quan: **97 passed, 1 warning**, 48,81 giây:

```bash
.venv/bin/pytest -q tests/test_setup2_spot_context.py tests/test_spot_donchian.py \
  tests/test_paper_cutover.py tests/test_setup2_*.py tests/test_tui.py \
  tests/test_spot_proxy.py tests/test_spot_4h_lifecycle.py tests/test_multi_asset_runtime.py
```

Lượt toàn bộ ngày 23/10: **1.925 passed, 3 failed, 29 warnings**, 493,22 giây.
Các lỗi:

- `test_perp_collection_migration.py::test_cli_inheritance_and_readiness_show_collection_without_changing_champion`:
  `gate_version` là `v1` thay vì `gate-v2.1`. Đã chạy riêng trên checkout commit gốc `4c1ce71`
  và tái hiện cùng lỗi (1 failed, 1,35 giây).
- `test_tui.py::test_trades_and_ops_tabs_show_the_portfolio_journal_scheduler_and_sources` và
  `test_tui.py::test_palette_form_runs_read_commands_at_once_and_only_shows_terminal_commands`:
  `textual.worker.WorkerCancelled` khi `run_app` chờ worker `load_chart`.
  Cả hai đã qua trong nhóm 97 test; TUI trên commit gốc chạy riêng 14/14 qua, rồi lặp ba lượt
  42/42 qua. Không sửa TUI hay test để che lỗi; chưa có bằng chứng quy hai lỗi này cho bản sửa HYPE.

Ngày 24/10 tiếp tục công việc sau khi môi trường làm mới: chạy lại toàn bộ `.venv/bin/pytest -q`,
lưu receipt bền dưới `state/verification/hype-spot-stale-20261024/`.
**Kết quả cuối: 1.927 passed, 1 failed, 29 warnings, 352,68 giây.** Lỗi duy nhất vẫn là migration
`gate_version: v1` thay vì `gate-v2.1` nói trên. Toàn bộ TUI và các test Setup-2, Spot Donchian,
paper cutover, dashboard đều qua; hai lỗi TUI của lượt trước không tái hiện. Suite chưa xanh hoàn toàn
vì lỗi migration có sẵn; không bỏ qua, xfail hay sửa test đó.

Lượt chạy nhóm liên quan trong sandbox dừng ở test Starlette/AnyIO. Chạy riêng
`test_setup2_page_and_api_render` ngoài sandbox qua trong 1,19 giây; nhóm 97 test chạy ngoài
sandbox cũng qua. Không đổi dependency hay cài package để xử lý việc này.

Receipt còn trên workspace (thư mục `state/verification` được gitignore):

| Receipt | SHA-256 |
|---|---|
| `state/verification/hype-spot-stale-20261024/pytest-full.txt` | `0598eda90dac5f11a7d3ec049e6464ed67e6fcb43ebbb2f8668558e3e41adcaa` |
| `state/verification/hype-spot-stale-20261024/protected-hashes.json` | `ca19e9092a57ec0910d5c98dddc7ed6614eb88593a6d5f4145fcc7ebc389d21a` |

Hash receipt của lượt ngày 23/10 (không commit log thô; các tệp `/tmp` đã mất khi môi trường làm mới):

| Receipt | SHA-256 |
|---|---|
| `/tmp/hype-spot-stale-red.txt` | `253e918c84d6d6ff28fe5b9bf2030e946b5f9453d96963e6c58e439415c56d56` |
| `/tmp/hype-spot-stale-focused-unsandboxed.txt` | `8a8b4b9744a100cba5b5d06109e989783809dd7d5431926cabf1e80a223818f7` |
| `/tmp/hype-spot-stale-full-pytest.txt` | `d533c48774e1f686c699394baac263ee687751650ddd0a21d2009d69282774aa` |
| `/tmp/hype-spot-stale-baseline-migration.txt` | `980991541af4e91fe3eeef7ed26ed064ec22ed16a775d711270076d8e340c1e1` |

Đối chiếu byte với `git show 4c1ce71:<path>`: bốn tệp dưới đây không đổi. SHA-256:

| Tệp | SHA-256 |
|---|---|
| `intraday/setup2.py` | `e13060390e3b889a1466b0dfe766009c90670394eeac2afe0420c17b65862ac5` |
| `intraday/setup2_store.py` | `8aabe992a53f0fe5cdadf54d2165488d967eb6d7dbb1d7b474bedda7de806ec9` |
| `intraday/contracts.py` | `221d3889c567e290b08be87bbe633547c0e6ce1fa44e77444f45a7e24a2a9759` |
| `docs/decisions/007-slot-rotation-five-coin-runtime.md` | `7a0ab367811f0819b9c347feb59d5149f1ff67bae2d263e3a520ed19cf2f2d58` |

## Đề xuất bù dữ liệu và triển khai — chưa thực hiện

**Không deploy trước khi buổi review 28/10 kết thúc.** Mốc chung an toàn trong checklist hiện tại là
`2026-10-28T04:11Z` (khác vài phút giữa campaign); phải chờ review thực tế hoàn tất và Julius duyệt deploy.
Push và deploy là hai lần duyệt riêng. Đề xuất không thay ADR-007 hay lịch soak của 9 route còn lại.

1. Sau khi được duyệt deploy: chụp bằng chứng của các campaign hiện tại, active-set version,
   rule/content hash, commit/image và trạng thái vị thế; tạo và verify backup theo runbook.
   Giữ image rollback và bằng chứng soak cũ. Rebuild image worker/web/admin từ commit được duyệt,
   khởi động theo thứ tự đã kiểm trong runbook để tránh tranh khoá; không khởi động Demo execution.
2. Trong container **admin** của bản đã duyệt, dùng DB/report volume hiện có và đúng cấu hình runtime.
   Các lệnh dưới đây là **đề xuất**, sẽ ghi DB hoặc tải dữ liệu công khai; không phải lệnh chỉ đọc
   và không được thực thi trong thời gian freeze. Không cần đọc/in `.env` hay provider key.

   ```bash
   aigt setup2 backfill --symbol HYPE --market perp
   aigt setup2 backfill --symbol HYPE --market spot
   aigt setup2 evaluate --symbol HYPE --market spot --profile setup2_slots_v1
   ```

   Perp trước để proxy giá có đủ dữ liệu. `sync` bắt đầu ở `available_at` của nến cuối đã lưu
   riêng cho mỗi interval, tải mọi nến đóng tới `floor_h4(now)`, phân trang qua nguồn public.
   H4 đã lưu tới nến mở 13/10 20:00Z thì mốc tải tiếp là 14/10 00:00Z; M15 lấy mốc riêng từ DB.
   Không hard-code mốc chung cho cả hai. Trường hợp này là khoảng trống ở cuối chuỗi, sync tự bù;
   nó không tự vá một lỗ hổng nằm trước nến cuối đã lưu. Nến đã lưu là bất biến, payload khác sẽ lỗi.
   Chạy lại backfill cùng ranh giới trả 0 khi đã đủ nến. `evaluate` chỉ đọc và không tạo observation.
3. Xác minh max thời gian/count/liên tục của H4 và M15, các proxy Perp trước niêm yết, kết quả evaluate,
   snapshot flags và lần H4 kế tiếp: scheduler `success` cùng observation mới đúng rule/bar hiện tại.
   Lần chạy worker đầu sau deploy cũng có thể tự bù qua cùng sync; backfill riêng giúp kiểm dữ liệu
   trước khi bắt đầu campaign mới. Không chạy chồng nhiều writer để bù.
4. **Campaign HYPE Spot phải bắt đầu lại.** Bù nến không khôi phục observation/Jev/heartbeat đã bỏ lỡ.
   Không ghi observation quá khứ, sửa `started_at` hoặc biến replay thành bằng chứng soak.
   Sau khi xác minh dữ liệu, đề xuất Julius duyệt đóng campaign cũ và tạo candidate mới cùng profile:

   ```bash
   aigt setup2 candidate --symbol HYPE --market spot --profile setup2_slots_v1 \
     --actor julius --reason "restart soak after short-context fix" --retire-open
   aigt assets rules replay NEW_ID --engine v2
   aigt assets rules start-soak NEW_ID --evaluation-id NEW_REPLAY_EVALUATION_ID
   ```

   Lấy ID thật từ kết quả, chỉ start-soak nếu replay mới pass; dùng cùng report root của admin.
   `--retire-open` đóng challenger/campaign cũ theo lifecycle hiện có; lưu bằng chứng trước khi đổi.
   Candidate mới có danh tính/content hash mới theo cơ chế sẵn có; không chỉnh tham số luật cũ
   hoặc dùng lại evaluation đã gắn hash khác. Các bước thay campaign này chỉ là đề xuất sau freeze.
5. Bản sửa bỏ thời gian chờ 35 nến 1d, nên **có thể bắt đầu soak mới ngày 28/10**, sau review/deploy
   và kiểm tra đạt, sớm hơn mốc 29/10. Vẫn cần đủ 14 ngày thật và heartbeat ≥95% cùng mọi gate còn lại:
   nếu bắt đầu 28/10 thì sớm nhất khoảng **11/11**, tính theo `started_at` thật, không cam kết đủ điều kiện.
   HYPE Spot chưa được kích hoạt Demo; G5/G6 và kích hoạt route vẫn cần bằng chứng và duyệt riêng.

Tài liệu này là đề xuất mới của bản sửa ngày 23/10, để Julius duyệt sau freeze; không sửa lại quyết định
lịch sử ngày 14/10 trong ADR-007/checklist và không chứng minh trạng thái hiện tại của VPS.
