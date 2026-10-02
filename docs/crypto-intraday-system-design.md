# Kiến trúc AIGT hiện hành — multi-asset Spot/Perp

Cập nhật 2026-10-01. Baseline implementation: `29f05c8`; source schema **v23**.
Đây là hợp đồng kiến trúc của code, không phải dashboard trạng thái realtime.
[Roadmap](../tasks/roadmap.md) phân biệt phần đã xây, đã deploy và chưa nghiệm thu.

## 1. Mục tiêu và ranh giới

AIGT đưa mỗi coin/scope qua catalog → evidence → baseline/candidate → replay/soak →
champion → activation execution riêng. Binance là nguồn thị trường nghiên cứu chính.
Spot và Perp có lifecycle độc lập; không còn giới hạn bootstrap ở ETH hoặc execution ở BTC.

Jev đề xuất quyết định. LLM tạo phân tích/thesis và bounded rule proposal.
Code deterministic sở hữu validation, sizing, hard-risk, reconciliation và exits.
Không model nào tự promote rule, bật trading, đổi leverage hoặc vượt hard-risk.

Ba luồng phải được gọi đúng tên:

| Luồng | Dữ liệu/vị thế | Có đặt lệnh trên sàn? |
| --- | --- | --- |
| Decision-only soak | Lưu model decision, heartbeat và forward outcome vào source DB | Không; không gọi ledger để tạo fill |
| Paper simulator legacy | Parent ledger BTC Spot/Perp, tài khoản giả lập tách khỏi `lab/` | Không |
| Binance Demo execution opt-in | Account/order/fill thực tế trên môi trường Demo, journal riêng | Có, chỉ sau activation; không phải tiền thật/mainnet |

**Đã có code ≠ đã deploy ≠ đã bật/đã nghiệm thu.**
Cài key hoặc deploy image không chứng minh quyền order-write, native-stop hay fill semantics.
Binance live, Hyperliquid execution và DEX execution khác chưa được hỗ trợ.
`connect bnb`, `connect hl demo`, `connect hl` chỉ là cú pháp dành sẵn.

## 2. Thành phần và quyền truy cập

```text
Market/news collectors → closed-bar snapshots → rule setup + Jev → source SQLite v23
                                      ↑                  ↑             │
                              LLM thesis/proposal → rule lifecycle     │ chỉ đọc
                                                        │ operator     ▼
                                                 approve/promote   Demo risk runtime
                                                                       │
                                                          durable intent + adapter
                                                                       │
                                                                Binance Demo API
                                                                       │
                                                         execution SQLite journal
```

Sơ đồ tổng quan tương tác nằm ở `/system-plan`; JSON nguồn là
`docs/diagrams/system-trading.architecture.json`. HTML được sinh bằng Archify, không sửa tay.
Nội dung authored là tiếng Việt; fixed Viewer UI và HTML locale mặc định English.
Sơ đồ rút gọn không thay thế các quyền chi tiết sau:

| Thành phần | Quyền / trách nhiệm |
| --- | --- |
| Soak worker | Ghi source DB; đọc provider/source secrets; không đặt exchange order |
| Web | Read projections; các mutation catalog/provider/operator cần token và idempotency; không giữ exchange key |
| Admin | CLI quản trị theo deployment hoặc native path; quyền được giới hạn theo lệnh |
| Demo worker | Source DB read-only; execution journal riêng writable; key mount read-only |
| Perp settings controller | Profile opt-in riêng; xác nhận leverage, gửi thay đổi rồi GET read-back; không activate trading |
| Hermes add-on | Operator API với token riêng; không nằm trong hot path, không tự promote |

Source schema v23 thêm catalog, venue routes và scan/request metadata, không rewrite
candles, signal, rule hoặc soak evidence. Journal execution có migration/schema riêng;
không dùng số version của source DB để suy ra version của journal.

## 3. Coin và venue onboarding

Catalog động seed BTC, ETH, HYPE, NEAR, ZEC, SOL; coin khác có thể đăng ký nếu syntax,
mapping và market được hỗ trợ. Capability không còn ticker-gated; lifecycle/evidence
vẫn quyết định quyền gọi model và execution.

Spot và Perp đi riêng: **add → scan → xem listing/volume/spread/depth/size impact → chọn venue**.
Non-interactive add không tự chọn sàn; chọn route cũng không activate trading.
Scan Demo/Testnet, route selection và execution quote không fallback sang mainnet.
Chỉ Binance Demo execution hiện selectable. Variational testnet chưa được xác minh;
nguồn thiếu/timeout/empty book hiển thị N/A, không giả volume hay thanh khoản.

Discovery thanh khoản là snapshot, không phải đảm bảo fill. Signal nghiên cứu vẫn dùng
Binance public/mainnet market data; Demo quotes được kiểm tra dislocation trước entry.
Xem [Demo runbook](binance-demo-execution-runbook.md) cho add/scan/venue/configure/activate.

## 4. Dữ liệu, cadence và model

| Job / runtime | Nhịp mặc định | Model / ranh giới |
| --- | --- | --- |
| Legacy paper risk/mark | 5 giây | Deterministic; không áp nhịp này cho mọi runtime |
| Book / Spot quote cache | 15 giây | Không model |
| Perp numeric primary | 30 giây mỗi coin soak | Jev; shadow chỉ lưu market data |
| Derivatives metadata | 60 giây | Funding/OI/long-short |
| Compact shadow experiment | 15 phút, khi runtime tương ứng chạy | Jev observation-only; không phải mỗi coin luôn có paired call |
| Spot 4h observation | Nến UTC 4h đóng mới | Native 8h/1d context; chỉ hỏi Jev khi có rule/setup và lifecycle soak |
| Legacy Spot daily | Nến 1d đóng mới | Compatibility; không mở entry mới trong paper path |
| News ingest | 30 phút | Normalize/allowlist; external text không có quyền lệnh |
| LLM analysis | 60 phút khi evidence thay đổi | Market/news/sentiment → thesis |
| Asset LLM proposal | Slot hằng ngày, xét eligibility riêng | Fresh evidence, một open candidate; tối đa 3 calls/90 ngày/coin/scope |
| Legacy paper retrospective | 09:00 Asia/Ho_Chi_Minh | Có code trong paper loop; không coi soak worker đã tạo retrospective |
| Demo execution profile | 30 giây mặc định | Reconcile/protect/exit/entry theo Demo runtime |

Cadence là cấu hình, không phải bằng chứng job đang chạy. Kiểm tra scheduler/projection
và model-call mới nhất riêng từng role; không chỉ nhìn một danh sách mixed calls.
`status.provider=stub` là cấu hình fallback, không phủ định active provider assignments.
Jev timeout/malformed response không cho phép entry; exits/protection không cần model.
LLM lỗi không được đổi champion hoặc ghi đè evidence.

Hyperliquid, Aster, Variational, Lighter, CryptoRank và Leviathan là các nguồn evidence,
không đồng nghĩa đã có adapter đặt lệnh cho các sàn đó. Xem
[external data runbook](external-data-runbook.md) cho cadence/TTL và giới hạn shadow.

Mọi candle/event/journal sử dụng UTC và timestamp nguồn/received riêng.
Dashboard lịch sử sử dụng UTC+7; hiển thị không làm đổi ranh giới nến.

## 5. Rule lifecycle và gate

Rule/registry/evaluation tách theo `(symbol, scope)`; champion BTC không cấp quyền cho ETH.
Chung schema/baseline defaults không có nghĩa dùng chung evaluation.

### Spot 4h — gate v1 compatibility

1. Closed native 4h history ≥365 ngày, coverage ≥99%; 8h/1d context tải native.
2. Replay walk-forward: ≥6 OOS closed trades, return sau chi phí >0, drawdown <8%,
   không hard-risk violation; champion comparison khi có champion.
3. Operator bắt đầu decision-only soak bằng **đúng passing replay ID**.
4. Soak ≥14 ngày, heartbeat ≥95%, ≥6 distinct setups có outcome sau 12h,
   score sau chi phí >0, không hard-risk violation.
5. Evaluate rồi promote bằng **đúng latest passing soak ID**; sau đó mới xét
   activation Demo riêng. Promote không tạo fill.

Replay thường hoàn thành nhanh khi đã có history; 365 ngày là lookback, không phải
thời gian phải chờ thu thập. 14 ngày chỉ là minimum, thiếu setup vẫn deferred.

**Giới hạn replay hiện tại:** `_walk_forward` mô phỏng Donchian next-open và chi phí
cố định 0,15% mỗi chiều. Chưa mô phỏng đầy đủ ATR sizing, allocation đa coin,
Spot emergency stop 10% hoặc shared Demo loss/margin guards. Return/DD này là
kết quả của evaluator đó, không phải dự báo PnL hay full-risk parity với Demo.
Không hạ gate hoặc rewrite evaluation cũ để làm candidate pass.

### Perp bootstrap — gate v1 compatibility

1. Baseline decision soak trước replay: ≥14 ngày, ≥100 matured 15m outcomes,
   outcome và 30s heartbeat coverage ≥95%.
2. Replay chỉ dùng evidence trước cutoff.
3. Post-replay validation dùng evidence riêng sau passing replay: ≥72 giờ,
   ≥100 outcomes, coverage ≥95%, score sau chi phí >0, không hard-risk violation.
4. Operator promote bằng đúng latest passing validation/soak ID; execution activation riêng.

Vì vậy Perp mới thường cần **ít nhất 14 ngày + 72 giờ**, không chỉ 14 ngày.
BTC Perp legacy có champion và portfolio evaluation riêng; Demo có compatibility
exception cho passing BTC parent evaluation. Không copy exception sang coin khác.

Trên lifecycle v1 chưa chọn v2, LLM chỉ đề xuất khi rejection/deterioration và đủ fresh evidence; không auto-replay,
auto-start Spot soak, auto-promote hay auto-activate. Rejection là kết quả hợp lệ.

### Versioned gate v2 — implementation local, chưa deploy

`assets rules replay RULE_ID --engine v2` dùng offline account replay (`replay-v2.2`)
và lưu `gate-v2.1` append-only; research `replay run` không tự trở thành gate ID.
Spot native 4h ≥365 ngày, window liên tục sau 1095 warm-up bars, ≥6 trades;
rule-only ex-post, không giả Jev/OOS. Perp vẫn cần decision collection ≥14 ngày,
≥100 verified decisions/outcomes, quote/outcome/heartbeat coverage ≥95% và funding đầy đủ.

Phí/slippage mỗi fill: Spot 10/5 bps, USDT Perp taker 5/5 bps; không BNB/VIP.
Spread Perp implicit, funding settlement riêng. Net return >0, DD <8%, daily guard
1,5% và Spot stop 10% giữ nguyên. Historical pass chỉ mở quyền operator bắt đầu
**14 ngày validation mới** (Spot ≥6 matured setups; Perp ≥100 outcomes và ≥6 trades mới).
Promotion dùng exact latest pass; Demo activation/account acceptance vẫn riêng.

Optional gate extension version 1 không đổi source schema v23, không rewrite v1
payload/raw evidence. V1-rejected rule có thể được chọn lại bằng campaign v2 mới,
nhưng v1 rejection còn nguyên. Campaign v2 không được fallback v1 để bypass.
Readiness GET không chạy replay hoặc DDL. Chi tiết/quy trình và giới hạn:
[runbook](replay-v2-runbook.md), [verification](replay-v2-gate-verification.md).

### Weekly gate policy — local 2026-10-02, chưa deploy

Optional policy Spot/Perp áp dụng cả future enabled routes. Background job poll
60 giây, đánh giá v2 khi minimum sẵn sàng rồi retry ≥7 ngày sau latest non-pass
evaluation cùng phase/campaign, kể cả chạy tay. Window tích lũy; collection/campaign
start, rules và v1 evidence không reset. Perp selection seal provenance prefix;
BTC giữ champion reference và candidate inheritance cùng parameters.

Durable cutoff/funding/evaluation ledger và OS lock độc lập collector, reconcile
crash thay vì append gate trùng. Public funding retry 3 lần/15 phút, không giả 0.
Hard-risk/binding error halt; validation reject kết thúc campaign. Pass dừng stage,
dashboard/alert chờ operator start validation/promote. Không auto-tune/model-call,
auto-transition/trading. Pause không fallback v1; GET không replay/DDL.
[Weekly runbook](weekly-gate-automation-runbook.md).

## 6. Demo execution và hard-risk

`ExecutionAdapter` tách signing, filters, quotes, account, order/fill và native protection
khỏi signal engine. Simulator là adapter/ledger tương thích; Binance Demo có transport
Spot và USD-M Perp riêng. Chưa có concurrent multi-venue capital routing.

Multi-route allocation do operator chọn: capital >0 và ≤10.000 USDT; Spot/Perp budget
60%/40%; effective per-sleeve notional caps 30%/20% operator capital.
Weights mỗi sleeve tổng ≤1; phần chưa phân bổ giữ reserve, không tự rebalance.
ATR có thể giảm Spot size; tăng leverage không tăng allocation notional.

| Chính sách | Multi-route Demo |
| --- | --- |
| Spot | USDT long-only; chỉ inventory từ confirmed AIGT fills, không nhận seeded holdings |
| Spot emergency stop | Mặc định operator-selected 10% dưới actual entry; native SELL stop; Donchian exit riêng |
| Perp | USD-M USDT; One-way, Single-asset, Isolated; leverage verified từng cặp 1–10x, default 3x |
| Gross / margin | Gross ≤50% strategy equity; isolated margin budget ≤10%, dùng actual leverage/initial margin |
| Loss guards | Daily loss 1,5%; high-water drawdown 8%; deterministic recovery/flatten/halt paths |
| Entry sanity | Fresh model/source/account/quote, filters, available cash/margin và Demo/mainnet dislocation |
| Unknown order | Intent persist trước submit; reconcile bằng reads, không retry submit mù |
| Protection | Stop failure/drift/pending reconciliation chặn entries; không bỏ bảo vệ để đổi settings |

Legacy BTC Demo và simulated parent paper có contract riêng (bao gồm fixed 3x).
Standalone intraday engine cũ có risk policy riêng; không nhập nhằng các margin/tranche
limits của thiết kế lịch sử với multi-runtime đang dùng.

`aigt perp ETH` đọc route, vị thế, mark/entry, unrealized PnL và leverage.
`aigt perp ETH -leverage`: nhập 1–10 → preview → Confirm/Cancel → venue write →
GET read-back. Cặp phải paused/flat/reconciled; margin mode không đổi tự động.
Dashboard queue có control token, settings controller riêng; web không cầm exchange key.
Leverage confirmation không activate trading; legacy fixed-3x cần chuyển multi khi flat/paused.

Fee/dust/third-asset fees, account cash drift, uncertain stop cancellation được giữ trong
journal và có thể pause để reconcile. Không sell số dư seeded hoặc bỏ evidence khó đối soát.

## 7. Vận hành, kiểm chứng và tài liệu chuẩn

- [Demo runbook](binance-demo-execution-runbook.md): native paths, isolated profiles,
  onboarding, allocation, exact evaluation activation và supervised acceptance.
- [Portfolio/soak runbook](shared-ai-portfolio-runbook.md): Docker global routing,
  legacy BTC parent evaluation, per-asset lifecycle và scope của simulator.
- [Provider runbook](intraday-provider-runbook.md): secret separation, model setup và health.
- [Roadmap](../tasks/roadmap.md), [plan](../tasks/plan.md), [checklist](../tasks/todo.md):
  trạng thái build/rollout/acceptance tách riêng.

VPS source volume là `/app/state/intraday/intraday.sqlite3`; native CLI mặc định XDG state.
Không suy ra hai path là cùng campaign. Demo/source/secret paths cần nhất quán giữa
host và container; execution commands không tự route vào admin deployment.

Backup CLI tạo online consistent artifact + SHA-256 manifest ngoài volume;
upgrade preflight mục tiêu v23 chỉ migrate/restore bản sao. Chưa có tự động backup
schedule/retention/production restore hoặc unified multi-coin Demo positions.
`aigt positions` hiện chỉ đọc BTC parent simulated ledger; không dùng để kết luận Demo flat.

Nghiệm thu Demo order-write/protection/close phải được operator cho phép riêng;
fake-exchange/browser tests và credentialed GET không chứng minh native-stop semantics.
Profile `demo` và Perp settings controller không tự start khi deploy worker/web.

## 8. Snapshot triển khai, không phải trạng thái realtime

Bằng chứng 2026-10-01 **05:30 UTC / 12:30 UTC+7**, implementation `29f05c8`:
worker/web deploy, schema v23, health endpoints HTTP 200 sau >5 phút; source data và
campaign starts được bảo toàn. BTC và ETH/HYPE/NEAR/ZEC/SOL Perp có success ticks mới.
Demo trading không được bật trong rollout đó.

BTC Spot 4h candidate 30/8 replay reject: return +4,93%, DD 11,03% ≥8%;
candidate không được đưa vào soak. Đây không phải kết luận về candidate tương lai.
Metadata artifact VPS (không nằm trong checkout):
`XDG_STATE_HOME/aigorithmic-trading/reports/vps-rollout-20261001T043018/final-health.json`.
Xem report/runtime mới trước mọi quyết định tiếp theo; snapshot này có thể lỗi thời.

## 9. Quyết định kiến trúc và lịch sử

- **Scoped lifecycle:** giữ symbol/scope riêng để không chuyển evidence hay quyền activation nhầm coin.
- **Execution modules:** đổi venue không thay model/rule engine; mỗi adapter chịu semantics của sàn.
- **Journal riêng, source read-only:** bảo vệ soak evidence và giữ reconciliation độc lập.
- **Operator approval:** model proposal và key connection không phải lệnh giao dịch.
- **UTC computation / UTC+7 UI:** không đổi candle boundaries chỉ vì timezone hiển thị.

[Thiết kế BTC Perp 2026-09-22](history/crypto-intraday-design-2026-09-22.md) là bản
lịch sử đã được supersede cho vận hành hiện hành; nội dung gốc vẫn được giữ.
[Research architecture](architecture.md) và [BTC daily lab runbook](btc-paper-runbook.md)
vẫn mô tả subsystem `lab/` độc lập, không phải execution architecture của AIGT.
