# Four-coin implementation verification — 2026-10-02

Scope: local implementation, isolated research; no push/merge/deploy/activation.
Approved [plan](../tasks/four-coin-replay-plan.md). No exchange orders/settings changed.

## Automated and browser checks

- `uv run pytest -q`: **876 passed**,18 upstream deprecation warnings,31.84s.
- `uv build`: wheel and source distribution built successfully.
- `git diff --check`: passed.
- Chrome headless in a separate temporary profile, isolated fixture DB/server.
  Chrome DevTools MCP was unavailable, so used Chrome's local CDP directly.
  Widths320/768/1024/1440: proposal table visible in Perp, no page overflow,
  zero console warnings/errors and failed requests; keyboard Tab focus works.
  AX tree inspected; not a full accessibility certification.
  Source85% vs fixture proposal73.2% stays separate; untrusted `<script>` rendered
  as text, zero inserted scripts; displayUTC+7. Fixture is not a real proposal.
- Browser check JSON/screenshots retained at `/tmp/aigt-four-coin-replay-mlOHG8/`
  (`browser-verification.json`, `confidence-320.png`, `confidence-1440.png`).

Tests cover finite confidence bounds, fractional/above85 values, exact runtime
filter, archived rule/hash preservation, strict structured model output, source
immutability, calendar windows, variant deduplication, training-only selection,
no holdout reselection, missing provenance/outcomes/funding/configuration, durable
weekly intent, pending competition blocking/dismissal, default-off scheduler,
generic Perp suppression, shared report volume, CLI routing and read-only web API.

## Actual data-backed batch

Source: existing verified VPS research snapshot, downloaded read-only; no live
source writes/container restarts. Local input:
`/tmp/aigt-four-coin-replay-mlOHG8/source.sqlite3`, SHA256 before/after:
`e0b6586d79b7cc5aace31d6fb065c141425050ce8f795e6c2514031c0488b825`.

Root:
`/home/julius/.local/state/aigorithmic-trading/reports/four-coin-research-20261002`.
Manifest: `studies/111bd8b59fc446b68b5f909b9e491b4b/manifest.json`.
Research cutoff2026-10-02T02:00Z, available in the snapshot. SQLite baseline
integrity `ok`; checksum `d61ee8fa10ddc32274540b60985d04c0fa15285690da83d3a32d061c3a99d119`.
Manifest cũ ghi research-working-file checksum lúc collection kết thúc:
`e58d4dcd6e09be25ac56c97ecab6a5268bec82c8663bc1ef83ae7bc8cf16a159`.
Backup pages can differ from original file pages; these hashes are separate bindings.

Working copy được mở lại trong bước chẩn đoán provider sau publish, nên raw-file
checksum hiện không còn khớp manifest cũ. **Không coi hash đó là final immutable
evidence** và không rewrite manifest để che mismatch. Baseline checksum vẫn khớp;
cả12 Spot bundles và dataset checksums đã được kiểm chứng lại từ read-only working
copy. Source original vẫn giữ nguyên hash. Đây là lỗi binding working-file, không
phải một thay đổi strategy hoặc mất dữ liệu soak. Đã sửa producer để batch mới
bind `research_evidence_database` tới final SQLite backup riêng; regression test
xác minh sửa working copy sau publish không làm đổi evidence snapshot.

Native closed Spot4h window2024-10-02T00:00Z →2026-10-02T00:00Z;
selection ends2026-04-02T00:00Z. Independent1000USDT accounts, fee10/slip5bps/fill.

| Coin | Setup | Selection net | DD | Closed trades | Result |
|---|---|---:|---:|---:|---|
| ETH | 30/8 (existing, deduplicated) | +3.913% | 4.622% | 8 | selected |
| NEAR | 30/8 | +2.689% | 3.376% | 3 | deferred |
| NEAR | 40/8 | +2.143% | 2.277% | 1 | deferred |
| NEAR | 50/8 | +2.143% | 2.277% | 1 | deferred |
| ZEC | 30/8 | +2.299% | 5.031% | 3 | deferred |
| ZEC | 40/8 | +1.055% | 4.860% | 3 | deferred |
| ZEC | 50/8 | +0.852% | 4.828% | 3 | deferred |
| SOL | 24/8 (existing) | −0.186% | 2.668% | 3 | reject |
| SOL | 30/8 | −0.186% | 2.668% | 3 | reject |
| SOL | 40/8 | +1.295% | 2.130% | 2 | deferred |
| SOL | 50/8 | +1.295% | 2.130% | 2 | deferred |

ETH holdout30/8: **net+3.163621%, DD3.102515%,9 closed trades** → research pass.
RunID `0dc62453de4349bf894b362228a0e16e`; net31.636209USDT, fee5.348185 and
slip2.674092USDT. Eight Donchian exits, one daily-loss guard exit; simulation
halted at daily_loss_limit and stayed halted. Entry audit has no hard violation;
marked exposure can grow above entry cap as price changes. This is not formal
gate pass, not Jev validation, and not a claim that trading is activated.

Other three coins have no qualifying selection candidate (minimum6 closed
trades; negative variants also rejected). **No holdout was run for them** and
no alternative setup was chosen from holdout outcomes. More history does not
guarantee many trades when terminal daily-risk guards halt the simulation.

## Perp: model review is configuration-blocked

Recorded evidence loaded with coverage≈99.33–99.43%,4083–4085 verified training
decisions per coin; public funding and deterministic selection-frontier reports
were generated. No LLM confidence proposal or Perp holdout comparison was produced.

The snapshot's active LLM profile has no matching secret in the machine's local
ProviderSecretStore. Factory raised `StructuredLLMError(missing_secret)` **before
any model request**. No provider fallback, credential copy, source migration or
fake proposal was used. Local default source DB is older than v23 and was not
migrated to work around that mismatch.

The immutable batch manifest predates the clearer error mapping and retains
`error / review_failed:StructuredLLMError` for those routes. Current implementation
maps this known configuration failure to `deferred / llm_provider:missing_secret`,
covered by a regression test; the old artifact was not rewritten. Weekly claims
remain in the report sidecar; do not change report root to bypass the budget.

To complete real LLM proposals, configure the matching active profile/secret in
the intended runtime, then review in an eligible weekly slot. VPS deployment or
credential transfer needs a separate operator decision. Nothing here starts
formal replay gates, fresh validation campaigns, Demo trading or HYPE cleanup.
