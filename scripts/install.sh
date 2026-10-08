#!/usr/bin/env bash
# SO-101 환경 설치기.
#   scripts/install.sh            고정 버전 설치. 다시 실행하면 정상 환경을 재사용하고 누락·부분 설치를 복구한다.
#   scripts/install.sh --repair   lock에 있는 모든 패키지를 캐시 없이 다시 받아 설치한다(파일 손상 의심 시).
#   scripts/install.sh --reset-venv
#                                 Python이 3.12가 아닌 등 재사용할 수 없는 .venv를 .venv.broken-<시각>으로 옮기고 새로 만든다(삭제하지 않음).
#   scripts/install.sh --relock   [유지보수용] lock/pyproject.toml로 lock/uv.lock을 다시 해결하고 점검한 뒤 설치한다.
#
# 설치 기준: lock/lerobot.env(LeRobot 태그·SHA, Python minor, uv 버전·체크섬) + lock/pyproject.toml + lock/uv.lock.
# 일반 설치는 `uv sync --locked`만 실행하며 lock을 재해결하지 않는다.
# 모든 다운로드·캐시·임시 파일은 작업공간 안(.cache/, .tmp/)에 둔다.
set -euo pipefail

WS="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd -P)"
cd "$WS"

# ---- 실행 환경 격리(첫 다운로드 전에 적용) ----
unset PYTHONPATH PYTHONHOME LD_LIBRARY_PATH VIRTUAL_ENV CONDA_PREFIX || true
export PYTHONNOUSERSITE=1
export TMPDIR="$WS/.tmp"
export UV_CACHE_DIR="${SO101_UV_CACHE_DIR:-$WS/.cache/uv}"
export UV_PYTHON_DOWNLOADS=never
export UV_PYTHON_PREFERENCE=only-system
export UV_PROJECT_ENVIRONMENT="$WS/.venv"
# venv 파일을 캐시와 하드링크로 공유하지 않는다(설치본 손상이 캐시로 번지지 않게, --repair가 깨끗한 파일을 받게).
export UV_LINK_MODE=copy
export GIT_LFS_SKIP_SMUDGE=1
mkdir -p "$TMPDIR" "$UV_CACHE_DIR" "$WS/logs"

MODE=install
for a in "$@"; do
  case "$a" in
    --repair) MODE=repair ;;
    --reset-venv) RESET_VENV=1 ;;
    --relock) MODE=relock ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "알 수 없는 옵션: $a" >&2; exit 2 ;;
  esac
done
RESET_VENV="${RESET_VENV:-0}"

log() { printf '[install] %s\n' "$*"; }
die() { printf '[install][FAIL] %s\n' "$*" >&2; exit 1; }

# lock/lerobot.env를 source하지 않고 KEY=VALUE만 읽는다.
envget() {
  local v
  v="$(grep -E "^$1=" "$WS/lock/lerobot.env" | tail -n1 | cut -d= -f2-)"
  [[ -n "$v" ]] || die "lock/lerobot.env에 $1 값이 없습니다."
  printf '%s' "$v"
}
LEROBOT_REPO="$(envget LEROBOT_REPO)"
LEROBOT_SHA="$(envget LEROBOT_SHA)"
LEROBOT_TAG="$(envget LEROBOT_TAG)"
PYTHON_MINOR="$(envget PYTHON_MINOR)"
UV_VERSION="$(envget UV_VERSION)"
UV_ASSET="$(envget UV_ASSET)"
UV_SHA256="$(envget UV_SHA256)"
UV="$WS/.tools/uv/uv"

# 설치 중 필요한 최소 여유 공간(MB). docs/CURRENT_STATE.md의 실측값(venv+캐시+checkout)에 여유를 더한 값.
REQUIRED_FREE_MB="${SO101_REQUIRED_FREE_MB:-3000}"

# ---- 0. 사전 점검 ----
log "작업공간: $WS"
[[ "$(uname -s)" == Linux ]] || die "Linux만 지원합니다."
[[ "$(uname -m)" == x86_64 ]] || die "lock/uv.lock은 x86_64 전용입니다(현재 $(uname -m))."
if [[ -r /etc/os-release ]]; then
  . /etc/os-release
  [[ "${ID:-}" == ubuntu && "${VERSION_ID:-}" == 24.04 ]] || log "경고: Ubuntu 24.04 외 환경입니다(${PRETTY_NAME:-unknown}). 계속 진행합니다."
fi
for f in "${XDG_CONFIG_HOME:-$HOME/.config}/uv/uv.toml" /etc/uv/uv.toml; do
  [[ -e "$f" ]] && log "경고: uv 사용자/시스템 설정 $f 이 있습니다. 인덱스 등 설정이 lock 설치에 섞이지 않는지 확인하세요."
done
for c in git curl tar sha256sum df; do command -v "$c" >/dev/null || die "필수 명령 '$c'이(가) 없습니다."; done
PYSYS="$(command -v "python$PYTHON_MINOR" || true)"
[[ -n "$PYSYS" ]] || die "python$PYTHON_MINOR이 없습니다. Ubuntu 24.04 기본 패키지입니다: sudo apt install python$PYTHON_MINOR"
avail_mb="$(df -Pm "$WS" | awk 'NR==2{print $4}')"
log "여유 공간: ${avail_mb} MB ($(df -P "$WS" | awk 'NR==2{print $6}'))"
if [[ ! -x "$WS/.venv/bin/python" && "$avail_mb" -lt "$REQUIRED_FREE_MB" ]]; then
  die "여유 공간이 ${REQUIRED_FREE_MB} MB보다 적습니다. 공간을 확보하거나 여유 있는 파일시스템에 작업공간을 두세요."
fi

# ---- 1. uv 고정 바이너리 ----
uv_ok() { [[ -x "$UV" ]] && [[ "$("$UV" --version 2>/dev/null | awk '{print $2}')" == "$UV_VERSION" ]]; }
if uv_ok; then
  log "uv $UV_VERSION 재사용"
else
  log "uv $UV_VERSION 내려받기(체크섬 검증)"
  dl="$TMPDIR/dl"; mkdir -p "$dl"
  curl -fsSL --retry 3 -o "$dl/$UV_ASSET" "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/$UV_ASSET" \
    || die "uv 다운로드 실패. 네트워크 확인 후 다시 실행하세요."
  echo "$UV_SHA256  $dl/$UV_ASSET" | sha256sum -c --quiet - || die "uv 체크섬 불일치. 파일을 사용하지 않습니다: $dl/$UV_ASSET"
  rm -rf "$WS/.tools/uv.new"; mkdir -p "$WS/.tools/uv.new"
  tar -xzf "$dl/$UV_ASSET" -C "$WS/.tools/uv.new" --strip-components=1
  if [[ -e "$WS/.tools/uv" ]]; then mv "$WS/.tools/uv" "$WS/.tools/uv.old-$(date +%Y%m%d_%H%M%S)"; fi
  mv "$WS/.tools/uv.new" "$WS/.tools/uv"
  uv_ok || die "uv 설치 확인 실패"
fi

# ---- 2. 공식 LeRobot 고정 SHA checkout(원본 참조·설치본 동일성 검증용) ----
EXT="$WS/external/lerobot"
if [[ -d "$EXT/.git" ]]; then
  head_sha="$(git -C "$EXT" rev-parse HEAD 2>/dev/null || echo none)"
  dirty="$(git -C "$EXT" status --porcelain 2>/dev/null | head -n1)"
  if [[ "$head_sha" == "$LEROBOT_SHA" && -z "$dirty" ]]; then
    log "external/lerobot @ $LEROBOT_SHA 재사용"
  elif [[ -n "$dirty" ]]; then
    die "external/lerobot에 수정된 파일이 있습니다. 덮어쓰지 않습니다. 'git -C external/lerobot status'로 확인하세요."
  else
    log "external/lerobot이 $head_sha에 있습니다. 수정 사항이 없으므로 $LEROBOT_SHA로 전환합니다."
    git -C "$EXT" fetch -q origin "$LEROBOT_SHA" || die "fetch 실패. 네트워크 확인 후 다시 실행하세요."
    git -C "$EXT" -c advice.detachedHead=false checkout -q --detach "$LEROBOT_SHA"
  fi
elif [[ -e "$EXT" ]]; then
  die "$EXT가 git checkout이 아닙니다. 내용을 확인하고 직접 옮긴 뒤 다시 실행하세요(자동 삭제하지 않음)."
else
  log "LeRobot $LEROBOT_TAG($LEROBOT_SHA) 받기"
  mkdir -p "$WS/external"
  rm -rf "$EXT.partial"   # 이전 실행이 남긴 이 설치기 전용 임시 폴더
  git clone -q --filter=blob:none --no-checkout "$LEROBOT_REPO" "$EXT.partial" || die "clone 실패. 다시 실행하세요."
  git -C "$EXT.partial" -c advice.detachedHead=false checkout -q --detach "$LEROBOT_SHA" || die "checkout 실패"
  mv "$EXT.partial" "$EXT"
fi
[[ "$(git -C "$EXT" rev-parse HEAD)" == "$LEROBOT_SHA" ]] || die "external/lerobot SHA 불일치"
tag_sha="$(git -C "$EXT" rev-parse "refs/tags/$LEROBOT_TAG^{commit}" 2>/dev/null || true)"
[[ -z "$tag_sha" || "$tag_sha" == "$LEROBOT_SHA" ]] || die "태그 $LEROBOT_TAG($tag_sha)가 고정 SHA와 다릅니다."

# ---- 3. (유지보수) lock 재해결 ----
if [[ "$MODE" == relock ]]; then
  log "[relock] lock/uv.lock 재해결"
  "$UV" lock --project lock --python "$PYTHON_MINOR"
  log "[relock] 공식 uv.lock과 비교"
  "$PYSYS" "$WS/tools/lock_tools.py" diff "$EXT/uv.lock" lock/uv.lock
  if grep -nE 'path = |directory = |editable = |file://|/home/|/mnt/' lock/uv.lock; then
    die "lock/uv.lock에 로컬 경로가 들어 있습니다."
  fi
  "$PYSYS" - "$WS/lock/uv.lock" <<'EOF'
import sys, tomllib
d = tomllib.load(open(sys.argv[1], "rb"))
bad, sdist_only = [], []
for p in d["package"]:
    if p["name"] == "so101-env":
        continue
    files = p.get("wheels", []) + ([p["sdist"]] if "sdist" in p else [])
    if any("hash" not in f for f in files):
        bad.append(p["name"])
    if not p.get("wheels"):
        sdist_only.append(p["name"])
print("[relock] 휠 없는 패키지(소스 빌드):", sdist_only)
if bad:
    sys.exit(f"[relock] 해시 없는 항목: {bad}")
if set(sdist_only) - {"feetech-servo-sdk"}:
    sys.exit("[relock] feetech-servo-sdk 외에 소스 빌드가 필요한 패키지가 생겼습니다. build-constraint를 검토하세요.")
EOF
fi

# ---- 4. venv ----
if [[ -e "$WS/.venv" ]]; then
  vpy="$WS/.venv/bin/python"
  vver="$("$vpy" -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null || echo none)"
  if [[ "$vver" != "$PYTHON_MINOR" ]]; then
    if [[ "$RESET_VENV" == 1 ]]; then
      bk="$WS/.venv.broken-$(date +%Y%m%d_%H%M%S)"
      log ".venv(Python $vver)을 $bk로 옮깁니다."
      mv "$WS/.venv" "$bk"
    else
      die ".venv의 Python이 $vver입니다($PYTHON_MINOR 필요). --reset-venv로 옮기고 새로 만들 수 있습니다."
    fi
  fi
fi
if [[ ! -e "$WS/.venv" ]]; then
  log ".venv 생성(시스템 python$PYTHON_MINOR)"
  "$UV" venv --quiet --python "$PYSYS" "$WS/.venv"
fi

# ---- 5. lock 그대로 동기화(재해결 없음, lock에 없는 패키지 제거, 누락·버전 차이 복구) ----
sync_args=(sync --project lock --locked --python "$WS/.venv/bin/python")
[[ "$MODE" == repair ]] && sync_args+=(--reinstall --no-cache)
if "$UV" sync --project lock --locked --python "$WS/.venv/bin/python" --check >/dev/null 2>&1 && [[ "$MODE" != repair ]]; then
  log "패키지 상태가 lock과 일치 → 재사용"
else
  log "uv sync --locked 실행"
  "$UV" "${sync_args[@]}" || die "의존성 설치 실패. 네트워크 확인 후 다시 실행하세요(이미 받은 파일은 .cache/uv에서 재사용)."
fi
"$UV" sync --project lock --locked --python "$WS/.venv/bin/python" --check >/dev/null || die "설치 후에도 환경이 lock과 다릅니다."
"$UV" pip check --python "$WS/.venv/bin/python" || die "의존성 불일치(uv pip check)"

# ---- 6. 실제 상태 검증(소프트웨어 점검) 후 stamp 기록 ----
log "소프트웨어 점검(scripts/so101 doctor --scope software)"
set +e
"$WS/scripts/so101" doctor --scope software
rc=$?
set -e
if [[ $rc -ne 0 && $rc -ne 2 ]]; then
  die "소프트웨어 점검 실패. 위 FAIL 항목을 확인하세요. 파일 손상이 의심되면 --repair로 다시 실행하세요."
fi
"$WS/.venv/bin/python" - "$WS" "$LEROBOT_SHA" "$UV_VERSION" <<'EOF'
import hashlib, json, sys, time, pathlib, platform
ws, sha, uvv = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3]
h = lambda p: hashlib.sha256((ws / p).read_bytes()).hexdigest()
stamp = {
    "lerobot_sha": sha,
    "uv_version": uvv,
    "python": platform.python_version(),
    "uv_lock_sha256": h("lock/uv.lock"),
    "pyproject_sha256": h("lock/pyproject.toml"),
    "lerobot_env_sha256": h("lock/lerobot.env"),
    "installed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
}
(ws / ".venv/so101_install_stamp.json").write_text(json.dumps(stamp, indent=2) + "\n")
EOF
log "완료. 다음: scripts/so101 doctor --scope software (상세), 실기는 README의 순서를 따르세요."
