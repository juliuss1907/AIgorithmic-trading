# Replay v2 — research và gate versioned tách biệt

Implementation local 2026-10-01: accounting `replay-v2.2`, gate `gate-v2.1`;
profile legacy vẫn dùng accounting `replay-v2.1`. Source schema v23 giữ nguyên.
V2 mô phỏng một coin/một market mỗi run. Không gửi lệnh, gọi model, refresh history,
đọc API key hoặc ghi vào source DB khi chạy `aigt replay run`.
Gate là luồng operator riêng: `aigt assets rules replay RULE_ID --engine v2` ghi
evaluation append-only; không tự start validation, promote hoặc bật execution.

Study nhiều setup và LLM confidence theo coin là workflow riêng, có thể tải public
history/funding và gọi model trên research copy; không đổi tính offline của
`replay run`. Xem [four-coin runbook](four-coin-replay-runbook.md).

## Chạy và xem kết quả

Các timestamp phải có timezone; `--from` inclusive, `--to` exclusive. Code chuyển
sang UTC; web hiển thị UTC+7. Chọn rule ID đúng symbol/scope, không nhất thiết đã pass.
Spot v2 chỉ nhận scope `spot_4h`, không dùng champion `spot_daily` legacy.

```bash
aigt replay run ETH --market spot --rule RULE_ID \
  --from 2026-09-01T00:00:00Z --to 2026-10-01T00:00:00Z
aigt replay run ETH --market perp --rule RULE_ID \
  --from 2026-09-28T00:00:00Z --to 2026-10-01T00:00:00Z --leverage 3
aigt replay list
aigt replay show RUN_ID
```

`RULE_ID` và `RUN_ID` là placeholders, không copy như ID thật. Coin mới dùng cùng
lệnh nếu đã đăng ký scope và có rule/evidence; không có nhánh riêng BTC/ETH.
Không yêu cầu 365 ngày hoặc 14 ngày cho report nghiên cứu này; gate v2 có minimum
riêng bên dưới. Khoảng thời gian ngắn không chứng minh hiệu quả chiến lược.

Nếu có deployment registry và không chọn đường dẫn native, CLI route tới Docker
admin. Image admin/web phải chứa feature này; không tự build/redeploy từ lệnh replay.
`--config` trong Docker được bind đúng một file JSON chỉ-đọc; không mount thêm key.

Chạy native rõ ràng khi test code local, không route sang image Docker cũ:

```bash
uv run aigt replay run ETH --market spot --rule RULE_ID \
  --from 2026-09-01T00:00:00Z --to 2026-10-01T00:00:00Z \
  --database /ABS/PATH/source.sqlite3 --report-dir /ABS/PATH/replay-reports
uv run aigt replay list --report-dir /ABS/PATH/replay-reports
uv run aigt replay show RUN_ID --report-dir /ABS/PATH/replay-reports
```

Database thiếu/cũ hoặc rule sai scope báo lỗi; không tạo DB hay migrate. Chọn
`--database` hoặc `--report-dir` là opt-out Docker routing. `list/show` không cần
đọc database. Không dùng `assets rules status` như read-only command nếu đang kiểm
chứng source tuyệt đối không ghi; tìm ID từ report/readiness hoặc SQL read-only.

## Report lưu ở đâu?

Native mặc định:
`$XDG_STATE_HOME/aigorithmic-trading/reports/replay-v2/<run_id>/`, hoặc
`~/.local/state/aigorithmic-trading/reports/replay-v2/<run_id>/` khi XDG chưa cấu hình.
Ưu tiên `--report-dir` → `INTRADAY_REPLAY_REPORT_DIR` → mặc định.

Docker: named volume `replay-reports`, mount `/app/state/replay-reports`:
admin/worker ghi, web chỉ đọc; worker confidence review vẫn default off.
Thay Compose file không phải deploy;
không start/recreate worker hay Demo execution trong triển khai feature local.

Bundle gồm `manifest.json`, `summary.json`, `summary.md`, `equity_curve.jsonl`,
`trades.jsonl`, `events.jsonl`; folder 0700, files 0600. Manifest công bố atomically
sau khi ghi/fsync artifacts. Run lỗi để lại directory chưa publish; catalog bỏ qua,
không tự xóa. Run ID mới mỗi lần, không ghi đè; result ID giống nhau khi input/config/
evaluator giống nhau. Checksum kiểm chứng artifact, không thay cho backup source.

Web `/replay` và `/replay/RUN_ID` chỉ đọc. Khi chạy native với root tùy chọn, đặt
`INTRADAY_REPLAY_REPORT_DIR` cho web trùng root CLI, hoặc truyền `replay_report_dir`
khi gọi `create_app`. Không nhập API key vào replay/dashboard.

API chỉ GET:

- `/api/replay/runs?offset=0&limit=20` (limit tối đa 100).
- `/api/replay/runs/RUN_ID`: summary, assumptions, checksums, preview equity ≤200 điểm.
- `/api/replay/runs/RUN_ID/equity_curve`, `/trades`, `/events`: offset/limit,
  tối đa 1000 rows/request. Trang HTML dùng 20 trades/30 events mỗi trang.

Không trả raw prompts, state snapshots, credential files hay local artifact paths.
IDs/path/series được kiểm tra; symlink và checksum sai bị từ chối.

## Mô hình và giả định

Defaults: 1000 USDT (≤10000), weight 1; Spot cap 30%, Perp cap 20% **vốn ban đầu**;
target còn bị giới hạn theo equity hiện tại. Phần chưa dùng là cash. Perp isolated
3x, override 1–10x; leverage thấp có thể giảm target để đủ margin room, không nhân
notional/PnL. Margin entry limit 10% dựa trên pre-fill equity; tỷ lệ mark-to-market
sau phí/biến động có thể khác, được báo thật trong report.

- Spot: Donchian trên closed native 4h; cần đủ contiguous warm-up bars trước entry,
  ATR size multiplier một lần; next-open fill, Donchian exit độc lập với stop 10%.
  Không dựng Jev cho lịch sử. Nếu window cắt giữa nến, chỉ mô phỏng nến 4h trọn vẹn.
  OHLC giả định open → adverse → favourable → close; intrabar timestamp ước tính.
- Perp: numeric-v1 primary Jev đã lưu, feature schema 2. Xác minh snapshot và
  archived successful model-call fingerprint, không dùng assignment hiện tại.
  Khớp quote hợp lệ đầu tiên **sau** decision availability (kể cả model completion),
  decision age ≤45s, entry spread ≤10bps/dislocation ≤1%. Entry dùng ask/bid,
  stop trigger dùng mark; gap fill ở quote khả dụng, không khớp giả ở stop.
- Một position mỗi route, không pyramid/flip/re-entry tại quote đóng. TakeProfit
  đóng position theo Demo semantics, không phải outcome directional-return 15 phút.
- Daily loss 1.5%, DD 8%, hardcoded và không model-tunable; terminal halt tới hết
  run. Phí đóng cũng có thể kích hoạt guard. Gap có thể làm lỗ vượt threshold;
  stop/risk guard không phải đảm bảo mức lỗ tuyệt đối.
- Cuối run đóng ở giá cuối hợp lệ với `window_end`. Nếu thiếu price tail, report
  ghi rõ: không kéo forward giá, không giả định giữ position qua funding tương lai
  rồi backdate lệnh đóng. Exit cuối cửa sổ là giả định nghiên cứu, không tín hiệu.

Profile mới mặc định tách Spot **fee 10 + slippage 5 bps**, Perp **taker fee 5 +
slippage 5 bps**, mỗi fill debit vào cash đúng một lần. Spread Perp đã nằm trong
bid/ask; funding tính riêng. Phí tham chiếu Regular User từ
[Binance Spot](https://www.binance.com/en/fee/trading) và
[USDT Perp](https://www.binance.com/en/fee/futureFee), snapshot 2026-10-01,
không giảm BNB/VIP, không khẳng định phí thực thu trên Demo hoặc phí lịch sử.
Slippage là cash charge giả định, không sửa fill price. Giá nguồn là market data
Binance đã lưu, không được xác minh là historical Demo quotes.

`--config FILE.json` chỉ nhận capital/leverage/profile. CLI `--capital`/`--leverage`
override file. CLI mặc định `--cost-profile binance-regular` (profile v2);
`--cost-profile legacy` tái lập combined Spot 15/Perp 5 bps với output v1.
Profile được khai báo trong file có ưu tiên hơn cost-profile CLI. Ví dụ legacy:

```json
{
  "capital": "1000",
  "leverage": 3,
  "profile": {"profile_id": "bnb-demo", "version": "1", "spot_cost_bps": "15", "perp_cost_bps": "5"}
}
```

Profile có thể thêm `instrument` theo contract `InstrumentRules`, bắt buộc
`instrument_source` + `instrument_observed_at`; symbol phải đúng coin. Đây là
metadata snapshot, không phải historical filters/maintenance tiers. Không có
filters thì quantity lý tưởng hóa và `instrument_filters_not_verified`; không
khẳng định Binance Demo hỗ trợ coin/order type chỉ vì replay có PnL.

Funding optional cho research, bắt buộc đầy đủ cho Perp gate. `profile.funding` gồm `symbol`, `source`, `coverage_start`,
`coverage_end`, `settlements` (mỗi row: aware `at`, Decimal `rate`, positive `mark`).
Timestamp unique/ordered trong coverage. Chỉ khai báo coverage thật đã kiểm chứng;
đừng khai báo empty/full coverage để biến dữ liệu thiếu thành funding 0.
Long trả rate dương, Short nhận; rate âm đảo dấu. Không dùng lastFundingRate/OI
hoặc tự dựng lịch funding làm settlement. Coverage thiếu ⇒ `net_pnl/net_return_pct`
null; chỉ hiện PnL/risk sau chi phí đã biết, đường đi risk có thể đổi khi bổ sung
funding đầy đủ. ES là tail mean 5% của **UTC daily equity returns**, ghi số mẫu.

V1 evaluation trong report chỉ tham chiếu; Spot v1 dùng folds khác, Perp v1 scoring
là decision-quality, không phải account return. Rule chọn sau start window được
gắn nhãn ex-post, không được gọi là OOS. Không auto tune để ép pass hoặc activate.

Chưa mô phỏng full order book, partial fills, API/reconciliation failures,
liquidation chính xác hoặc portfolio đa coin. Hyperliquid cần profile, dataset
mapping và acceptance riêng; engine không chứa Binance API calls nhưng hiện chỉ
`bnb-demo` được hỗ trợ. Không đổi profile name để giả hỗ trợ sàn khác.

## Operator gate v2: replay → validation mới → promote

Các ID bên dưới là placeholders. Khi test native, thêm `--database /ABS/source.sqlite3`
và `--report-dir /ABS/replay-reports` cho các lệnh lifecycle. Phải giữ report root:
start/promote kiểm chứng checksum và binding report với evaluation.

```bash
aigt assets rules replay RULE_ID --engine v2
aigt assets rules start-soak RULE_ID --evaluation-id REPLAY_EVAL_ID
aigt assets rules evaluate RULE_ID
aigt assets rules activate RULE_ID --evaluation-id SOAK_EVAL_ID
aigt assets rules status COIN
aigt assets readiness COIN --market spot
```

`replay run` không tạo gate ID. Historical gate pass chỉ cho phép operator bắt đầu
campaign mới. `activate` ở namespace rules chỉ promote champion; không activate
Demo worker, không đổi allocation/leverage. Default lifecycle replay vẫn v1 nếu
không chọn `--engine v2`; campaign đã chọn v2 không được fallback sang v1.

- Spot: ≥2190 native 4h bars, coverage ≥99%, latest age ≤14700s; window liên tục
  từ sau 1095 bars đầu đến cutoff nến đóng, ≥6 closed trades. Đây là rule-only
  ex-post replay, không dựng Jev/OOS giả. Validation mới ≥14 ngày, ≥6 matured 12h
  setups, heartbeat ≥95%, score sau chi phí >0, không hard-risk violation.
- Perp: collection ≥14 ngày, ≥100 matured outcomes và verified decisions,
  outcome/heartbeat/quote coverage ≥95%, ≥6 closed trades, funding/provenance đầy đủ.
  Sau gate cần **14 ngày mới**, ≥100 outcomes/decisions và ≥6 closed trades mới;
  không dùng lại tập collection trước cutoff.
- Replay account net return >0, drawdown <8%, guard ngày 1,5%; Spot stop 10%.
  Champion comparison cùng window/profile: score return−DD và daily ES.
  Guard halt đúng luật không tự thành hard-risk violation. Thiếu evidence/funding
  là deferred; hard violation là reject ngay, không che bằng thiếu mẫu.

Perp funding thu thập public qua lệnh riêng; không cần key, không nằm trong replay engine:

```bash
aigt replay funding fetch ETH --from 2026-09-01T00:00:00Z --to 2026-10-01T00:00:00Z
aigt assets rules replay RULE_ID --engine v2 --funding-id FUNDING_ID
aigt assets rules evaluate RULE_ID --funding-id POST_GATE_FUNDING_ID
```

Snapshot ở `REPORT_ROOT/funding/FUNDING_ID.json`, giữ raw rows/checksum và requested
coverage. Endpoint Binance public/mainnet là nguồn settlement nghiên cứu, không phải
Demo account statement. Window phải đã kết thúc; empty/invalid/timeout không thành
funding 0 hay complete. Validation cần snapshot phủ toàn bộ window mới.

## Compatibility và triển khai

### Explicit Perp champion collection inheritance

Đối với Perp đã có champion, operator có thể giữ nguyên tham số và tái sử dụng
collection archived đã audit, thay vì tự backdate một challenger mới:

```bash
aigt assets rules inherit-perp BTC --collection-from 2026-09-25T12:55:46.676811Z
aigt assets rules status BTC
aigt assets readiness BTC --market perp
# Dùng candidate_id mới từ output, không dùng ID champion v1:
aigt assets rules replay CANDIDATE_ID --engine v2 --funding-id FUNDING_ID
```

Native/local: thêm `--database /ABS/source.sqlite3` cho từng lệnh và giữ cùng
`--report-dir /ABS/replay-reports` cho funding/replay/start/evaluate/activate.
Không copy local snapshot evaluation ID sang live VPS để start soak.

`inherit-perp` là write opt-in riêng; cần enabled shadow/soak route có champion,
không competing candidate hoặc active validation. Mốc from phải aware, có recorded
decision trong 30 giây đầu; không đặt trước history để giả đủ 14 ngày. Audit prefix
kết thúc 15 phút trước clock để tránh in-flight decisions; thiếu archived model-call
provenance/quotes thì không tạo candidate. Không gọi model hoặc exchange API.

Candidate giữ actual creation clock; immutable `perp_gate_collections` bind
candidate/champion hashes, source config và dataset checksum. Retry cùng champion/from
trả cùng binding; replay kiểm chứng lại prefix, từ chối khi evidence/rule thay đổi.
Champion, v1 registry anchors và evaluations không đổi. Collection proof được ghi
trong report methodology và status; readiness GET chỉ preview source, không rerun replay.

Coverage/funding/số giao dịch và minimum 14 ngày vẫn được xét trên growing window.
Historical account replay là ex-post, không phải OOS/Jev validation mới. Passing gate
chỉ cho phép operator start campaign mới; ít nhất 14 ngày sau campaign start vẫn bắt
buộc và không tính lại collection cũ. Candidate đã inherit không được fallback v1.
Extension optional, readers không chạy DDL; nâng CLI/admin/readers trước khi dùng
luồng mới. Không tự xóa binding để rollback hoặc bật Demo.

Kết quả local BTC/ETH và SOL deferred:
[verification](btc-eth-gate-v2-verification.md), [plan](../tasks/btc-eth-gate-v2-plan.md).

Optional extension `replay_gate_meta` version 1 chỉ được cài khi operator ghi gate;
readiness/read-only readers không chạy DDL. Source schema vẫn v23. V1 evaluation,
raw market data và soak evidence không bị xóa/rewrite. V1-rejected candidate có thể
được chọn vào campaign v2 mới sau passing replay; không sửa rule content để ép pass.

Trước khi deploy cần verified backup và nâng toàn bộ writer/CLI/execution reader
liên quan. Chưa deploy gate v2 trong bước implementation này. Passing/promoted v2
được Demo acceptance đọc đúng binding; account, venue, allocation, settings và
supervised order acceptance vẫn cần phê duyệt riêng.
