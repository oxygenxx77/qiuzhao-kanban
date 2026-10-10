#!/bin/bash
cd "$(dirname "$0")" || exit 1
source venv/bin/activate

# 先杀死可能残留的旧服务，防止端口冲突（只匹配看板自己的进程，不影响求职助手）
pkill -f "qiuzhao-kanban/venv/bin/uvicorn" >/dev/null 2>&1

# 让 uvicorn 在后台静默运行，日志写入 uvicorn.log
nohup uvicorn main:app --reload > uvicorn.log 2>&1 &

# 等2秒确保服务启动，然后自动打开浏览器
sleep 2
open http://127.0.0.1:8000/
