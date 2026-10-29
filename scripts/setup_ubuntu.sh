#!/usr/bin/env bash
# SO-101 연구 환경 설치(Ubuntu 24.04 x86_64 + NVIDIA GPU, 개인 conda 환경).
#
#   bash scripts/setup_ubuntu.sh                       # 기본: conda 환경 jammin_so-arm 생성(있으면 갱신) → lock 설치 → 점검(GPU 필수)
#   bash scripts/setup_ubuntu.sh --env-name NAME       # 다른 환경 이름
#   bash scripts/setup_ubuntu.sh --conda /경로/conda   # 사용할 conda 지정(기본: PATH·$CONDA_EXE·~/miniforge3 등에서 찾음)
#   bash scripts/setup_ubuntu.sh --gpu skip            # GPU 점검 생략(GPU 없는 PC·CI). 기본 required
#   bash scripts/setup_ubuntu.sh --install-miniforge   # conda가 없을 때만: 고정 버전 Miniforge를 ~/miniforge3에 설치(셸 설정은 바꾸지 않음)
#   bash scripts/setup_ubuntu.sh --dry-run             # 실행할 명령만 표시
#   bash scripts/setup_ubuntu.sh --help
#
# 하는 일: ① OS(Ubuntu 24.04 x86_64) 확인 ② conda 찾기 ③ NVIDIA 드라이버 상태 표시(바꾸지 않음)
#   ④ environment.yml로 conda 환경 생성, 이미 있으면 같은 스펙으로 갱신(삭제·재생성 안 함)
#   ⑤ 그 환경의 python -m pip로 requirements/linux-cu128.lock.txt 설치(--require-hashes --no-deps: 다시 해결하지 않음)
#   ⑥ pip check ⑦ scripts/check_env.py(소프트웨어·CUDA 점검, 로봇·카메라·모델·데이터 불필요)
# 하지 않는 일: apt·sudo·사용자 그룹 변경(→ scripts/setup_system.sh, 명시적으로만), NVIDIA 드라이버·커널 변경, 재부팅,
#   base 환경·다른 사람 환경 설치, ~/.bashrc 등 셸 설정 변경(conda init 안 함), Hugging Face 모델·데이터 다운로드.
# 다시 실행해도 안전하다(같은 lock이면 아무것도 바뀌지 않음).
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_NAME="jammin_so-arm"
CONDA_BIN=""
GPU_MODE="required"
INSTALL_MINIFORGE=0
MINIFORGE_DIR="$HOME/miniforge3"
DRY_RUN=0
SKIP_CHECK=0
PY_VERSION="3.12.15"
LOCK="$REPO/requirements/linux-cu128.lock.txt"
BUILD_LOCK="$REPO/requirements/build.lock.txt"
DRIVER_MIN="570.86"   # PyTorch cu128 휠의 드라이버 하한(공식 LeRobot v0.6.1 pyproject 주석)
# Miniforge(conda-forge 공식 배포) 고정값: https://github.com/conda-forge/miniforge/releases/tag/26.7.2-1
MINIFORGE_VERSION="26.7.2-1"
MINIFORGE_URL="https://github.com/conda-forge/miniforge/releases/download/${MINIFORGE_VERSION}/Miniforge3-${MINIFORGE_VERSION}-Linux-x86_64.sh"
MINIFORGE_SHA256="cf0df84220e1c83a1cb2f447056594ca458fa4c6a353430c498f281ec21bd187"

log() { printf '[setup] %s\n' "$*"; }
die() { printf '[setup][FAIL] %s\n' "$1" >&2; exit "${2:-1}"; }
run() {
  printf '[setup] $ %s\n' "$*"
  [ "$DRY_RUN" = 1 ] || "$@"
}
usage() { awk 'NR>1 && /^#/ {sub(/^# ?/, ""); print; next} NR>1 {exit}' "$0"; }

while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --env-name) ENV_NAME="${2:?--env-name 값 필요}"; shift 2 ;;
    --env-name=*) ENV_NAME="${1#*=}"; shift ;;
    --conda) CONDA_BIN="${2:?--conda 값 필요}"; shift 2 ;;
    --conda=*) CONDA_BIN="${1#*=}"; shift ;;
    --gpu) GPU_MODE="${2:?--gpu 값 필요}"; shift 2 ;;
    --gpu=*) GPU_MODE="${1#*=}"; shift ;;
    --install-miniforge) INSTALL_MINIFORGE=1; shift ;;
    --miniforge-dir) MINIFORGE_DIR="${2:?}"; shift 2 ;;
    --miniforge-dir=*) MINIFORGE_DIR="${1#*=}"; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --skip-check) SKIP_CHECK=1; shift ;;
    *) die "알 수 없는 옵션: $1 (--help 참고)" 2 ;;
  esac
done

case "$GPU_MODE" in required|skip|auto) ;; *) die "--gpu는 required|skip|auto" 2 ;; esac
[[ "$ENV_NAME" =~ ^[A-Za-z0-9_.-]+$ ]] || die "환경 이름은 영문·숫자·_.- 만: $ENV_NAME" 2
case "$ENV_NAME" in base|root) die "base/root 환경에는 설치하지 않습니다(개인 환경 이름을 쓰세요)." 2 ;; esac

# ---- ① 대상 OS 확인(다른 OS면 설치를 시작하지 않음) ----
OS_RELEASE="${SO101_OS_RELEASE:-/etc/os-release}"   # SO101_OS_RELEASE는 차단 동작 시험용
os_id=""; os_ver=""
if [ -r "$OS_RELEASE" ]; then
  # shellcheck source=/dev/null
  os_id="$(. "$OS_RELEASE" && echo "${ID:-}")"
  # shellcheck source=/dev/null
  os_ver="$(. "$OS_RELEASE" && echo "${VERSION_ID:-}")"
fi
arch="$(uname -m)"
if [ "$os_id" != "ubuntu" ] || [ "$os_ver" != "24.04" ] || [ "$arch" != "x86_64" ]; then
  die "대상은 Ubuntu 24.04 x86_64입니다(현재: ${os_id:-unknown} ${os_ver:-?} $(uname -s) $arch). 설치를 시작하지 않습니다." 2
fi
log "OS: Ubuntu $os_ver $arch"
[ -f "$LOCK" ] && [ -f "$BUILD_LOCK" ] && [ -f "$REPO/environment.yml" ] || die "저장소 파일 누락(environment.yml, requirements/*.lock.txt)"

# ---- ② conda 찾기(없으면 안내, --install-miniforge일 때만 고정 버전 설치) ----
find_conda() {
  local c
  for c in "$CONDA_BIN" "${CONDA_EXE:-}" "$(command -v conda 2>/dev/null || true)" "$MINIFORGE_DIR/bin/conda" \
           "$HOME/miniforge3/bin/conda" "$HOME/miniconda3/bin/conda" "$HOME/anaconda3/bin/conda" "/opt/conda/bin/conda"; do
    [ -n "$c" ] && [ -x "$c" ] && { echo "$c"; return 0; }
  done
  return 1
}
if [ -n "$CONDA_BIN" ] && [ ! -x "$CONDA_BIN" ]; then die "--conda 경로가 실행 파일이 아님: $CONDA_BIN" 2; fi
if ! CONDA="$(find_conda)"; then
  if [ "$INSTALL_MINIFORGE" != 1 ]; then
    cat >&2 <<EOF
[setup][FAIL] conda를 찾지 못했습니다. 둘 중 하나를 하세요.
  1) 이미 설치된 conda가 있으면 경로 지정:  bash scripts/setup_ubuntu.sh --conda /경로/bin/conda
  2) 개인 홈에 Miniforge ${MINIFORGE_VERSION} 설치(고정 버전·sha256 검증, 셸 설정 변경 없음):
       bash scripts/setup_ubuntu.sh --install-miniforge
     (설치 위치: ${MINIFORGE_DIR}. 이미 있으면 덮어쓰지 않습니다)
EOF
    exit 3
  fi
  [ -e "$MINIFORGE_DIR" ] && die "$MINIFORGE_DIR 이(가) 이미 있습니다. 덮어쓰지 않습니다(--conda로 그 안의 conda를 지정)." 3
  dl="$REPO/.cache/downloads"; inst="$dl/Miniforge3-${MINIFORGE_VERSION}-Linux-x86_64.sh"
  run mkdir -p "$dl"
  if [ ! -f "$inst" ]; then run curl -fL --retry 3 -o "$inst.partial" "$MINIFORGE_URL"; run mv "$inst.partial" "$inst"; fi
  if [ "$DRY_RUN" != 1 ]; then
    got="$(sha256sum "$inst" | awk '{print $1}')"
    [ "$got" = "$MINIFORGE_SHA256" ] || { rm -f "$inst"; die "Miniforge sha256 불일치($got). 파일을 지웠습니다." 3; }
    log "Miniforge sha256 확인: $MINIFORGE_SHA256"
  fi
  run bash "$inst" -b -p "$MINIFORGE_DIR"   # -b: 묻지 않음, conda init 하지 않음(셸 설정 불변)
  CONDA="$MINIFORGE_DIR/bin/conda"
fi
log "conda: $CONDA ($("$CONDA" --version 2>/dev/null || echo '?'))"

# ---- ③ NVIDIA 드라이버 상태(표시만. 드라이버·커널은 바꾸지 않음) ----
ver_ge() { [ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -1)" = "$2" ]; }
if command -v nvidia-smi >/dev/null 2>&1 && smi="$(nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader 2>/dev/null)"; then
  log "GPU: $smi"
  drv="$(printf '%s\n' "$smi" | head -1 | awk -F', ' '{print $2}')"
  if ! ver_ge "$drv" "$DRIVER_MIN"; then
    log "[주의] NVIDIA 드라이버 $drv < $DRIVER_MIN: PyTorch cu128(CUDA 12.8)을 쓰려면 관리자에게 드라이버 ${DRIVER_MIN} 이상(RTX 50 계열은 570 이상 지원 드라이버)을 요청하세요. 이 스크립트는 드라이버를 바꾸지 않습니다."
  fi
else
  log "[주의] nvidia-smi 없음 또는 실패: NVIDIA 드라이버 미설치·미로드일 수 있습니다. Python 환경 설치는 계속하지만 GPU 점검은 실패합니다(관리자에게 드라이버 상태 확인 요청)."
fi

# ---- ④ conda 환경: 없으면 생성, 있으면 같은 스펙으로 갱신(삭제·재생성 안 함) ----
env_prefix() {
  "$CONDA" env list --json 2>/dev/null | python3 -c '
import json, os, sys
name = sys.argv[1]
for p in json.load(sys.stdin).get("envs", []):
    if os.path.basename(p.rstrip("/")) == name and os.path.basename(os.path.dirname(p.rstrip("/"))) == "envs":
        print(p); break' "$ENV_NAME"
}
command -v python3 >/dev/null || die "python3(시스템)가 필요합니다(Ubuntu 기본 포함). 설치 여부를 관리자에게 확인하세요."
PREFIX="$(env_prefix || true)"
if [ -n "$PREFIX" ]; then
  log "기존 환경 발견: $ENV_NAME ($PREFIX) → 삭제하지 않고 environment.yml 스펙으로 갱신"
  cur_py="$("$PREFIX/bin/python" -c 'import platform;print(platform.python_version())' 2>/dev/null || echo none)"
  if [ "$cur_py" != "$PY_VERSION" ] && [ "$cur_py" != none ]; then
    die "기존 환경 $ENV_NAME의 Python이 $cur_py입니다($PY_VERSION 필요). 자동으로 지우지 않습니다. 다른 이름(--env-name)을 쓰거나, 직접 확인 후 정리하세요: $CONDA env remove -n $ENV_NAME"
  fi
  run "$CONDA" env update -n "$ENV_NAME" -f "$REPO/environment.yml"
else
  log "새 환경 생성: $ENV_NAME"
  run "$CONDA" env create -n "$ENV_NAME" -f "$REPO/environment.yml"
  PREFIX="$(env_prefix || true)"
  [ -n "$PREFIX" ] || [ "$DRY_RUN" = 1 ] || die "환경 생성 후 경로를 찾지 못함: $ENV_NAME"
fi
PY="${PREFIX:-<env>}/bin/python"
if [ "$DRY_RUN" != 1 ]; then
  [ -x "$PY" ] || die "환경 Python 없음: $PY"
  got_py="$("$PY" -c 'import platform;print(platform.python_version())')"
  [ "$got_py" = "$PY_VERSION" ] || die "환경 Python $got_py ≠ $PY_VERSION"
  log "환경 Python: $PY ($got_py)"
fi

# ---- ⑤ pip lock 설치(그 환경의 python -m pip만 사용, 사용자 pip 설정·환경변수 무시) ----
pip_env=(env -u PYTHONPATH -u PYTHONHOME -u PIP_INDEX_URL -u PIP_EXTRA_INDEX_URL -u PIP_REQUIRE_VIRTUALENV
         PYTHONNOUSERSITE=1 PIP_DISABLE_PIP_VERSION_CHECK=1)
# feetech-servo-sdk(휠 없음)를 빌드할 setuptools를 먼저(lock과 같은 버전·해시), 그다음 전체를 다시 해결 없이 설치
run "${pip_env[@]}" "$PY" -m pip install --isolated --no-deps --require-hashes -r "$BUILD_LOCK"
run "${pip_env[@]}" "$PY" -m pip install --isolated --no-deps --require-hashes --no-build-isolation -r "$LOCK"
run "${pip_env[@]}" "$PY" -m pip check

# ---- ⑥ 점검 ----
if [ "$SKIP_CHECK" = 1 ]; then
  log "점검 생략(--skip-check). 나중에: $PY $REPO/scripts/check_env.py --gpu $GPU_MODE"
  rc=0
else
  set +e
  run "${pip_env[@]}" "$PY" "$REPO/scripts/check_env.py" --gpu "$GPU_MODE" --lock "$LOCK"
  rc=$?
  set -e
fi

cat <<EOF

[setup] 완료(점검 종료 코드 $rc: 0=PASS, 1=FAIL). 다음부터:
  conda activate $ENV_NAME
    (conda가 셸에 초기화돼 있지 않으면 먼저: eval "\$($CONDA shell.bash hook)")
  VS Code: 명령 팔레트 → "Python: Select Interpreter" → ${PREFIX:-<env 경로>}/bin/python
  다시 점검: python scripts/check_env.py      장치 권한 점검: bash scripts/check_devices.sh
EOF
exit "$rc"
