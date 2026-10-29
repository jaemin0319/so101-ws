#!/usr/bin/env bash
# 리더 팔 → 팔로워 팔 추종(lerobot-teleoperate). 실행하면 팔로워 팔이 실제로 움직인다.
#
#   bash scripts/teleop.sh              # config/so101.env의 포트·ID·보정 파일로 실행. Ctrl+C로 종료
#   bash scripts/teleop.sh --dry-run    # 실행할 명령만 표시
#   bash scripts/teleop.sh --fps=30     # 그 밖의 인자는 lerobot-teleoperate에 그대로 넘긴다
#
# 보정 파일(calibration/)이 없거나 모터 값과 다르면 lerobot이 연결할 때 보정을 묻는다.
# Ctrl+C로 멈추면 팔로워 토크가 꺼져 팔이 힘없이 내려온다. 멈추기 전에 팔로워를 받치거나 리더를 편한 자세로 둔다.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DRY=0
args=()
for a in "$@"; do
  case "$a" in
    -h|--help) awk 'NR>1 && /^#/ {sub(/^# ?/, ""); print; next} NR>1 {exit}' "$0"; exit 0 ;;
    --dry-run) DRY=1 ;;
    *) args+=("$a") ;;
  esac
done
# shellcheck source=scripts/activate.sh
source "$REPO/scripts/activate.sh" >/dev/null

cmd=(lerobot-teleoperate
  --robot.type=so101_follower
  --robot.port="$SO101_FOLLOWER_PORT"
  --robot.id="$SO101_FOLLOWER_ID"
  --teleop.type=so101_leader
  --teleop.port="$SO101_LEADER_PORT"
  --teleop.id="$SO101_LEADER_ID"
  "${args[@]}")
printf '[teleop] HF_LEROBOT_CALIBRATION=%s\n[teleop] $' "$HF_LEROBOT_CALIBRATION"
printf ' %q' "${cmd[@]}"; echo
[ "$DRY" = 1 ] || exec "${cmd[@]}"
