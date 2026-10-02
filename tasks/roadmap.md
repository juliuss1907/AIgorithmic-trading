# Roadmap hiện hành — AIGT multi-asset Spot/Perp

Cập nhật 2026-10-01; baseline code `29f05c8`, source schema v23.
[Kiến trúc chuẩn](../docs/crypto-intraday-system-design.md) ·
[Plan](plan.md) · [Checklist](todo.md).

**Đã xây ≠ đã deploy ≠ đã nghiệm thu/kích hoạt.** Không dùng checkbox build làm quyền trade.
Roadmap này thay hướng SPY/ETF/BTC-only trước đây; bản gốc nằm ở
[research roadmap lịch sử](../docs/history/research-roadmap-2026-09-16.md).
Subsystem `lab/` vẫn giữ dữ liệu, control và tests độc lập.

## 1. Nền tảng đã xây

| Hạng mục | Code hiện có | Giới hạn / nghiệm thu còn lại |
| --- | --- | --- |
| Market/news plane | Multi-symbol Binance; DEX/context/news collectors, provenance/TTL | DEX là evidence, không phải execution adapter |
| Jev + LLM | Perp primary, Spot setup, hourly thesis, bounded proposals | Không model nào tự promote hoặc activate |
| Rule lifecycle | Baseline/champion/challenger theo symbol/scope; replay và soak gates | Passing evaluation phải thuộc đúng coin/scope |
| Catalog/onboarding | Add → scan volume/liquidity → chọn venue, Spot/Perp riêng; v23 | Route selection không bật trading |
| Binance Demo module | USDT Spot và USD-M Perp đa coin, journal và allocation riêng | Supervised order/stop/close acceptance chưa hoàn tất |
| Per-pair leverage | CLI/dashboard confirmation, controller opt-in, read-back | Real Demo setting-write acceptance chưa hoàn tất |
| Per-asset readiness | CLI/API và `/assets`; scoped gates, blockers, pre/post-cutoff, lower bound, selected venue | Chỉ rule + venue; chưa deploy/không cấp quyền execution hoặc kiểm tra account |
| Operations | Dashboard, readiness report, backup/verify và upgrade rehearsal | Chưa tự schedule/retain/restore backup |
| Simulator / research | BTC parent paper và `lab/` | Không đại diện cho vị thế Demo đa coin |
| Replay v2 research | Offline single-coin Spot/Perp, ledger/risk, versioned profile, CLI/artifacts và read-only `/replay` (local) | Không thay gate v1; funding/filters/Demo-price limitations rõ; chưa deploy |
| Gate v2 | Versioned evaluations/campaigns, separated fees/slippage, CLI/readiness, exact promotion binding (local) | Validation 14 ngày mới; không rewrite v1/auto trading; chưa deploy |

Code supports coin mới nếu catalog và Binance Demo hỗ trợ, không còn ETH-only bootstrap.
[Demo plan](binance-demo-execution-plan.md) và
[leverage plan](perp-leverage-control-plan.md) giữ bằng chứng từng slice.

## 2. Trạng thái rollout đã kiểm chứng

Snapshot **2026-10-01 05:30 UTC**, không phải realtime:

- VPS worker/web chạy baseline `29f05c8`, source schema v23; migration giữ evidence.
- BTC và ETH/HYPE/NEAR/ZEC/SOL Perp tiếp tục decision-only soak; starts không reset.
- BTC Spot 4h candidate 30/8 bị replay reject (DD 11,03%); không start soak cho candidate đó.
- Demo execution và leverage controller **không được start trong rollout này**; không gửi order.
- Report: `XDG_STATE_HOME/aigorithmic-trading/reports/vps-rollout-20261001T043018/final-health.json`.

Không hardcode các trạng thái này thành UI realtime. Kiểm tra source DB/journal/service trước action.

## 3. Các bước vận hành còn lại — không phải build thêm adapter

1. Theo dõi 5 Perp decision soak ≥14 ngày và đủ outcome/coverage.
2. Replay Perp tại cutoff rồi validation trên evidence riêng: v1 ≥72 giờ,
   campaign v2 ≥14 ngày mới; không dùng lại tập replay.
3. Với Spot, cần candidate pass replay trước khi operator start soak ≥14 ngày và đủ setup.
4. Supervised Binance Demo acceptance: read/preflight → order → protection → reconcile → close,
   chỉ sau approval và đúng champion/evaluation. Tách Spot/Perp, không vượt gate để test chiến lược.
5. Settings-only Demo leverage acceptance khi paused/flat; không activate trading bằng đổi leverage.
6. Tiếp tục Hermes/Telegram read-only rollout riêng; không đặt add-on vào hot path.

Các mốc là minimum đánh giá (v2 Perp ≥14 + 14 ngày), không phải lời hứa ngày bật giao dịch.
Không tự activate khi đủ lịch hoặc khi LLM đưa proposal.

## 4. Trạng thái các hạng mục xây tiếp

| Ưu tiên | Hạng mục | Acceptance cần đạt |
| --- | --- | --- |
| 1 — đã xây local | Replay v2 Spot/Perp và versioned gate | ATR/allocation/stop/loss/funding assumptions inspectable; gate/campaign riêng. Còn rollout/acceptance; không rewrite evaluation cũ |
| 2 — đã xây local | Soak readiness theo coin/scope | CLI/API/dashboard đã có; rollout/kiểm chứng source VPS là bước riêng, chưa deploy |
| 3 | Unified Demo portfolio/positions | Đọc execution journal đa coin, open orders/stop/PnL/reconciliation; tách simulator |
| 4 | Backup/alerts vận hành | Schedule ngoài volume, verification, retention/restore drill riêng; cảnh báo protection/cash drift/stale |
| 5 | Hyperliquid execution adapter | Giữ interface/journal isolation; spec/testnet acceptance trước mainnet |
| Sau | Concurrent multi-venue allocation / live trading | Quyết định mandate, capital, reconciliation, launch approval riêng |

Các hạng mục chưa xây là backlog định hướng, chưa cấp quyền triển khai hoặc deploy.
Readiness đã xây local theo [plan riêng](asset-readiness-plan.md), chưa được deploy.
Replay research theo [plan riêng](replay-v2-plan.md), gate theo
[gate plan](replay-v2-gate-plan.md)/[runbook](../docs/replay-v2-runbook.md), chưa deploy.
Research report không phải gate evaluation; gate pass chỉ cho phép explicit validation,
không activate Demo.
Không tối ưu tham số để ép một gate pass; rejection là evidence hợp lệ.

## 5. Quy tắc cập nhật roadmap

- Kiến trúc lấy từ code; operational snapshot phải có timestamp và report.
- Checklist tách build, deploy, credentialed acceptance và activation.
- Không xóa nhiệm vụ chưa xong, đổi kết quả backtest hoặc ghi trạng thái chạy từ một plan cũ.
- Tài liệu nghiên cứu lịch sử gắn scope/nhãn, không được dùng làm runbook crypto hiện hành.
