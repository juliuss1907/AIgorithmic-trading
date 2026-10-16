# Jev Setup-2 review v2: collect và đánh giá

**Hoàn tất đợt 2. Chưa có bằng chứng A có ích:** trên kiểm 2026, net PnL
197,72 USD → 132,77 USD, DD tổng 1,1766% → 0,6071%; CI chênh lệch TB/lệnh
[-3,6510; 2,3054] USD chứa 0. Chi phí collect 0,016022832 USD / trần 1 USD.

Đợt tiếp theo sau [kiểm chứng đợt 1](jev-setup2-review-replay-2026-10-16.md).
Julius đã duyệt gọi thật với trần 1 USD và đánh giá theo mục 7 của note gốc.
Không merge, push, deploy hay thay đổi runtime/soak.

## Câu hỏi v2 và dry-run trước gọi thật

Chỉ bỏ câu `RSI extremes alone do not veto a multi-day trend.` khỏi phần dẫn
chung; không thêm câu dẫn nào. `QUESTION_SET_VERSION=setup2-review-questions-v2`.
Test khóa hash toàn bộ ba câu hỏi sau thay đổi:
`ee1b7367ad9228d4f1accd94e75515052468354b0da0a473b40e6125468fab60`.

Dry-run v2: **796 entry bars, 379 state, 379 khóa thiếu, 0 network calls**.
Ước tính 0,012571272 USD; dự toán byte + overhead với hai retry 0,199685052 USD.
Đây là dự toán, không phải chi phí đo thực tế.

- Receipt: `~/.local/state/aigorithmic-trading/jev-review/collect-dry-run-20261016-v2.json`
- SHA256 file: `0b73a473aa82fd0fc68e8aab932fd9aae160d0c774fd548bd8fc19cd6a863683`
- `plan_hash`: `18c49bd7cdea2f78eb080ea18eb20cd21cf146575e7f61b346771f0de4939e7a`
- `request_set_hash`: `7f7d654a4b6c265bcc089af2e80dfdbae78051ab97b7fdfffbdc2ae4b65d854b`

Trước collect, thêm sổ đặt chỗ chi phí append-only trong cache riêng. Mọi lần
gọi, retry, resume đều tính vào trần 1 USD; khi ngắt không mất khoản đã đặt chỗ.
Nguồn giá đã đọc lại ngày 16/10:
[OpenRouter endpoint metadata](https://openrouter.ai/api/v1/models/typesafe/jev-1.13/endpoints)
và [TypeSafe models](https://docs.typesafe.ai/models): 0,042 USD/triệu input token,
output miễn phí. Đặt chỗ theo context tối đa 262.144 token mỗi call, chỉ giải phóng
phần dư khi có usage hợp lệ. Thiếu usage giữ nguyên khoản đặt chỗ.

[Giao thức đánh giá](jev-setup2-review-evaluation-protocol-2026-10-16.md) chốt
16 tổ hợp, tiêu chí chọn/tie-break, chia kỳ, bootstrap và calibration trước khi
có câu trả lời thật. Cấu hình thắng tuning sẽ được ghi và khóa trước test.

Kiểm chứng trước collect: 24 test encoder/cache/plan và 18 test provider/CLI/budget
qua. Chưa sửa thêm bất kỳ file nào trong 99 file source được bảo vệ ở bước này.

Commit v2 trước inference: `7e04378`; commit bộ đánh giá/khóa ngưỡng và sổ chi phí
cố định: `34f4728`. Hai reviewer độc lập đã kiểm lại Spec/Standards, không còn
finding mở. Finding về việc đổi đường dẫn answer cache có thể đặt lại budget
được sửa bằng sổ riêng `~/.local/state/aigorithmic-trading/jev-review/collection-costs.sqlite3`.
Lượt collect đầu giữ cùng answer cache và ledger từ đầu tới cuối; các khoản đó
được chuyển bằng ID bất biến trước lần resume. Không xóa hay ghi đè receipt cũ.

## Kết quả collect thật

**379/379 state v2 hợp lệ, 0 khóa thiếu; 380 request; tổng chi phí provider báo 0.016022832 USD** / trần 1 USD. Không còn khoản đặt chỗ chưa quyết toán. Lượt đầu 378 thành công, một phản hồi sai định dạng; lượt bổ sung chỉ gọi một khóa thiếu và thành công. Không đưa phản hồi lỗi vào cache ý kiến.

Model trả về: `typesafe/jev-1.13-20260917` cho cả 379 câu trả lời. Receipt giữ ở `~/.local/state/aigorithmic-trading/jev-review/collect-execute-20261016-v2-attempt{1,2}.json`; cả hai ràng buộc approved plan v2 đã nêu trên.

- Cache thật và bản đóng băng `evaluation-v2-20261016/answers-v2-frozen.sqlite3`: SHA256 `cc9b2e6fe60eadfd337fda2aa86673f984a2fe348daeb67b7a3d3e79e6bdf38e`.
- Hash tập câu trả lời dùng: `5fc12e15cbbf72e92e42eaa26d7d178d85e68bd9f76797965f39c55cfd12593c`.
- Database nguồn và bản nghiên cứu giữ nguyên SHA256; journal signals/model_calls/gate evaluations/campaigns giữ nguyên số bản ghi. [Receipt](../state/verification/jev-review-v2-20261016/data-boundary-v2.json).

## Ngưỡng khóa và kết quả theo kỳ

Chọn đúng 16 tổ hợp theo giao thức, chỉ dùng PnL tuning trước 2026. Cấu hình thắng:
```json
{
  "t_dis": "0.5",
  "t_exh": "0.7",
  "t_reduce": "0.5",
  "t_skip": "0.7",
  "version": "setup2-review-labels-v1"
}
```
Lock được ghi lúc `2026-10-16T12:10:29.157052+00:00`, trước replay test; `lock_hash=fab1a99cce633aa8d9725d7451d64994315cfcd719fae67169bc5d30aede383e`. SHA256 file khóa `46f008ed49e38b72c828d425083366e8b55e18994620f6bb50f6a1ba375d359c`. SHA256 receipt tuning `0a1981401051ab079f3081f64b514e7ee2192b26540ae0af1d7a31b218492b80`; lệnh test kiểm cả hash này và hash lock. Toàn bộ 16 điểm tuning nằm trong file khóa, không có lần chọn ngưỡng thứ hai.

Vốn 1.000 USD/route, tổng 10.000 USD. Tuning kết thúc trước 2026-01-01; test bắt đầu 2026-01-01, kết thúc trước 2026-10-14 UTC. Mỗi kỳ bắt đầu flat và đóng cuối kỳ. DD tổng của các tài khoản độc lập; không phải DD danh mục slot.

| Kỳ | Nhánh | Lệnh | Net PnL USD | DD tổng | TB/lệnh USD | Winrate |
|---|---|---:|---:|---:|---:|---:|
| tuning | rule_only | 237 | 171.7770 | 0.9684% | 0.7248 | 36.71% |
| tuning | reviewed | 237 | 65.4108 | 0.6825% | 0.2760 | 36.71% |
| test | rule_only | 103 | 197.7194 | 1.1766% | 1.9196 | 35.92% |
| test | reviewed | 103 | 132.7652 | 0.6071% | 1.2890 | 35.92% |

Bootstrap tuning: chênh lệch TB/lệnh A − luật = **-0.4488 USD**, CI 95% **[-1.7482, 0.7968] USD**; 10.000 mẫu, seed 20261016, lấy mẫu riêng theo route/nhánh, giữ cỡ mẫu mỗi nhóm.

Bootstrap test: chênh lệch TB/lệnh A − luật = **-0.6306 USD**, CI 95% **[-3.6510, 2.3054] USD**; 10.000 mẫu, seed 20261016, lấy mẫu riêng theo route/nhánh, giữ cỡ mẫu mỗi nhóm.

**CI test chứa 0: chưa có bằng chứng A có ích.** Không dùng điểm PnL đơn lẻ để kết luận cải thiện hoặc thay đổi runtime.

### Từng route: tuning

Mỗi ô ghi **luật → A**. Nhãn trên tất cả tín hiệu (kể cả khi đang có vị thế): `{"AGREE": 32, "REDUCE": 506}`.

| Route | Lệnh | Net PnL USD | DD route | TB/lệnh USD | Winrate |
|---|---:|---:|---:|---:|---:|
| ETHUSDT spot | 25 → 25 | 34.6727 → 8.2984 | 4.8030% → 3.3296% | 1.3869 → 0.3319 | 24.00% → 24.00% |
| ETHUSDT perp | 35 → 35 | 0.8447 → 0.4610 | 4.6419% → 2.3726% | 0.0241 → 0.0132 | 34.29% → 34.29% |
| HYPEUSDT spot | 0 → 0 | 0.0000 → 0.0000 | 0.0000% → 0.0000% | — → — | — → — |
| HYPEUSDT perp | 8 → 8 | -15.6772 → -7.8622 | 3.9816% → 2.0178% | -1.9597 → -0.9828 | 12.50% → 12.50% |
| NEARUSDT spot | 27 → 27 | -2.7838 → -13.5611 | 3.8818% → 3.5751% | -0.1031 → -0.5023 | 37.04% → 37.04% |
| NEARUSDT perp | 30 → 30 | 41.7160 → 39.3362 | 3.4147% → 1.7409% | 1.3905 → 1.3112 | 40.00% → 40.00% |
| SOLUSDT spot | 26 → 26 | 20.8755 → 1.6038 | 4.5081% → 2.6423% | 0.8029 → 0.0617 | 38.46% → 38.46% |
| SOLUSDT perp | 31 → 31 | 23.6385 → 8.0843 | 3.4769% → 1.7733% | 0.7625 → 0.2608 | 48.39% → 48.39% |
| ZECUSDT spot | 34 → 34 | 58.8907 → 24.2080 | 3.9219% → 2.4489% | 1.7321 → 0.7120 | 38.24% → 38.24% |
| ZECUSDT perp | 21 → 21 | 9.6000 → 4.8425 | 2.7791% → 1.4116% | 0.4571 → 0.2306 | 38.10% → 38.10% |

### Từng route: test

Mỗi ô ghi **luật → A**. Nhãn trên tất cả tín hiệu (kể cả khi đang có vị thế): `{"AGREE": 16, "REDUCE": 242}`.

| Route | Lệnh | Net PnL USD | DD route | TB/lệnh USD | Winrate |
|---|---:|---:|---:|---:|---:|
| ETHUSDT spot | 11 → 11 | -14.8308 → -12.9824 | 3.7028% → 2.4192% | -1.3483 → -1.1802 | 36.36% → 36.36% |
| ETHUSDT perp | 11 → 11 | 53.5778 → 26.7889 | 2.3612% → 1.2118% | 4.8707 → 2.4354 | 45.45% → 45.45% |
| HYPEUSDT spot | 0 → 0 | 0.0000 → 0.0000 | 0.0000% → 0.0000% | — → — | — → — |
| HYPEUSDT perp | 12 → 12 | -27.3253 → -15.0466 | 4.0057% → 2.1559% | -2.2771 → -1.2539 | 16.67% → 16.67% |
| NEARUSDT spot | 13 → 13 | 96.6107 → 92.4919 | 2.4807% → 1.6801% | 7.4316 → 7.1148 | 61.54% → 61.54% |
| NEARUSDT perp | 9 → 9 | 8.4337 → 4.1013 | 1.7726% → 0.8980% | 0.9371 → 0.4557 | 55.56% → 55.56% |
| SOLUSDT spot | 13 → 13 | -5.7671 → -5.1060 | 3.1356% → 1.8047% | -0.4436 → -0.3928 | 30.77% → 30.77% |
| SOLUSDT perp | 11 → 11 | 21.4278 → 10.8001 | 2.8228% → 1.4464% | 1.9480 → 0.9818 | 27.27% → 27.27% |
| ZECUSDT spot | 13 → 13 | 91.9179 → 45.9765 | 2.9742% → 1.5376% | 7.0706 → 3.5367 | 30.77% → 30.77% |
| ZECUSDT perp | 10 → 10 | -26.3254 → -14.2586 | 2.7129% → 1.4683% | -2.6325 → -1.4259 | 20.00% → 20.00% |

## Phản thực của nhóm VETO/REDUCE

Chỉ đếm các tín hiệu có lệnh trong baseline hoặc đã thử/vào lệnh ở A. `paired_rule_only` là lệnh baseline thực sự; `isolated_flat_start` là lệnh độc lập bổ sung cho đường vị thế đã phân kỳ. Tổng dưới đây mô tả nhóm, không phải tiền lời có thể cộng thêm vào portfolio.

| Kỳ | Nhãn | Phương pháp | Số lệnh | Tổng PnL nếu vào USD |
|---|---|---|---:|---:|
| tuning | VETO | paired_rule_only | 0 | 0.0000 |
| tuning | VETO | isolated_flat_start | 0 | 0.0000 |
| tuning | REDUCE | paired_rule_only | 213 | 213.0424 |
| tuning | REDUCE | isolated_flat_start | 0 | 0.0000 |
| test | VETO | paired_rule_only | 0 | 0.0000 |
| test | VETO | isolated_flat_start | 0 | 0.0000 |
| test | REDUCE | paired_rule_only | 94 | 130.1310 |
| test | REDUCE | isolated_flat_start | 0 | 0.0000 |

## Calibration so với lệnh lỗ

Mỗi mẫu là một lệnh rule-only thực sự; loss khi net PnL < 0. Cùng một state có thể lặp trên nhiều lệnh. Dự báo tỷ lệ nền của test được khóa bằng tỷ lệ lỗ tuning; không ước lượng lại từ test. Brier thấp hơn tốt hơn. Bin trống giữ rõ, không nội suy.

| Kỳ | n | Tỷ lệ lỗ | Dự báo nền | Brier nền | Brier P(skip) | Brier P(exhaustion) |
|---|---:|---:|---:|---:|---:|---:|
| tuning | 237 | 63.29% | 63.29% | 0.2323 | 0.4799 | 0.3259 |
| test | 103 | 64.08% | 63.29% | 0.2302 | 0.4640 | 0.3188 |

| Kỳ | Xác suất | Bin | n | P trung bình | Tần suất lỗ |
|---|---|---|---:|---:|---:|
| tuning | exhaustion | [0.0, 0.2) | 0 | — | — |
| tuning | exhaustion | [0.2, 0.4) | 194 | 0.3197 | 65.98% |
| tuning | exhaustion | [0.4, 0.6) | 43 | 0.4379 | 51.16% |
| tuning | exhaustion | [0.6, 0.8) | 0 | — | — |
| tuning | exhaustion | [0.8, 1.0] | 0 | — | — |
| tuning | skip | [0.0, 0.2) | 175 | 0.1140 | 64.57% |
| tuning | skip | [0.2, 0.4) | 62 | 0.2310 | 59.68% |
| tuning | skip | [0.4, 0.6) | 0 | — | — |
| tuning | skip | [0.6, 0.8) | 0 | — | — |
| tuning | skip | [0.8, 1.0] | 0 | — | — |
| test | exhaustion | [0.0, 0.2) | 0 | — | — |
| test | exhaustion | [0.2, 0.4) | 77 | 0.3147 | 64.94% |
| test | exhaustion | [0.4, 0.6) | 26 | 0.4438 | 61.54% |
| test | exhaustion | [0.6, 0.8) | 0 | — | — |
| test | exhaustion | [0.8, 1.0] | 0 | — | — |
| test | skip | [0.0, 0.2) | 71 | 0.1246 | 66.20% |
| test | skip | [0.2, 0.4) | 32 | 0.2353 | 59.38% |
| test | skip | [0.4, 0.6) | 0 | — | — |
| test | skip | [0.6, 0.8) | 0 | — | — |
| test | skip | [0.8, 1.0] | 0 | — | — |

## Result ID của 40 replay theo cấu hình khóa

Bundle summary/trades/events/equity có manifest riêng trong `~/.local/state/aigorithmic-trading/jev-review/evaluation-v2-20261016/{tuning,test}-reports/`; receipt `{tuning,test}.json` ghi đường dẫn từng bundle và toàn bộ phản thực/calibration. Hash các artifact được ghép trong manifest kiểm chứng cuối.

| Kỳ | Route | Rule-only result_id | A result_id |
|---|---|---|---|
| tuning | ETHUSDT spot | `e6873a3681c948f244aa9bdc977558a1ccc649e63f588a008ff987d6e59782af` | `afcfad3f839b4937600d75c5894482697adea82676415d08cbccc55710c4d1c1` |
| tuning | ETHUSDT perp | `45c958a28e173501ebb2b80ec56e774bfc94a4f322d62d6a6beb7b14d9854ba7` | `dfb04abd83f75736c60da2ff4f5c84d5bcd14451a2456231e128af53b06430dc` |
| tuning | HYPEUSDT spot | `541f2e583f11670490402ede324f2c0adecafcf94b7e76a8013d6c80e8e2c42c` | `f9e1467cfe322bb6efb976840b26d4a4b1df851c9ab8bc3cbaa4b8742bce12e7` |
| tuning | HYPEUSDT perp | `37e2fe4e6a8a78301fb0f841d34093cc9aef7de588fbe7379790b4ab796ad700` | `e255007cd4b59b7c3cd672eb8da0908281abc719ff3376458b85bf76a758319b` |
| tuning | NEARUSDT spot | `d84733265deb8e60f7f9876649f234c599038455099366943d435595488eadda` | `e9376ea8918155873be17420915b319c7bfc3381456b2e0bc370a7a8d7d25c95` |
| tuning | NEARUSDT perp | `a41cc89cb89461f5b75ae8a02d85552de3be973d42247846c54661bd4e373039` | `1a117a0d5546166764b34ff1c1ca5c4761d0230571cb832f2102c703056e99e6` |
| tuning | SOLUSDT spot | `db45528ea025cf0e15f162248c5caef2c8d8d40f09c73058d8ebf8f33d5e9c81` | `0ca4b0fad40160b3c6112de4440a3fde51434e210229ed556b1464ecdeaac0e0` |
| tuning | SOLUSDT perp | `2e13dde2d989bf9610ad7bfc8bfddc073cf72e3cc892fde6e759e5be3d8b7d23` | `fe9a4985018a1293bc67ae0306f56707cfa923301623995d333f1a91b048300c` |
| tuning | ZECUSDT spot | `c4467b7879d81cc71866d6ec890b77df9204e02644ee88838f2c6f351d33713b` | `479cad59b2a0fed49cda07e93c4400a5972074c67f57890ccbd46de79dfbc331` |
| tuning | ZECUSDT perp | `fd3d3dda5280447c8cc9f4c7e82ce20892406e576492233ba54990b79ee9a53a` | `3f1860c0822f7216830884d5896d1eec106ee053eed1a99464970d37af26bf7b` |
| test | ETHUSDT spot | `461ff279006cd28ed7493ebfab67728066f75b04e447bc4c8c79cdd80ea26214` | `024474abff9250d1aed9819ef924a40bd8387bf29890d85e75765d18e06f2acf` |
| test | ETHUSDT perp | `773c08acd0989c1fbf88b24c5edad17c692bd867b15b90bd653379f3c8e40830` | `7d0e17dcaa62e38267052bb2bc7b233af695e07d64f57df1fe36f24821a23399` |
| test | HYPEUSDT spot | `b5e1863aca7dd30a7fccd3f71db8ca734e5f6faab8a48fddc0a5600dd929324d` | `5486fb376ebf15af1ad6440b814e83de703fc147d473e777f52b6cfb94616260` |
| test | HYPEUSDT perp | `43a32da62c6b121fdadc11c7466a7e00a095a39a0aea78705d22d4191595a5eb` | `839648d3c7eab3d238bc26bd2b89596d1c1daa7218dc4212a1153bf5ce02e561` |
| test | NEARUSDT spot | `191bc81746571e1365fedd37d51c787ce6de12926a9d6e03738f9017ecae718a` | `2db14685330d936192b9fb190c43d03276dd6a09423535510932044fd1b8d912` |
| test | NEARUSDT perp | `aa654a3c7cf5c4efc02cb4466a58ef46b906e0584351e33b779dd72640162a27` | `c530d4de694c435888b34f0f2a4cd0ef2db94b0ca06247203b804c1c272a89de` |
| test | SOLUSDT spot | `68665a865b0cd67def9ce0dc0990de5f2f4c04844d7747fa921bfc571deb15bd` | `32f6875bdee4405e24ad1a903b59013dfbbb2255f68e94788a9f6075f8430a53` |
| test | SOLUSDT perp | `943ad83f618d9b322f57f69af82e23b98578babf75dea3521fcfdd6554d89a53` | `8dc183e6a39690ce7f48e26193cf3ba61a96b4ee6dba11093479d8d00bcf48e7` |
| test | ZECUSDT spot | `bed1a10fc0c4909bc999c2e6e57bb8ee2a7797a0e50bae6958bdee7e45e0bdca` | `b88dcd528503f98387d4e4586d680b8d67515e41cedc7910364528667ca51fb3` |
| test | ZECUSDT perp | `d0d454d0b449213a423b9c106d1ac16f9b9f5aa655b52ea1ba89bfa5f02ab67a` | `5654fb778573ae363e06e7f1ff8375359509c822ebd50b95122119a92b18c53d` |

## Phạm vi bảo vệ và tham chiếu

Manifest gốc đã chuyển ra khỏi worktree advisory được gỡ:
`/home/julius/.local/state/aigorithmic-trading/verification/jev-advisory-20261015T114503Z/protected-before.json`.
Đã đọc lại và xác minh SHA256
`95a91cf9f9a8afdaa6566ac5bc5b2b994dfea0508bfea4a6bf5701487f071efa`.

Đợt 2 không sửa thêm source bảo vệ. **96 file còn nguyên hash**, đúng ba ngoại lệ
của đợt 1 giữ nguyên hash sau sửa. Chi tiết tại
[receipt đối chiếu gốc](../state/verification/jev-review-v2-20261016/protected-v2-original-manifest.json):

| File trong `intraday/replay_v2/` | Trước ngoại lệ | Sau ngoại lệ, giữ nguyên trong đợt 2 |
|---|---|---|
| `contracts.py` | `b04e2de48ba300ad95e0691efd4c3504286309bd1cf2bc7ead0b66cc998ff546` | `4cb314d9c8873e25bf40a06e4d690770504e5412cd7f23cb0bf6e32e1fc617ab` |
| `engine.py` | `7494b3fecc183cee3983930003cfd358009d2ed3fabd16edfed89e56b4467640` | `f6f4166cc766ca2910aa5b28dfcde617e4d094483af4831340662573c2e8a17e` |
| `metrics.py` | `810d3f88d5bb92fa678473605993b744304e5ad54b8e3df9f2a0de9db00f6dd6` | `b3a7009e10309ffa3cc5b200f7fbd0514054d91e331bd77be9195cb377eb1037` |

- Chạy lại 10 ca H4/rotation: **10/10 result_id và summary khớp**, kể cả cost ×2;
  [receipt](../state/verification/jev-review-v2-20261016/reference-replay-v2.json).
- Chạy lại 10 route G4 mặc định tắt lớp phủ: **toàn bộ output canonical giữ nguyên
  từng byte** so với checkout gốc `9eeb207`, bao gồm events/trades/equity và input ID;
  [đối chiếu](../state/verification/jev-review-v2-20261016/disabled-comparison-v2.json).

## Giới hạn và phần chưa làm

Không dùng kết quả này để kích hoạt giao dịch. Jev có thể đã học lịch sử dù state
không có tên coin/ngày; luật và universe được chọn hậu nghiệm; các kỳ vẫn cùng
thị trường, ít lệnh và nhiều state lặp/tương quan. Bootstrap lệnh độc lập có thể
đánh giá thấp bất định. Xác suất exhaustion được kiểm với proxy lỗ lệnh, không
phải nhãn sự kiện reversal-before-continuation được đo độc lập.

DD tổng là của 10 tài khoản route độc lập, mỗi tài khoản 1.000 USD; không áp
competition/rotation ba slot. Phản thực isolated có thể chồng lấn và không được
cộng vào PnL danh mục. Các tín hiệu entry=True khi cả hai nhánh đang có vị thế
chỉ có nhãn, không được đếm giả thành một lệnh bị veto/reduce.

Chưa làm replay lớp phủ danh mục slot, shadow live, nhánh B/hội đồng LLM hay vòng
học hằng tuần. Không đổi rule/content_hash, active set, soak, scoped_gate,
rule_allows_answers hay runtime Demo. Không VPS, Binance order, push, merge hoặc
deploy; giữ nguyên ranh giới đánh giá soak 28/10.

## Kiểm thử và file thay đổi đợt 2

Manifest kiểm chứng cuối:
[manifest.json](../state/verification/jev-review-v2-20261016/manifest.json), SHA256
`3c723dff31faa2317de1966e8fe632baf109a269e5e687198a81ae35d28280de`.
Finalizer đã kiểm lại budget/cache, lock/receipt tuning, hash database/source,
40 bundle report và tổng hợp lại metrics từ journal gốc.

Chạy toàn bộ `uv run --frozen --no-sync pytest -q -p no:cacheprovider` với
`PYTHONPATH` trỏ worktree này và virtualenv dùng chung: **1.868 passed, 1 failed,
23 warnings**, 426,21 giây. Lỗi duy nhất là baseline đã được cho phép:
`tests/test_perp_collection_migration.py::test_cli_inheritance_and_readiness_show_collection_without_changing_champion`
(`gate_version='v1'` thay vì `'gate-v2.1'`). Không sửa lỗi ngoài phạm vi.
[Log đầy đủ](../state/verification/jev-review-v2-20261016/pytest-full.log).

Code mới/đổi so với `03242cd`: `intraday/setup2_review.py`,
`intraday/jev_review.py`, `intraday/jev_review_provider.py`,
`intraday/jev_review_cli.py`, `intraday/jev_review_budget.py`,
`intraday/jev_review_evaluation.py`, `scripts/evaluate_jev_review.py`.
Test: `tests/test_setup2_review.py`, `tests/test_jev_review_cli.py`,
`tests/test_jev_review_budget.py`, `tests/test_jev_review_evaluation.py`.
Tài liệu: CHANGELOG, báo cáo/giao thức đợt 2, link từ báo cáo đợt 1 và đường dẫn
manifest trong `docs/jev-review-protected-scope-2026-10-16.md`.
