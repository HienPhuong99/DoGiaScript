@echo off
rem Task Scheduler gọi file này mỗi sáng thứ Hai. Kết quả từng lần chạy nối vào dogia.log.
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
.venv\Scripts\python.exe dogia.py >> dogia.log 2>&1
