#!/usr/bin/env bash
# USB 시리얼(SO-101 모터 보드)·카메라 장치 목록과 접근 권한만 확인한다. 포트를 열지 않는다.
# 로봇 connect·보정·토크·동작 명령을 실행하지 않는다.
#
#   bash scripts/check_devices.sh
#
# 종료 코드: 0 = 문제 없음, 2 = 주의(장치 미연결·권한·간섭 서비스), 1 = 대상 OS 아님
# 장치가 하나도 없으면 "미연결"로 표시한다(소프트웨어 설치 실패가 아님).
set -uo pipefail
warn=0
say() { printf '%s\n' "$*"; }
w() { printf '[WARN] %s\n' "$*"; warn=1; }
[ "$(uname -s)" = Linux ] || { echo "Linux 전용입니다(현재 $(uname -s))."; exit 1; }

user="$(id -un)"
groups=" $(id -nG) "
say "== 사용자: $user  그룹:$groups"

say "== USB 시리얼(모터 보드)"
shopt -s nullglob
serial=(/dev/ttyACM* /dev/ttyUSB*)
if [ ${#serial[@]} -eq 0 ]; then
  w "시리얼 장치 없음(/dev/ttyACM*, /dev/ttyUSB*) — 보드 미연결 또는 USB 케이블(데이터 지원)·허브 확인. 소프트웨어 문제 아님"
else
  for d in "${serial[@]}"; do
    perm="$(stat -c '%A %U:%G' "$d")"
    acc="읽기·쓰기 가능"
    [ -r "$d" ] && [ -w "$d" ] || { acc="권한 없음"; }
    say "  $d  $perm  → $acc"
    [ "$acc" = "읽기·쓰기 가능" ] || w "$d 접근 불가: 그룹 $(stat -c %G "$d")에 속해야 함 → scripts/setup_system.sh --add-dialout(관리자 승인 후) 실행, 그다음 로그아웃·재로그인"
  done
  if [ -d /dev/serial/by-id ]; then
    say "  by-id(보드 시리얼 번호 기준, 재연결·다른 USB 포트에도 그대로. 설정에는 이 경로 사용):"
    for l in /dev/serial/by-id/*; do say "    $l -> $(readlink -f "$l")"; done
  fi
fi
case "$groups" in *" dialout "*) say "  dialout 그룹: 속함" ;; *) say "  dialout 그룹: 속하지 않음(시리얼 권한이 dialout이면 필요)" ;; esac
if dpkg -s brltty >/dev/null 2>&1; then
  w "brltty 설치됨: CH340/CH341 계열 USB 시리얼을 점유할 수 있음. 보드가 사라지면 관리자에게 brltty 비활성화 검토 요청(자동 제거하지 않음)"
fi
if systemctl is-active --quiet ModemManager 2>/dev/null; then
  w "ModemManager 실행 중: 새 시리얼 장치를 잠시 점유할 수 있음. 포트 점유 문제가 생기면 관리자에게 문의"
fi
if command -v lsof >/dev/null 2>&1 && [ ${#serial[@]} -gt 0 ]; then
  for d in "${serial[@]}"; do
    pids="$(lsof -t "$d" 2>/dev/null | tr '\n' ' ')"
    [ -z "$pids" ] || w "$d 를 다른 프로세스가 사용 중(PID $pids, 같은 사용자 기준)"
  done
fi

say "== 카메라(V4L2)"
video=(/dev/video*)
if [ ${#video[@]} -eq 0 ]; then
  w "카메라 장치 없음(/dev/video*) — 미연결. 소프트웨어 문제 아님"
else
  for d in "${video[@]}"; do
    perm="$(stat -c '%A %U:%G' "$d")"
    acc="읽기·쓰기 가능"; [ -r "$d" ] && [ -w "$d" ] || acc="권한 없음"
    name=""; [ -r "/sys/class/video4linux/$(basename "$d")/name" ] && name="$(cat "/sys/class/video4linux/$(basename "$d")/name")"
    say "  $d  $perm  $name  → $acc"
    [ "$acc" = "읽기·쓰기 가능" ] || w "$d 접근 불가: 그룹 $(stat -c %G "$d") 필요(보통 로그인 사용자는 자동 허용). 관리자에게 문의"
  done
  if command -v v4l2-ctl >/dev/null 2>&1; then
    say "  v4l2-ctl --list-devices:"; v4l2-ctl --list-devices 2>/dev/null | sed 's/^/    /'
  else
    say "  (v4l2-ctl 없음: 장치·형식 확인용 v4l-utils는 scripts/setup_system.sh --apt로 설치 가능)"
  fi
  if [ -d /dev/v4l/by-id ]; then
    say "  by-id(같은 모델 카메라 2대는 시리얼이 같아 하나만 보이고, 재부팅마다 가리키는 장치가 바뀔 수 있음):"
    for l in /dev/v4l/by-id/*; do say "    $l -> $(readlink -f "$l")"; done
  fi
  if [ -d /dev/v4l/by-path ]; then
    say "  by-path(USB 포트 위치 기준, 설정에는 이 경로 사용):"
    for l in /dev/v4l/by-path/*; do case "$l" in *usbv2*) ;; *) say "    $l -> $(readlink -f "$l")" ;; esac; done
  fi
fi
say "  참고: 카메라 번호·회전·front/wrist 대응은 실제 영상으로 확인(python scripts/cam_diag.py). 이 스크립트는 카메라를 열지 않음"

# config/so101.env에 적힌 경로가 지금 있는지(장치를 열지는 않음)
CONF="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/config/so101.env"
if [ -r "$CONF" ]; then
  say "== 설정값($CONF)과 비교"
  for key in SO101_FOLLOWER_PORT SO101_LEADER_PORT SO101_CAM_FRONT SO101_CAM_WRIST; do
    val="$(sed -n "s/^$key=//p" "$CONF" | tail -1)"
    if [ -e "$val" ]; then
      say "  $key = $val -> $(readlink -f "$val")"
    else
      w "$key = $val 없음(미연결이거나 다른 USB 포트/다른 PC. 장치를 꽂은 뒤 scripts/find_arms.py·cam_diag.py로 다시 확인)"
    fi
  done
fi

say "== NVIDIA"
if command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader 2>/dev/null | sed 's/^/  /' || w "nvidia-smi 실행 실패"
else
  w "nvidia-smi 없음(드라이버 미설치·미로드). 관리자에게 확인 요청"
fi
[ "$warn" = 0 ] && { say "결과: 문제 없음"; exit 0; }
say "결과: 주의 항목 있음(위 [WARN])"
exit 2
