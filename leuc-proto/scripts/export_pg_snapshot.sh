#!/usr/bin/env sh
# AI-GEN-BEGIN
# 从运行中的 leuc-pgsql 导出全量快照到 docker/init/01-leuc-data.sql.gz
set -e
ROOT="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/docker/init/01-leuc-data.sql.gz"
TMP="/tmp/leuc-dump-$$.sql"

echo "[export] dumping from leuc-pgsql ..."
docker exec leuc-pgsql pg_dump -U leuc -d leuc --no-owner --no-acl -f "$TMP"
docker cp "leuc-pgsql:$TMP" "$ROOT/docker/init/_dump.sql"
docker exec leuc-pgsql rm -f "$TMP"

python3 - <<PY
import gzip
from pathlib import Path
root = Path(r"""$ROOT""") / "docker" / "init"
raw = (root / "_dump.sql").read_text(encoding="utf-8", errors="replace")
lines = [
    ln for ln in raw.splitlines(True)
    if not ln.startswith("\\restrict") and not ln.startswith("\\unrestrict")
]
out = root / "01-leuc-data.sql.gz"
with gzip.open(out, "wt", encoding="utf-8", compresslevel=9) as f:
    f.writelines(lines)
(root / "_dump.sql").unlink(missing_ok=True)
print(f"[export] wrote {out} ({out.stat().st_size} bytes)")
PY
# AI-GEN-END
