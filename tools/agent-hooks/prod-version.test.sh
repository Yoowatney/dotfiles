#!/usr/bin/env bash
# prod-version: one line from deploy run history (api#1659). Run: bash tools/agent-hooks/prod-version.test.sh
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
tmp=$(mktemp); trap 'rm -f "$tmp"' EXIT
run() { PROD_VERSION_RUNS="$tmp" bash "$here/prod-version"; }
# the 11/27 case: v1.28.0 is live, v1.29.0 started later and still runs, rc runs are not production
cat >"$tmp" <<'JSON'
[{"headBranch":"v1.29.0","status":"in_progress","conclusion":"","createdAt":"2026-11-27T07:30:09Z"},
 {"headBranch":"v1.29.0-rc.1","status":"completed","conclusion":"success","createdAt":"2026-11-27T04:43:14Z"},
 {"headBranch":"v1.28.0","status":"completed","conclusion":"success","createdAt":"2026-11-27T04:31:02Z"},
 {"headBranch":"v1.27.0","status":"completed","conclusion":"success","createdAt":"2026-11-26T16:08:44Z"}]
JSON
[ "$(run)" = "운영 v1.28.0 (시작 11-27 13:31 KST, 성공) · 진행 중인 운영 배포: v1.29.0 (시작 11-27 16:30 KST, in_progress)" ] || { echo "FAIL running: $(run)"; exit 1; }
# nothing running; a rc-only history has no production version; a failed newest run is shown
echo '[{"headBranch":"v1.28.0","status":"completed","conclusion":"success","createdAt":"2026-11-27T04:31:02Z"}]' >"$tmp"
[ "$(run)" = "운영 v1.28.0 (시작 11-27 13:31 KST, 성공) · 진행 중인 운영 배포: 없음" ] || { echo "FAIL idle: $(run)"; exit 1; }
echo '[{"headBranch":"v1.30.0-rc.1","status":"completed","conclusion":"success","createdAt":"2026-11-27T08:45:52Z"}]' >"$tmp"
[ "$(run)" = "운영 알 수 없음 · 진행 중인 운영 배포: 없음" ] || { echo "FAIL rc-only: $(run)"; exit 1; }
echo '[{"headBranch":"v1.29.0","status":"completed","conclusion":"failure","createdAt":"2026-11-27T07:30:09Z"},{"headBranch":"v1.28.0","status":"completed","conclusion":"success","createdAt":"2026-11-27T04:31:02Z"}]' >"$tmp"
run | grep -q "가장 최근 운영 배포 v1.29.0 실패(failure)" || { echo "FAIL failed: $(run)"; exit 1; }
echo "prod-version test ok"
