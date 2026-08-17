#!/bin/sh
# 备份循环（容器 backup 服务专用）：SQLite 在线热备 + 保留最近 N 份。
#
# 复用 scripts/backup_db.py（sqlite backup API，WAL 下不阻塞在线读写，见 G10）。
# 选 while+sleep 而非 cron：python:3.11-slim 无 cron，且该语义可原样平移到
# k8s CronJob / 裸机 systemd timer——部署形态后定时只需换宿主。
#
# 环境变量（见 .env.example「备份」段）：
#   SC_BACKUP_INTERVAL_HOURS  备份间隔小时（默认 24）
#   SC_BACKUP_KEEP            保留备份份数（默认 30，按 mtime 取新弃旧）
#   SC_BACKUP_DIR             备份落盘目录（默认 /backups，compose 内为命名卷）
#
# 要点：备份写独立 .bak 文件、只读源库——不构成对 sc.db 的第二个写者，
#       守住「单写进程」不变量；单次失败不退出循环，下一轮自愈。

set -eu

INTERVAL_HOURS="${SC_BACKUP_INTERVAL_HOURS:-24}"
KEEP="${SC_BACKUP_KEEP:-30}"
DEST_DIR="${SC_BACKUP_DIR:-/backups}"

mkdir -p "$DEST_DIR"

while true; do
  ts=$(date +%Y%m%d-%H%M%S)
  echo "[backup-loop] start $ts"
  if python -m scripts.backup_db "$DEST_DIR/sc.db.$ts.bak"; then
    echo "[backup-loop] backed up to $DEST_DIR/sc.db.$ts.bak"
  else
    echo "[backup-loop] backup failed, will retry next round" >&2
  fi

  # 保留策略：按 mtime 最新 KEEP 份，其余删除
  ls -1t "$DEST_DIR"/sc.db.*.bak 2>/dev/null \
    | tail -n +"$((KEEP + 1))" \
    | xargs -r rm -f

  echo "[backup-loop] done, sleeping ${INTERVAL_HOURS}h"
  sleep "$((INTERVAL_HOURS * 3600))"
done
