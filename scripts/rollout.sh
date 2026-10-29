#!/usr/bin/env bash
# 학습된 ACT 모델로 팔로워 팔을 자동 실행(lerobot-rollout, base 전략). 실행하면 팔로워 팔이 스스로 움직인다.
#
#   bash scripts/rollout.sh                       # 체크포인트 $SO101_CHECKPOINT(100000), 모델 기본 n_action_steps(100), 시간 제한 없음
#   bash scripts/rollout.sh --step 140000         # 다른 체크포인트(020000 040000 060000 080000 100000 120000 140000)
#   bash scripts/rollout.sh --n-action-steps 20   # 추론 빈도: 예측한 100개 동작 중 몇 개를 실행하고 다시 추론할지(1~100)
#   bash scripts/rollout.sh --ensemble            # 매 프레임 추론 + temporal ensembling(n_action_steps=1, coeff 0.01)
#   bash scripts/rollout.sh --ensemble 0.05       # ensembling 계수 지정
#   bash scripts/rollout.sh --duration 60         # 초 단위 제한(기본 0 = 무제한, Ctrl+C로 종료)
#   bash scripts/rollout.sh --no-view             # 브라우저 카메라 화면 없이 lerobot-rollout만 실행
#   bash scripts/rollout.sh --dry-run             # 실행할 명령만 표시
#   bash scripts/rollout.sh --check               # 로봇·카메라를 열지 않고 lerobot이 해석한 설정만 표시(모델 config.json 필요)
#   bash scripts/rollout.sh -- --policy.device=cpu   # -- 뒤 인자는 lerobot-rollout에 그대로 넘긴다
#
# 기본으로 scripts/rollout_view.py를 거쳐 실행해 http://127.0.0.1:8765 에서 정책 입력 화면(front 위, wrist 아래)을 보여 준다.
# 카메라는 한 프로그램만 열 수 있으므로 cam_monitor.py 창은 닫고 실행한다.
# 종료(Ctrl+C·시간 제한)하면 팔이 시작 자세로 천천히 돌아간다(lerobot 기본 return_to_initial_position).
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STEP=""; NAS=""; ENSEMBLE=""; DURATION=""; VIEW=1; DRY=0; CHECK=0
extra=()
usage() { awk 'NR>1 && /^#/ {sub(/^# ?/, ""); print; next} NR>1 {exit}' "$0"; }
while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --step) STEP="${2:?--step 값 필요}"; shift 2 ;;
    --n-action-steps) NAS="${2:?--n-action-steps 값 필요}"; shift 2 ;;
    --ensemble)
      if [[ "${2:-}" =~ ^[0-9.]+$ ]]; then ENSEMBLE="$2"; shift 2; else ENSEMBLE="0.01"; shift; fi ;;
    --duration) DURATION="${2:?--duration 값 필요}"; shift 2 ;;
    --no-view) VIEW=0; shift ;;
    --dry-run) DRY=1; shift ;;
    --check) CHECK=1; shift ;;
    --) shift; extra+=("$@"); break ;;
    *) echo "알 수 없는 옵션: $1 (--help 참고. lerobot-rollout 인자는 -- 뒤에)" >&2; exit 2 ;;
  esac
done
[ -z "$NAS" ] || [ -z "$ENSEMBLE" ] || { echo "--n-action-steps와 --ensemble은 함께 쓸 수 없습니다(ensemble은 n_action_steps=1 고정)." >&2; exit 2; }
if [ -n "$NAS" ] && { ! [[ "$NAS" =~ ^[0-9]+$ ]] || [ "$NAS" -lt 1 ] || [ "$NAS" -gt 100 ]; }; then
  echo "--n-action-steps는 1~100(모델 chunk_size=100)" >&2; exit 2
fi

# shellcheck source=scripts/activate.sh
source "$REPO/scripts/activate.sh" >/dev/null
STEP="${STEP:-$SO101_CHECKPOINT}"
POLICY="$SO101_MODEL_DIR/$(basename "$SO101_MODEL_REPO_ID")/checkpoints/$STEP/pretrained_model"
if [ ! -f "$POLICY/model.safetensors" ] && [ "$DRY" != 1 ] && [ "$CHECK" != 1 ]; then
  echo "모델이 없습니다: $POLICY  → 먼저 bash scripts/download.sh model" >&2; exit 3
fi

cam() {  # cam <경로> [rotation]
  printf '{type: opencv, index_or_path: %s, width: %s, height: %s, fps: %s, fourcc: %s%s}' \
    "$1" "$SO101_CAM_WIDTH" "$SO101_CAM_HEIGHT" "$SO101_CAM_FPS" "$SO101_CAM_FOURCC" "${2:+, rotation: $2}"
}
CAMERAS="{front: $(cam "$SO101_CAM_FRONT" "$SO101_CAM_FRONT_ROTATION"), wrist: $(cam "$SO101_CAM_WRIST")}"

if [ "$VIEW" = 1 ]; then runner=(python "$REPO/scripts/rollout_view.py"); else runner=(lerobot-rollout); fi
largs=(--strategy.type=base
  --policy.path="$POLICY"
  --device=cuda
  --robot.type=so101_follower
  --robot.port="$SO101_FOLLOWER_PORT"
  --robot.id="$SO101_FOLLOWER_ID"
  --robot.cameras="$CAMERAS"
  --fps="$SO101_CAM_FPS")
[ -z "$NAS" ] || largs+=(--policy.n_action_steps="$NAS")
[ -z "$ENSEMBLE" ] || largs+=(--policy.n_action_steps=1 --policy.temporal_ensemble_coeff="$ENSEMBLE")
[ -z "$DURATION" ] || largs+=(--duration="$DURATION")
largs+=("${extra[@]}")
cmd=("${runner[@]}" "${largs[@]}")

printf '[rollout] HF_LEROBOT_CALIBRATION=%s\n[rollout] $' "$HF_LEROBOT_CALIBRATION"
for a in "${cmd[@]}"; do  # 복사해 쓸 수 있게: 공백 있는 --key=value는 값만 큰따옴표로
  if [[ "$a" == --*=*" "* ]]; then printf ' %s="%s"' "${a%%=*}" "${a#*=}"; else printf ' %s' "$a"; fi
done; echo
[ "$DRY" = 1 ] && exit 0
if [ "$CHECK" = 1 ]; then
  python - "${largs[@]}" <<'EOF'
import sys

import lerobot.scripts.lerobot_rollout  # noqa: F401  로봇·카메라·정책 설정 클래스 등록(lerobot-rollout과 같은 import)
from lerobot.configs import parser
from lerobot.rollout.configs import RolloutConfig


@parser.wrap()
def show(cfg: RolloutConfig) -> None:
    p = cfg.policy
    print(f"[check] policy {p.type}: {p.pretrained_path}")
    print(f"[check]   n_action_steps={p.n_action_steps} chunk_size={p.chunk_size} temporal_ensemble_coeff={p.temporal_ensemble_coeff}")
    print(f"[check] robot {cfg.robot.type}: port={cfg.robot.port} id={cfg.robot.id} calibration_dir={cfg.robot.calibration_dir or '(HF_LEROBOT_CALIBRATION)'}")
    for k, c in cfg.robot.cameras.items():
        print(f"[check]   camera {k}: {c.index_or_path} {c.width}x{c.height}@{c.fps} fourcc={c.fourcc} rotation={int(c.rotation)}")
    print(f"[check] fps={cfg.fps} duration={cfg.duration}(0=무제한) device={cfg.device} strategy={type(cfg.strategy).__name__}")


sys.argv = ["lerobot-rollout", *sys.argv[1:]]
show()
EOF
  exit $?
fi
exec "${cmd[@]}"
