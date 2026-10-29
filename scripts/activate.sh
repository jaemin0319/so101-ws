# shellcheck shell=bash
# 현재 셸에 SO-101 환경을 켠다: conda 환경 활성화 + config/so101.env 불러오기. 실행이 아니라 source로 쓴다.
#
#   source scripts/activate.sh
#
# conda가 셸에 초기화돼 있지 않아도 된다(conda init 없이 "conda: command not found"를 피한다).
# conda 찾는 순서: $CONDA_EXE, PATH의 conda, $CONDA/bin/conda, ~/miniforge3, ~/miniconda3, ~/anaconda3, /opt/conda
# 환경 이름을 바꾸려면: SO101_ENV_NAME=다른이름 source scripts/activate.sh

SO101_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export SO101_REPO
set -a
# shellcheck source=config/so101.env
. "$SO101_REPO/config/so101.env"
set +a

_so101_conda=""
for _c in "${CONDA_EXE:-}" "$(command -v conda 2>/dev/null || true)" "${CONDA:+$CONDA/bin/conda}" \
          "$HOME/miniforge3/bin/conda" "$HOME/miniconda3/bin/conda" "$HOME/anaconda3/bin/conda" "/opt/conda/bin/conda"; do
  if [ -n "$_c" ] && [ -x "$_c" ]; then _so101_conda="$_c"; break; fi
done
unset _c
if [ -z "$_so101_conda" ]; then
  echo "[activate] conda를 찾지 못했습니다. 먼저 설치: bash scripts/setup_ubuntu.sh --install-miniforge" >&2
  unset _so101_conda
  return 1 2>/dev/null || exit 1
fi
eval "$("$_so101_conda" shell.bash hook)"
unset _so101_conda
if ! conda activate "$SO101_ENV_NAME"; then
  echo "[activate] conda 환경 $SO101_ENV_NAME 활성화 실패. 먼저 설치: bash scripts/setup_ubuntu.sh" >&2
  return 1 2>/dev/null || exit 1
fi
echo "[activate] conda 환경: $SO101_ENV_NAME ($CONDA_PREFIX), 설정: $SO101_REPO/config/so101.env"
