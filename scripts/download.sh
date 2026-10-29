#!/usr/bin/env bash
# Hugging Face에서 모델·데이터셋을 config/so101.env의 repo·revision 그대로 받고, 원격 파일 목록·크기와 비교한다.
# 공개 repo라 로그인이 필요 없다. 받다가 끊겨도 같은 명령을 다시 실행하면 이어 받는다(이미 받은 파일은 건너뜀).
#
#   bash scripts/download.sh model               # 모델 전체: 체크포인트 020000~140000(가중치+학습 상태, 약 4.3 GB)
#   bash scripts/download.sh model --policy-only # 실행용 pretrained_model만(약 1.5 GB). 학습 재개용 optimizer 상태 제외
#   bash scripts/download.sh dataset             # 학습 데이터셋(100 에피소드, AV1 영상)
#   bash scripts/download.sh verify              # 받은 모델이 원격과 같은지만 다시 확인
#
# 저장 위치: $SO101_MODEL_DIR/<repo 이름>, $SO101_DATA_DIR/<repo 이름>. 기록: $SO101_WORK_DIR/logs/hf_download_*.log
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WHAT="${1:-}"; shift || true
POLICY_ONLY=0
for a in "$@"; do
  case "$a" in
    --policy-only) POLICY_ONLY=1 ;;
    *) echo "알 수 없는 옵션: $a (--help)" >&2; exit 2 ;;
  esac
done
case "$WHAT" in
  model|dataset|verify) ;;
  -h|--help|"") awk 'NR>1 && /^#/ {sub(/^# ?/, ""); print; next} NR>1 {exit}' "$0"; [ -n "$WHAT" ]; exit $? ;;
  *) echo "model | dataset | verify 중 하나(--help)" >&2; exit 2 ;;
esac
# shellcheck source=scripts/activate.sh
source "$REPO/scripts/activate.sh" >/dev/null
mkdir -p "$SO101_WORK_DIR/logs" "$HF_HOME"

if [ "$WHAT" = dataset ]; then
  repo="$SO101_DATASET_REPO_ID"; rev="$SO101_DATASET_REVISION"; type=dataset; dest="$SO101_DATA_DIR/$(basename "$repo")"
else
  repo="$SO101_MODEL_REPO_ID"; rev="$SO101_MODEL_REVISION"; type=model; dest="$SO101_MODEL_DIR/$(basename "$repo")"
fi
include=()
[ "$POLICY_ONLY" = 1 ] && include=(--include "checkpoints/*/pretrained_model/*")

if [ "$WHAT" != verify ]; then
  log="$SO101_WORK_DIR/logs/hf_download_${type}_$(date +%Y%m%d_%H%M%S).log"
  echo "[download] $repo@$rev → $dest (기록 $log)"
  hf download "$repo" --repo-type "$type" --revision "$rev" --local-dir "$dest" "${include[@]}" 2>&1 | tee "$log"
fi

# 원격 파일 목록·크기와 비교(--policy-only면 pretrained_model만)
python - "$repo" "$rev" "$type" "$dest" "$POLICY_ONLY" <<'EOF'
import os, sys
from huggingface_hub import HfApi
repo, rev, typ, dest, policy_only = sys.argv[1:6]
api = HfApi()
info = (api.dataset_info if typ == "dataset" else api.model_info)(repo, revision=rev, files_metadata=True)
files = [s for s in info.siblings if policy_only != "1" or "/pretrained_model/" in s.rfilename]
bad = []
for s in files:
    p = os.path.join(dest, s.rfilename)
    if not os.path.exists(p):
        bad.append(f"없음 {s.rfilename}")
    elif s.size is not None and os.path.getsize(p) != s.size:
        bad.append(f"크기 다름 {s.rfilename} ({os.path.getsize(p)} ≠ {s.size})")
total = sum(s.size or 0 for s in files) / 1e9
print(f"[download] 확인: 원격 {len(files)}개({total:.2f} GB) 중 불일치 {len(bad)}개 — {dest}")
for b in bad[:20]:
    print("  " + b)
sys.exit(1 if bad else 0)
EOF
