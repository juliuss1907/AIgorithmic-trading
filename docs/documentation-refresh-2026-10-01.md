# Đồng bộ tài liệu AIGT — biên bản 2026-10-01

Baseline code `29f05c8`, source schema v23. Phạm vi: README, thiết kế chuẩn,
roadmap/checklists/runbooks, System Plan, sơ đồ và PDF/DOCX/PNG được commit trong repo.
Không đổi trading/risk engine, database/schema, key, provider hoặc dịch vụ VPS.
Không push/merge/deploy hay activate execution trong đợt này.

## Nội dung và lịch sử

- [Kiến trúc chuẩn](crypto-intraday-system-design.md) mô tả multi-asset Spot/Perp,
  source evidence, scoped model/rule lifecycle và opt-in Binance Demo execution.
- [Roadmap](../tasks/roadmap.md) tách build/deploy/acceptance; giữ các việc chưa xong.
- Thiết kế BTC Perp gốc được bảo toàn nguyên nội dung trong `docs/history/`;
  research roadmap gốc giữ nội dung, chỉ thêm nhãn và rebase link tương đối.
- Tài liệu `lab/`, API research và các feature slice cũ có nhãn scope/historical.
- Replay Spot hiện chưa mô phỏng đầy đủ ATR sizing hoặc shared Demo risk;
  docs không hạ gate, đổi kết quả hay tuyên bố full-risk parity.
- Snapshot VPS được ghi ngày giờ riêng; không trình bày thành realtime status.

## Kiểm chứng

- `uv run --frozen pytest -q`: **725 passed**, 18 dependency deprecation warnings.
- Kiểm tra link tương đối của Markdown sửa đổi và lịch sử: không có broken target.
- CLI `--help`: root, assets rules, Demo configure và Perp shortcuts khớp parser hiện có.
  Không chạy lệnh activation, settings write hoặc đặt order để kiểm tra tài liệu.
- System Plan dùng database thử riêng ở `/tmp/`, Chrome profile tạm và CDP loopback:
  widths 320/768/1440/2048; không page horizontal overflow, iframe tải đúng,
  không runtime exception, failed network request hoặc HTTP error.
  Table/TOC vẫn có horizontal scroll riêng theo CSS hiện có.
- Test page kiểm tra source counts trước/sau GET không đổi. Không thêm route hay JS mutation.
- DOT render thành PNG; DOCX có hai figure và metadata multi-asset/v23;
  PDF 8 trang, kiểm tra trực quan hai figure và một trang nội dung.
- Self-review: scope permissions, cadence/runtime distinction, gate order,
  native-vs-Docker routing, journal isolation và historical evidence retention.
- `git diff --check`: pass.

## Archify artifact handoff

```text
diagram_type: architecture
output: /home/julius/julius-workspace/ai-labs/projects/system-trading/intraday/web_assets/static/system-trading-architecture.html
specification_sha256: 52ebcaf8f274ebd676fdabc71baba833ad738f1d3271c07085e98b740a6d4142
artifact_sha256: de4179a2339c2cecd582c38d3eac2e05de9f758ff65aa684e51bb31864e97f0f
validation: 9/9 showcase, 0 errors, 0 warnings
browser_evidence: passed
visual_review: passed
correction_rounds: 1
```

Authored source: `docs/diagrams/system-trading.architecture.json` (6296 bytes).
Delivered HTML: 814101 bytes. JSON source and trusted HTML hashes match the receipt.
Automated browser measurement covers light READ/Still at 1440×900, 1600×1000,
1920×1080 and 2048×1320; both themes captured at endpoint sizes. Perceptual review
is separate from automation: inspected light 1440×900 and dark 2048×1320 renders.
Automated receipt retains `visualReview: pending`; it does not claim perceptual review.

Local screenshots/receipts and synthetic database are in `/tmp/aigt-docs-check-sEwHYo/`.
These are temporary verification artifacts, not immutable market evidence or VPS backups.
Generated HTML is not hand-edited; fixed Viewer UI/locale remain English while
authored diagram content is Vietnamese. Archify package was not upgraded.
