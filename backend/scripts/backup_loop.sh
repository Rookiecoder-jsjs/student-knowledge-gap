#!/bin/sh
# 备份循环（容器 backup 服务专用）：SQLite 在线热备 + 保留最近 N 份 + 压缩归档。
#
# 复用 scripts/backup_db.py（sqlite backup API，WAL 下不阻塞在线读写，见 G10）。
# 选 while+sleep 而非 cron：python:3.11-slim 无 cron，且该语义可原样平移到
# k8s CronJob / 裸机 systemd timer——部署形态后定时只需换宿主。
#
# 环境变量（见 .env.example「备份」段）：
#   SC_BACKUP_INTERVAL_HOURS    备份间隔小时（默认 24）
#   SC_BACKUP_KEEP              保留备份份数（默认 30，按 mtime 取新弃旧）
#   SC_BACKUP_DIR               备份落盘目录（默认 /backups，compose 内为命名卷）
#   SC_BACKUP_COMPRESS_DAYS     归档阈值天龄（默认 0=关）：超过此天龄的旧份
#                               gzip 压缩（§9「历史学期归档压缩」）——压缩后
#                               仍参与 KEEP 计数，恢复时 gunzip 即用
#
# 要点：备份写独立 .bak 文件、只读源库——不构成对 sc.db 的第二个写者，
#       守住「单写进程」不变量；单次失败不退出循环，下一轮自愈。

set -eu

INTERVAL_HOURS="${SC_BACKUP_INTERVAL_HOURS:-24}"
KEEP="${SC_BACKUP_KEEP:-30}"
DEST_DIR="${SC_BACKUP_DIR:-/backups}"
COMPRESS_DAYS="${SC_BACKUP_COMPRESS_DAYS:-0}"

mkdir -p "$DEST_DIR"

compress_old() {
  # §9 保留期限的归档半边：mtime 早于阈值的 .bak → .bak.gz（幂等：已压缩跳过）
  [ "$COMPRESS_DAYS" -gt 0 ] || return 0
  find "$DEST_DIR" -name "sc.db.*.bak" -type f -mtime +"$COMPRESS_DAYS" \
    | while IFS= read -r f; do
        [ -f "$f.gz" ] && continue
        if gzip -9 "$f"; then
          echo "[backup-loop] archived $f -> $f.gz"
        else
          echo "[backup-loop] compress failed: $f (will retry next round)" >&2
        fi
      done
}

while true; do
  ts=$(date +%Y%m%d-%H%M%S)
  echo "[backup-loop] start $ts"
  if python -m scripts.backup_db "$DEST_DIR/sc.db.$ts.bak"; then
    echo "[backup-loop] backed up to $DEST_DIR/sc.db.$ts.bak"
  else
    echo "[backup-loop] backup failed, will retry next round" >&2
  fi

  compress_old || true

  # 保留策略：按 mtime 最新 KEEP 份，其余删除（.gz 归档同池计数——
  # ls 取 mtime 对两种后缀一视同仁）
  ls -1t "$DEST_DIR"/sc.db.*.bak "$DEST_DIR"/sc.db.*.bak.gz 2>/dev/null \
    | tail -n +"$((KEEP + 1))" \
    | xargs -r rm -f

  echo "[backup-loop] done, sleeping ${INTERVAL_HOURS}h"
  sleep "$((INTERVAL_HOURS * 3600))"
done
