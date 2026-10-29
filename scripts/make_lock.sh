#!/usr/bin/env bash
# [유지보수자 전용] Linux x86_64 + CUDA 12.8 의존성 lock을 다시 만든다. 일반 설치에는 필요 없다.
#
#   UV=/path/to/uv UV_PYTHON=/path/to/python3.12 scripts/make_lock.sh          # 다시 해결(uv.lock 갱신) + 내보내기
#   UV=... scripts/make_lock.sh --export-only                                  # uv.lock 그대로, lock.txt만 다시 만들기
#   UV=... scripts/make_lock.sh --check                                        # uv.lock·lock.txt가 pyproject와 일치하는지만 확인
#
# - uv 0.12.23(연구 저장소와 같은 버전)을 요구한다. 어느 OS에서 실행해도 결과는 Linux x86_64 전용이다
#   (requirements/pyproject.toml의 environments). 해결 기준 시각(exclude-newer)이 고정돼 있어 같은 입력이면 같은 결과다.
# - 결과물:
#   requirements/uv.lock                  uv 해결 결과(모든 파일 sha256)
#   requirements/linux-cu128.lock.txt     pip install --require-hashes --no-deps 용(인덱스 지정 포함)
#   requirements/build.lock.txt           feetech-servo-sdk(sdist) 빌드용 setuptools(해시 고정)
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REQ="$REPO/requirements"
UV_REQUIRED="0.12.23"
MODE="lock"
case "${1:-}" in
  -h|--help) awk 'NR>1 && /^#/ {sub(/^# ?/, ""); print; next} NR>1 {exit}' "$0"; exit 0 ;;
  --export-only) MODE="export" ;;
  --check) MODE="check" ;;
  "") ;;
  *) echo "알 수 없는 옵션: $1 (--help)"; exit 2 ;;
esac
UV="${UV:-$(command -v uv || true)}"
[ -n "$UV" ] && [ -x "$UV" ] || { echo "uv가 필요합니다(UV=/경로/uv). 버전 $UV_REQUIRED"; exit 2; }
uv_ver="$("$UV" --version | awk '{print $2}')"
[ "$uv_ver" = "$UV_REQUIRED" ] || { echo "uv $UV_REQUIRED 필요(현재 $uv_ver)"; exit 2; }
export UV_PYTHON_DOWNLOADS=never
export UV_CACHE_DIR="${UV_CACHE_DIR:-$REPO/.cache/uv}"

if [ "$MODE" = lock ]; then
  "$UV" lock --project "$REQ"
elif [ "$MODE" = check ]; then
  "$UV" lock --project "$REQ" --check
fi

tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT
"$UV" export --project "$REQ" --frozen --format requirements-txt --no-emit-project --no-header --output-file "$tmp" >/dev/null
lock_sha="$(shasum -a 256 "$REQ/uv.lock" 2>/dev/null | awk '{print $1}' || sha256sum "$REQ/uv.lock" | awk '{print $1}')"
render_main() {
  cat <<EOF
# 자동 생성 파일 — 직접 고치지 말 것(scripts/make_lock.sh). 대상: Linux x86_64, CPython 3.12, CUDA 12.8(cu128)
# 원본: requirements/pyproject.toml → requirements/uv.lock (sha256 ${lock_sha}), uv ${uv_ver}
# 설치: python -m pip install --isolated --no-deps --require-hashes --no-build-isolation -r requirements/linux-cu128.lock.txt
#   (scripts/setup_ubuntu.sh가 실행. 모든 패키지·전이 의존성의 버전과 파일 sha256이 고정돼 있어 pip가 다시 해결하지 않는다)
# 인덱스: PyPI + PyTorch cu128(torch·torchvision의 +cu128 빌드). 해시가 고정돼 있어 다른 파일은 설치되지 않는다.
--index-url https://pypi.org/simple
--extra-index-url https://download.pytorch.org/whl/cu128

EOF
  cat "$tmp"
}
render_build() {
  echo "# 자동 생성 파일(scripts/make_lock.sh). feetech-servo-sdk(sdist) 빌드용 — linux-cu128.lock.txt의 setuptools와 같은 버전·해시."
  echo "--index-url https://pypi.org/simple"
  echo
  awk '/^setuptools==/{p=1} p{print} p && !/\\$/{exit}' "$tmp"
}
if [ "$MODE" = check ]; then
  diff -q <(render_main) "$REQ/linux-cu128.lock.txt" >/dev/null || { echo "[FAIL] linux-cu128.lock.txt가 uv.lock과 다름(make_lock.sh --export-only)"; exit 1; }
  diff -q <(render_build) "$REQ/build.lock.txt" >/dev/null || { echo "[FAIL] build.lock.txt가 uv.lock과 다름"; exit 1; }
  echo "[OK] uv.lock·linux-cu128.lock.txt·build.lock.txt가 pyproject와 일치"
  exit 0
fi
render_main > "$REQ/linux-cu128.lock.txt"
render_build > "$REQ/build.lock.txt"
grep -q '^setuptools==81.0.0 ' "$REQ/build.lock.txt" || { echo "[FAIL] build.lock.txt에 setuptools==81.0.0이 없음"; exit 1; }
n="$(grep -c '^[A-Za-z0-9_.-]*==' "$REQ/linux-cu128.lock.txt")"
echo "[OK] lock 생성: 패키지 ${n}개 → requirements/linux-cu128.lock.txt, build.lock.txt (uv.lock sha256 ${lock_sha:0:12}…)"
