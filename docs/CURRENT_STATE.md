# 현재 상태

상태 라벨: `VERIFIED`(실제 실행해 확인) / `NOT VERIFIED`(아직 실행 안 함).

## 요약(2026-10-29)

| 항목 | 상태 | 근거 |
|---|---|---|
| conda 환경 `jammin_so-arm` + lock 99개 설치 | `VERIFIED`(기준 컴퓨터) | 계정 이름 변경 후 재설치. `check_env.py --gpu required` PASS 29, FAIL 0 (2026-10-29) |
| RTX 5060 Laptop(sm_120) CUDA 연산 | `VERIFIED`(기준 컴퓨터) | 드라이버 595.99.02, 행렬곱·cuDNN conv·ResNet18 GPU 순전파 통과 |
| 새 Ubuntu 24.04에서 처음부터 설치 | CI 결과 참고 | GitHub Actions ubuntu-24.04 러너(GPU 없음). 실행 기록은 저장소 Actions 탭 |
| 팔 두 대 인식·모터 응답·leader/follower 구분 | `VERIFIED`(2026-10-23) | 두 포트 모두 ID 1~6 응답, 전압 12V/6V로 구분(WORKLOG) |
| 시리얼 권한(dialout) | `VERIFIED`(2026-10-23) | 그룹 추가·재부팅 후 두 포트 읽기·쓰기 가능 |
| 카메라 2대 30fps, front/wrist 대응, rotation 180 | `VERIFIED`(2026-10-23) | 실제 프레임 촬영·학습 화면과 비교 |
| MJPG로 프레임 깨짐 해결 | `VERIFIED`(2026-10-23) | YUYV 동시 31~36/150 → MJPG 0/150(lerobot 카메라 코드 포함) |
| `lerobot-teleoperate`(leader → follower) | 연결·보정까지 `VERIFIED`(2026-10-23, 사용자 실행) | 보정 파일 생성(leader 19:10, follower 19:12). 추종 동작 자체의 확인 기록은 없음 |
| `lerobot-rollout` 체크포인트 100000 | 실행 `VERIFIED`(2026-10-23, 사용자 실행) | 실행 중 29.8Hz 경고 1회 확인(WORKLOG). 공을 그릇에 넣는 성공률은 기록 없음 |
| `rollout_view.py` 브라우저 화면 | `VERIFIED`(2026-10-23, 사용자 실행, 좌우 배치) / 위아래 배치는 가짜 프레임으로만 확인 | |
| 모델 파일 78개 원격과 일치 | `VERIFIED`(2026-10-29) | `bash scripts/download.sh verify` 불일치 0 |
| `rollout.sh` 옵션별 lerobot 설정 해석 | `VERIFIED`(2026-10-29, 장치 없이) | `--check`: 기본·`--n-action-steps 20`·`--ensemble`·`--ensemble 0.05 --step 140000 --duration 60 --no-view` 모두 의도한 값 |
| `teleop.sh`·`rollout.sh`·`find_arms.py`·`cam_diag.py`로 실제 장치 실행 | `NOT VERIFIED` | 2026-10-29 저장소 작성 시 팔·카메라 미연결. 스크립트가 만드는 명령과 읽는 레지스터는 2026-10-23에 직접 실행해 확인한 것과 같다 |

## CI가 확인하는 것과 한계

- **static**
  - `bash -n`, shellcheck
  - `py_compile`
  - `check_repo.py`: 버전 일관성, lock 해시, 설정 필수 키, 보정 파일, 비밀값, 대용량 파일
  - `--help` 동작, 다른 OS·base 환경 차단
- **install**
  - Miniforge 26.7.2-1 → `setup_ubuntu.sh --gpu skip`(새 환경 `jammin_so-arm`)
  - 재실행 시 중복 생성 없음
  - `activate.sh`
  - `teleop.sh`·`rollout.sh --dry-run`
  - 모델 설정 JSON만 받아 `rollout.sh --check`(기본·n_action_steps 20·ensemble)
  - `check_env.py --gpu skip`
- **한계:** 러너에 GPU·USB 장치가 없다. 그래서 CUDA 연산, 팔·카메라 동작은 CI로 확인되지 않는다. 이 부분은 위 표의 기준 컴퓨터 기록을 본다.

## 다른 컴퓨터에서 재현할 때 확인할 것

1. `setup_ubuntu.sh` 끝의 점검이 FAIL 0인지 본다(GPU 항목 포함).
2. `check_devices.sh`로 설정 경로가 있는지 본다. 카메라 by-path는 PC마다 달라서 `cam_diag.py --all`로 다시 찾는다.
3. `find_arms.py`로 leader/follower를 판정한다.
4. 보정 파일과 실제 팔의 차이를 lerobot이 연결할 때 물어보면, 다시 보정한 뒤 커밋한다.
