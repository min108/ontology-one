#!/usr/bin/env bash
# 在仓库根目录运行 0.7.0 基线的全部既有检查，并输出逐条用例结果，供与 baseline-tests.txt 比对。
# 前提：已设置 MYSQL_HOST/MYSQL_PORT/MYSQL_USER/MYSQL_PASSWORD 指向一次性测试库（需建库建用户权限）；
#       已构建技能测试镜像：docker build -f ops/skill-runner.Dockerfile -t ontology-skill-test:0.5.0 .
# 用法：PY=.venv/bin/python OUT=/某个仓库外的目录 bash docs/blueprint/rounds/r1-baseline/run_baseline.sh
set -u
PY=${PY:-.venv/bin/python}
OUT=${OUT:-/tmp/ontology-baseline}
mkdir -p "$OUT"
: > "$OUT/summary.tsv"
run() { local name=$1; shift; local t0=$(date +%s); "$@" > "$OUT/$name.log" 2>&1; local rc=$?; printf '%s\texit=%s\t%ss\n' "$name" "$rc" "$(( $(date +%s) - t0 ))" >> "$OUT/summary.tsv"; }
run unittest_all env RUN_HARNESS_SDK_TESTS=1 RUN_FEDERATION_MYSQL_TESTS=1 RUN_JOBS_MYSQL_TESTS=1 "$PY" -m unittest discover -s tests -v
run github_scripts "$PY" -m unittest discover -s .github/scripts -p 'test_*.py'
for s in restore_v020 security workflows sources mcp skills entity_storage agent_runtime; do run "check_$s" "$PY" "scripts/check_$s.py"; done
run frontend sh -c 'npm --prefix frontend ci && npm --prefix frontend run build'
# 逐条结果：模块.类.方法::方法名 状态（ok / skipped / FAIL / ERROR），排序后可直接 diff baseline-tests.txt
"$PY" - "$OUT/unittest_all.log" > "$OUT/tests.txt" <<'PY'
import re, sys
res = {}
for line in open(sys.argv[1], encoding='utf-8', errors='replace'):
    m = re.match(r'^(\S+) \((\S+)\)(?: .*)? \.\.\. (ok|skipped|FAIL|ERROR)', line)
    if m:
        res[m.group(2) + '::' + m.group(1)] = m.group(3)
for k in sorted(res):
    print(k, res[k])
PY
cat "$OUT/summary.tsv"; echo "用例结果：$OUT/tests.txt（$(wc -l < "$OUT/tests.txt") 条）"
