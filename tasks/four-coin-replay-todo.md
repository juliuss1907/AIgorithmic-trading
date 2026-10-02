# Four-coin implementation checklist

- [ ] S1 Contracts and immutable snapshot: confidence69..100, old hashes unchanged;
  test boundaries/non-finite/isolation/integrity/source preservation.
- [ ] S2 Spot study: 24months,18/6 split, distinct setups, next-open replay;
  test deterministic selection, gaps, no holdout reselection, no source writes.
- [ ] S3 Perp study/review:90days,70/30 split, funding/provenance, model sees only
  training, one durable attempt/coin/week; test missing data and fractional rates.
- [ ] Checkpoint: focused tests/build, review artifact and risk separation.
- [ ] S4 CLI study/review/status and default-off Monday09UTC+7 scheduler;
  test deduplication, errors, competitor suppression and Docker/native routing.
- [ ] S5 Read-only dashboard proposal section: current/proposed, evidence/model,
  metrics/blockers/report links; test read-only API, escaping, UTC+7 and browser.
- [ ] S6 Four-coin data-backed study on separate research snapshot; record all
  results including deferred. No gate/campaign/activation/source mutations.
- [ ] Checkpoint: full pytest, build, diff check, scoped review; no push/deploy.
