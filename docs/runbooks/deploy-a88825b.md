# Deploy `a88825b` lên VPS (Jev tư vấn + heartbeat soak)

Trạng thái: **đã chạy 2026-10-17 08:12–08:16Z** (Julius duyệt). Kết quả ghi ở CHANGELOG. Bản dưới giữ làm mẫu cho lần deploy sau.
Cần ở cùng mạng LAN với VPS (`192.168.21.57`).

## Thay đổi so với bản đang chạy (`236e767`)

| Phần | Thay đổi | Ảnh hưởng lúc soak |
|---|---|---|
| `intraday/setup2_store.py` | Heartbeat đếm mọi nến luật đã đánh giá, bắt mọi lỗi khi gọi Jev | **Có.** Các nến lỗi Jev từ 14/10 được tính lại |
| `intraday/trade_alerts.py`, `scoped_gate.py` | Chữ Telegram: Jev chỉ là ý kiến; vẫn báo tín hiệu khi Jev lỗi | Có, chỉ ở nội dung tin `[SOAK]` |
| `intraday/execution/*` | Chế độ `--jev-mode advisory` cho Demo | Không, `demo-execution` chưa bật (G5/G6 riêng) |
| `active_set_cli.py` | Sửa chuỗi help | Không |
| `scheduler.py`, `store.py`, `__main__.py` (thêm 17/10) | Job Setup-2 lỗi `OperationalError` (database khoá) được chạy lại ở vòng kế tiếp, trong 30 phút | **Có.** Không còn mất nến như 16/10 và 17/10 lúc 00:00Z |

Commit đích: `ffd67c7` (`a88825b` cộng bản sửa thử lại ngày 17/10).

Không đổi: `deploy/` (compose, Dockerfile), dependency (`pyproject.toml`, `uv.lock`), schema database (v23), active set, rule và
candidate, campaign soak, `.env.intraday`, key. Vì không đổi schema, lùi về bản cũ không cần khôi phục database.

## Giờ làm

Nến H4 đóng lúc 00, 04, 08, 12, 16, 20Z (07, 11, 15, 19, 23, 03 giờ Việt Nam). Worker ghi observation ngay sau giờ đóng nến.
Bắt đầu **sau HH:15** của một giờ đóng nến và restart worker **trước HH+3:30**. Ví dụ: bắt đầu 11:15 giờ VN, xong trước 14:30.
Build image không ảnh hưởng worker; chỉ bước restart cần nằm trong cửa sổ này.

## Chuẩn bị (trên máy local)

```bash
cd ~/julius-workspace/ai-labs/projects/system-trading
git fetch origin && git merge-base --is-ancestor ffd67c7 origin/feature/slot-runtime && echo "đã có ffd67c7 trên remote"
```

Trên VPS, các lệnh dưới dùng hàm `C`:

```bash
ssh julius@192.168.21.57
cd ~/AIgorithmic-trading && export PATH=$HOME/.local/bin:$PATH
C() { docker compose --env-file /home/julius/AIgorithmic-trading/.env.intraday \
      -f /home/julius/AIgorithmic-trading/deploy/intraday/compose.yaml "$@"; }
```

## 1. Ghi trạng thái trước (chỉ đọc)

```bash
git status --short && git log -1 --oneline        # mong đợi: sạch, 236e767
C ps                                               # worker, web chạy; demo-execution không chạy
C images | grep -E 'worker|web|admin'              # ghi image ID để lùi lại
C --profile admin run --rm --no-deps admin doctor
C --profile admin run --rm --no-deps admin status  # paper, execution_enabled=false, schema 23
C --profile admin run --rm --no-deps admin active-set show
```

Heartbeat từng route, chạy từ máy local (script chỉ đọc, chạy được với cả code cũ lẫn mới):

```bash
ssh julius@192.168.21.57 'docker compose --env-file /home/julius/AIgorithmic-trading/.env.intraday \
  -f /home/julius/AIgorithmic-trading/deploy/intraday/compose.yaml exec -T worker python -' < scripts/soak_heartbeat.py
```

Cột `old` là cách tính đang chạy, `new` là cách tính sau deploy, `jev_err` là số nến Jev lỗi. Lưu output này vào CHANGELOG.
Nếu `new` của route nào dưới 95%, dừng lại báo Julius: route đó thiếu nến vì lý do khác Jev, deploy không cứu được.

## 2. Sao lưu

```bash
aigt backup create                                 # in đường dẫn file .sqlite3 và .manifest.json
aigt backup verify ~/.local/state/aigorithmic-trading/backups/intraday-<TIMESTAMP>.sqlite3   # status=ok
```

Không bắt buộc chép ra máy local, vì deploy không đổi schema hay dữ liệu.

## 3. Lấy code và build (worker vẫn chạy bản cũ)

```bash
git fetch origin feature/slot-runtime
git checkout --detach ffd67c7 && git log -1 --oneline
C build worker web admin
```

## 4. Restart: web trước, worker sau

Bài học ngày 13/10: khởi động cùng lúc làm luồng nền của worker chết vì tranh khoá database.

```bash
C up -d --no-deps --wait web
curl -fsS -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8081/healthz   # mong đợi 200
C up -d --no-deps --wait worker
C ps
```

## 5. Kiểm tra sau deploy

```bash
C --profile admin run --rm --no-deps admin doctor
C --profile admin run --rm --no-deps admin status       # vẫn paper, execution_enabled=false, schema 23
C --profile admin run --rm --no-deps admin active-set show   # giống bước 1
C logs --since 10m worker | grep -E 'Traceback|database is locked|setup2_jev' || echo 'không có lỗi'
```

- Chạy lại `scripts/soak_heartbeat.py`: cột `new` phải bằng hoặc cao hơn `old` ở bước 1, campaign và `started` không đổi.
- Sau giờ đóng nến H4 kế tiếp (+10 phút), chạy lại script: `last bar` của cả 9 route phải là nến vừa đóng.
- Nếu nến đó có tín hiệu: tin `[SOAK]` trên Telegram có dòng Jev theo chữ mới.

## Lùi lại

Chỉ cần nếu worker lỗi hoặc thiếu nến sau deploy. Database không đổi nên không khôi phục backup.

```bash
git checkout --detach 236e767
C build worker web admin
C up -d --no-deps --wait web && C up -d --no-deps --wait worker
```

Lùi lại thì cách tính heartbeat trở về như cũ (nến lỗi Jev không được tính).

## Ghi lại

Thêm mục CHANGELOG: giờ deploy, commit, image ID cũ/mới, output heartbeat trước và sau, kết quả `doctor`/`status`, file backup.
