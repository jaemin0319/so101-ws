#!/usr/bin/env bash
# [선택, sudo 필요] 시스템 설정 — Python 환경 설치(setup_ubuntu.sh)와 분리돼 있다. 명시한 옵션만 실행한다.
# 시스템을 바꾸는 작업이므로 어떤 명령이 실행되는지 --dry-run으로 먼저 확인할 수 있다. 드라이버·커널·apt upgrade·서비스 제거는 하지 않는다.
#
#   bash scripts/setup_system.sh --apt           # v4l-utils(카메라 목록·형식 확인용 v4l2-ctl)만 apt로 설치
#   bash scripts/setup_system.sh --add-dialout   # 현재 사용자를 dialout 그룹에 추가(USB 시리얼 권한). 로그아웃·재로그인 필요
#   bash scripts/setup_system.sh --dry-run --apt --add-dialout   # 실행할 명령만 표시
#
# 필요 없는 것: CUDA Toolkit 전체(PyTorch 휠이 CUDA 런타임을 포함), 시스템 FFmpeg(conda 환경의 ffmpeg 사용),
# 시스템 python 패키지. NVIDIA 드라이버가 없거나 570.86 미만이면 관리자에게 요청한다(이 스크립트는 바꾸지 않음).
set -euo pipefail
APT=0; DIALOUT=0; DRY=0
usage() { awk 'NR>1 && /^#/ {sub(/^# ?/, ""); print; next} NR>1 {exit}' "$0"; }
for a in "$@"; do
  case "$a" in
    --apt) APT=1 ;;
    --add-dialout) DIALOUT=1 ;;
    --dry-run) DRY=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "알 수 없는 옵션: $a (--help)"; exit 2 ;;
  esac
done
[ "$APT" = 1 ] || [ "$DIALOUT" = 1 ] || { usage; echo; echo "옵션을 하나 이상 지정하세요(아무것도 하지 않았습니다)."; exit 2; }
ID=""; VERSION_ID=""
if [ -r /etc/os-release ]; then . /etc/os-release; fi
[ "${ID:-}" = ubuntu ] && [ "${VERSION_ID:-}" = 24.04 ] || { echo "Ubuntu 24.04 전용입니다(현재 ${ID:-?} ${VERSION_ID:-?})."; exit 2; }
run() { printf '$ %s\n' "$*"; [ "$DRY" = 1 ] || "$@"; }
if [ "$APT" = 1 ]; then
  run sudo apt-get update
  run sudo apt-get install -y --no-install-recommends v4l-utils
fi
if [ "$DIALOUT" = 1 ]; then
  u="$(id -un)"
  if id -nG "$u" | tr ' ' '\n' | grep -qx dialout; then
    echo "이미 dialout 그룹입니다: $u"
  else
    run sudo usermod -aG dialout "$u"
    echo "적용하려면 로그아웃 후 다시 로그인하세요(재부팅 불필요). 확인: id -nG"
  fi
fi
