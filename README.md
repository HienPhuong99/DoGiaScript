# Dò giá MacBook

Script dò giá MacBook trên 18 web bán lẻ Việt Nam, lọc theo cấu hình mục tiêu và ghi vào Google Sheet.
Toàn bộ cấu hình (web nào bật, cấu hình nào theo dõi, từ khóa loại trừ, ngưỡng deal) đọc từ chính sheet đó.

## Cài đặt

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy config.example.json config.json
copy webhook.example.gs webhook.gs
```

1. Tự đặt một chuỗi bí mật ngẫu nhiên, điền vào `token` trong `config.json` và `TOKEN` trong `webhook.gs` (hai nơi phải giống nhau).
2. Mở Google Sheet → Tiện ích mở rộng → Apps Script, dán `webhook.gs`, triển khai dạng "Ứng dụng web" (Thực thi bằng: Tôi; Quyền truy cập: Bất kỳ ai).
3. Dán URL ứng dụng web vào `webhook_url` trong `config.json`.

`config.json` và `webhook.gs` chứa bí mật nên đã nằm trong `.gitignore`.

## Chạy

```bash
.venv\Scripts\python dogia.py            # dò và ghi sheet
.venv\Scripts\python dogia.py --dry      # dò, không ghi sheet, chỉ lưu CSV vào ket-qua/
.venv\Scripts\python dogia.py --dry --near   # in thêm các tin gần khớp (lệch tình trạng, bị từ khóa loại)
.venv\Scripts\python dogia.py --only qmacstore.com
.venv\Scripts\python dogia.py --test     # tự kiểm tra
```

Chạy định kỳ: trỏ Task Scheduler vào `run.cmd`; nhật ký từng lần chạy nằm ở `dogia.log`.

## Sheet cần có các tab

`Web dò giá`, `Cấu hình mục tiêu`, `Từ khóa loại trừ`, `Cài đặt`, `Nhật ký`, `Deal tốt`, `Nhật ký lỗi`.

## Giới hạn

- Facebook không được dò tự động.
- Chợ Tốt chỉ lấy TP.HCM; giá do người bán tự ghi.
- Tin ở shop máy cũ không ghi "used / like new / 9x% / open box" được coi là máy mới.
- Thẻ gộp nhiều biến thể (Topzone) chỉ đọc được biến thể mặc định.
- Web nào bắt đầu chặn thì lỗi hiện ở tab `Nhật ký lỗi`; khi đó mới cần thêm trình duyệt stealth (`scrapling install`).
