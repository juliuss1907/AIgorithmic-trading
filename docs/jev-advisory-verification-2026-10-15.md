# Kiểm chứng Jev advisory — 2026-10-15

Hoàn tất triển khai và review local trên `feature/jev-advisory`, worktree `system-trading-jev-advisory`, từ
`cad8779`. Commit mã và test được kiểm toàn bộ: `80e9dfd65e509dd6766b5dfd9034252d382aed78`. Báo cáo này được commit riêng sau kiểm chứng.
Yêu cầu: `/home/julius/julius-workspace/tasks/inbox/note-2026-10-15-1653.md`.

## Hành vi đã giao

- `configure --jev-mode advisory` chỉ nhận profile `setup2_slots_v1`; cờ ở allocation Demo, không ở active set.
  `gate` vẫn là mặc định và serialize giữ nguyên từng byte cho cả ba profile.
- Luật và rủi ro quyết định entry Spot long/Perp short. Thiếu/lỗi Jev chờ tối đa 120 giây từ đóng H4 rồi vẫn chạy;
  có câu trả lời phản đối thì ghi audit, không chặn lệnh. Giá lệch tối đa 1% từ H4 close, spread tối đa 0,1%.
- Định danh theo `setup2:{rule_id}:{bar}` bền qua restart, flatten/reactivate và câu trả lời muộn; phân biệt `close:`.
  Đọc advice đúng route/rule/nến, không yêu cầu tuổi 45 giây/provenance; đọc lỗi trả unavailable.
- Audit ở kết quả, `slot_selection.steps` và `entry_jev`, cả nhánh stop bảo vệ thất bại. Rotation vẫn cần hơn 5 ADX;
  cổng PnL, slot 1/3, ba vị thế, vốn hai túi, gross/Perp/margin, lỗ ngày/DD và native stop giữ nguyên.
- Telegram soak ghi tín hiệu luật và ý kiến Jev; Demo đọc ý kiến tại reservation bất biến, thử lại có giới hạn và
  không gửi trùng. Không gửi Telegram thật. Status hiện `allocation.jev_mode=advisory`; không thêm giao diện riêng.
- ADR-007, checklist G5/G6 và Demo runbook mô tả flag, acceptance, audit và rollback paused + flat. Giữ nguyên
  phần lịch sử Jev của diễn tập trên `cad8779`, chỉ bổ sung câu dẫn tới quyết định mới.

## Kiểm thử

| Bộ kiểm | Kết quả |
|---|---|
| Toàn bộ trên `80e9dfd` | **1808 passed, 1 failed, 23 warnings**, 408.06 giây |
| Allocation/CLI/gate và hồi quy đầu | 46 passed |
| Runtime/source/slot/G5 trước review | 39 passed |
| Alerts và hồi quy | 36 passed |
| Sau sửa review, gồm `test_setup2_parity.py`, `test_setup2_slots_profile.py`, toàn bộ `test_slots_*` | 51 passed |
| Fixture A/B sau checkpoint, gồm test GC mới | 8 passed |
| Test lỗi stop Spot/Perp do reviewer chạy độc lập | 2 passed |

Các nhóm tập trung có phần giao nhau; không cộng để suy ra tổng test. Tổng có **60 ca mới** so với base.
Lỗi duy nhất còn lại đúng phạm vi được note cho phép:
`tests/test_perp_collection_migration.py::test_cli_inheritance_and_readiness_show_collection_without_changing_champion` — `gate_version` thực tế `v1`, test đòi `gate-v2.1`, cũng có trên base.
Không thêm skip/xfail hay nới assertion để đạt kết quả.

Lệnh toàn bộ dùng venv sẵn có, không sync/cài dependency:

```bash
PYTHONDONTWRITEBYTECODE=1 \
UV_CACHE_DIR=/tmp/jev-uv-cache \
UV_PROJECT_ENVIRONMENT=/home/julius/julius-workspace/ai-labs/projects/system-trading/.venv \
PYTHONPATH=/home/julius/julius-workspace/ai-labs/projects/system-trading-jev-advisory \
uv run --frozen --no-sync pytest -q -p no:cacheprovider
```

Giữ đầy đủ log của các lần thất bại: lượt đầu 1.805 pass/1 lỗi cũ; lượt xác nhận trước sửa fixture
1.806 pass/2 lỗi (lỗi cũ và hash WAL). Lượt cuối ở bảng trên đã chạy sau cả sửa review lẫn fixture.

## Replay và ranh giới bất biến

Tái chạy hai đối chứng H4 và tám ca slot 4/5 coin 2026 từ input đã đóng băng, cửa sổ
2026-01-01 tới 2026-10-02. **10/10 `result_id` và summary khớp** các comparison gốc.
Đối chiếu summary sau chuẩn hóa khóa JSON như định dạng artifact; không sửa số liệu để khớp.
Hash input: `fe09301c47264605e6fb2a0a2848f15ef5063ff9157f685d29a247358743b8a9`. Nguồn: `/home/julius/.local/state/aigorithmic-trading/reports/donchian-adx-five-20261005/approved-data/inputs.json`.
Đối chứng: `donchian-rotation-2026ytd-20261012/runs/comparison.json` và
`donchian-rotation-five-2026ytd-20261013/runs/comparison.json` trong report root local.
Receipt có checksum dataset và ba journal của từng ca; kiểm lại lineage trước/sau replay.

| Ca tham chiếu | `result_id` không đổi |
|---|---|
| `ETH-NEAR-SOL-H4` | `b99e0c1b3885a3b06a8158df0e1b7e8f09167b02453c48ebf7d4153639f04c36` |
| `ETH-NEAR-SOL-H4-cost2x` | `b404fd46a114937ff3dbeabdac54e29d2578ff78336f7ec188b3c422db213508` |
| `ETH-NEAR-SOL-ZEC-ROT-SLOT` | `7ead20459f2ded4f672347cc245b1be07903d050d82220f3546dcf59cd281376` |
| `ETH-NEAR-SOL-ZEC-ROT-SLOT-cost2x` | `02857ed610e9da55d1f6c27afd67e2789face50862c38c529d364292cc812e8c` |
| `ETH-NEAR-SOL-ZEC-ROT-SLOT-W25` | `a175c74f9c2156c7a942b0d5da9150af3d333c525589b56f1d0d23e6bb1f3992` |
| `ETH-NEAR-SOL-ZEC-ROT-SLOT-W25-cost2x` | `68d9022a6dfd366aa6bf376e9d51674ca9bbab8bdfa5157b6b9f98e86cd9446d` |
| `BTC-ETH-NEAR-SOL-ZEC-ROT-SLOT` | `d0c3c35d91efccdd793bd99255cdf7a4000c7c4bef02f041127a6488e97b9cbc` |
| `BTC-ETH-NEAR-SOL-ZEC-ROT-SLOT-cost2x` | `9d350d6286d9959d0922ffb9c1db7e413d89223a2d9bd0b6d336dde9629fd5a4` |
| `BTC-ETH-NEAR-SOL-ZEC-ROT-SLOT-W25` | `8c9ebcfa4f8d556be296e9e9f60a83897d236d99b9b0162b03db4e97b2af38e7` |
| `BTC-ETH-NEAR-SOL-ZEC-ROT-SLOT-W25-cost2x` | `1928fbb0c9e82d4e5f2039e9f7135357ab9cff372d539086d537a736b25ebb27` |

SHA-256 của **99 file** replay/contracts/active_set/setup2/lifecycle giữ nguyên. So sánh nguyên văn các method
`MultiDemoRuntime._cycle` (đường per-route cũ), `ScopedEntryGate.perp_entry` và `ScopedEvidenceSource.latest_decision`
với base cũng khớp. Candidate mẫu cố định giữ cả payload bytes lẫn content hash:

| Profile/market | `content_hash` |
|---|---|
| `setup2_v1:spot` | `146e4de799fe67efb4245eb609364791b52ef79b5484c62fb31d0f784aef0fd0` |
| `setup2_v1:perp` | `060bf7ea425a711977167f3ed42f23db061b9dca7697902e57024f69b9b2d86a` |
| `setup2_slots_v1:spot` | `acf3fbd93c699bddfce10524555d4d5cdfd42ed960bd1a8a2907665bb8e85dea` |
| `setup2_slots_v1:perp` | `cf2f1f8682184ccffaf144e3f4a9223fa07f6290f66e5e16f0dfc13e3cc902ef` |

Không đọc lại trạng thái VPS trong task này; bằng chứng trên là mã nguồn và fixture local, không phải xác minh
soak live. Worktree `prep/g5-demo` vẫn sạch; workspace gốc giữ nguyên ba mục untracked có sẵn.

## Standards

Review độc lập ban đầu: 0 vi phạm bắt buộc, 1 nhận xét P3 về dùng sự có mặt của `jev` làm cờ chế độ.
Đã đổi sang truyền `advisory` tường minh trong `0bdf349`. Reviewer kiểm lại: **0 finding còn mở**.
Review thêm `80e9dfd`: **0 finding**. Báo cáo chi tiết nằm trong receipt `review.md`.

## Spec

Review độc lập ban đầu: 1 P2 — mất selection audit/kết quả khi stop bảo vệ lỗi. Test Spot/Perp tái hiện trước sửa;
`0bdf349` lưu audit trước mutation và giữ kết quả khi pause. Reviewer chạy lại hai test: qua; **0 finding còn mở**.
Review thêm fixture `80e9dfd`: hợp phạm vi để đáp ứng cổng full suite; **0 finding còn mở**.

Hai trục đều đã đóng hết finding: Standards 1 P3 đã sửa; Spec 1 P2 đã sửa.

## Khác kế hoạch và lý do

1. Mở giới hạn `OrderIntent.source_id` 128 → 160: khóa bắt buộc phải chứa `rule_id` hợp lệ dài 128 ký tự cùng
   prefix/nến. Test dùng đủ 128 ký tự chứng minh cần thay đổi; payload cũ không đổi.
2. Chỉ advisory truyền aggregate Perp với dấu âm vào coordinator: dấu dương cũ khiến short thứ hai bị hiểu là
   flip, fixture ban đầu chỉ mở một vị thế. Test nay mở được ba short và xoay đúng. Giới hạn rủi ro và gate cũ giữ nguyên.
3. Ổn định fixture `test_compact_ab.py`: lỗi hash tái hiện cả ở base `cad8779`. Ba lần chẩn đoán cho thấy GC tự
   checkpoint WAL làm đổi bytes nhưng dump logic y nguyên, ngay cả khi chưa gọi nghiên cứu. Checkpoint/truncate
   trước lúc chốt checksum, đóng connection tường minh, thêm regression; không đổi mã replay hay bỏ assertion.
4. Đọc ý kiến Telegram từ reservation theo đúng entry ID thay vì route hiện tại để retry sau rotation không gắn nhầm
   ý kiến. Dùng bảng event có sẵn; không đổi schema. Lỗi đọc/format tùy chọn không làm hỏng thông báo fill.

## Commit local

```text
5fbcab8 feat(execution): add explicit Jev advisory policy
7e49bfb feat(execution): run slot entries with bounded Jev advice
bdf4d2d feat(alerts): show Jev advice without implying an entry veto
a94b4a1 docs(execution): record advisory rollout and rollback policy
0bdf349 fix(execution): retain advisory audit on protection failure
80e9dfd test(replay): checkpoint A/B fixture before freezing checksum
```

Commit chứa báo cáo/CHANGELOG kiểm chứng được thêm sau danh sách trên; không có sửa mã chạy sau test cuối.

## File đã đổi

- [CHANGELOG.md](../CHANGELOG.md)
- [docs/binance-demo-execution-runbook.md](../docs/binance-demo-execution-runbook.md)
- [docs/decisions/007-slot-rotation-five-coin-runtime.md](../docs/decisions/007-slot-rotation-five-coin-runtime.md)
- [docs/jev-advisory-verification-2026-10-15.md](../docs/jev-advisory-verification-2026-10-15.md)
- [docs/runbooks/slot-runtime-demo-rollout-checklist.md](../docs/runbooks/slot-runtime-demo-rollout-checklist.md)
- [intraday/execution/allocation.py](../intraday/execution/allocation.py)
- [intraday/execution/cli.py](../intraday/execution/cli.py)
- [intraday/execution/contracts.py](../intraday/execution/contracts.py)
- [intraday/execution/journal.py](../intraday/execution/journal.py)
- [intraday/execution/multi_runtime.py](../intraday/execution/multi_runtime.py)
- [intraday/execution/scoped_source.py](../intraday/execution/scoped_source.py)
- [intraday/scoped_gate.py](../intraday/scoped_gate.py)
- [intraday/trade_alerts.py](../intraday/trade_alerts.py)
- [tests/test_compact_ab.py](../tests/test_compact_ab.py)
- [tests/test_jev_advisory_alerts.py](../tests/test_jev_advisory_alerts.py)
- [tests/test_jev_advisory_gate.py](../tests/test_jev_advisory_gate.py)
- [tests/test_jev_advisory_runtime.py](../tests/test_jev_advisory_runtime.py)
- [tests/test_jev_advisory_source.py](../tests/test_jev_advisory_source.py)
- [tests/test_setup2_runtime.py](../tests/test_setup2_runtime.py)

## Bằng chứng và việc còn lại của Julius

Receipt local: `state/verification/jev-advisory-20261015T114503Z` (gitignored), giữ cả log đỏ, lỗi fixture ban đầu, replay, snapshot hash, review và script
tái lập. [Manifest](../state/verification/jev-advisory-20261015T114503Z/manifest.json), SHA-256 **`4c9c788d9996303c9075bbae4f0ba8d56b8522048465f7996018843bdd809d87`**.

Không còn quyết định thiết kế Jev cần chờ: Julius đã chọn advisory. Push/deploy, key/tài khoản thật, soak đủ điều kiện,
G5 configure/activate và G6 chạy Demo vẫn là các bước được duyệt riêng theo checklist; task này không thực hiện.
Không chạm VPS/Binance, file env/secrets hay `.hermes/MEMORY.md`; không push, deploy, xóa file hoặc đổi soak/active set.
