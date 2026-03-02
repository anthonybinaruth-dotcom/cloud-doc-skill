#!/bin/bash
# 云文档监控 - 定时执行脚本

cd "$(dirname "$0")/.."

# 加载环境变量
if [ -f .env ]; then
    export $(grep -v '^#' .env | xargs)
fi

# 激活虚拟环境
source venv/bin/activate

# 执行检查
python -m src.main --check-now

# 记录执行时间
echo "[$(date '+%Y-%m-%d %H:%M:%S')] 定时检查完成" >> logs/cron.log