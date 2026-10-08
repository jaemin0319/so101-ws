# AGENTS.md — 이 저장소에서 작업하는 Claude Code/에이전트용 규칙

## 범위

- **목표**: 공식 LeRobot v0.6.1(`7e241bd630a3719a56157a497ce5d08f244784f1`)로 SO-101 리더→팔로워 추종(카메라 없음)을 설치·확인한다.
- **범위 밖**:
  - 카메라, 데이터셋 수집·업로드, ACT/VLA 학습·추론, 시뮬레이터, ROS 브리지
  - 자체 모터 드라이버·보정·추종 루프 작성
- **공식 코드**: `external/lerobot`(원본 참조)과 `.venv`의 lerobot을 수정하지 않는다. 버전을 바꾸려면 `lock/lerobot.env`를 고치고 `scripts/install.sh --relock` 후 REFERENCES의 비교를 갱신한다.
- **시스템**: 시스템 Python·ROS·NVIDIA/CUDA·conda/mamba·`~/.bashrc`를 변경하지 않는다.
  - `sudo pip`, `[all]` 설치, `chmod 666/777`을 쓰지 않는다.
  - 관리자 권한이 필요한 명령(`usermod` 등)은 사용자에게 안내만 한다.

## 작업 순서 (새 PC)

[docs/REPRODUCE.md](docs/REPRODUCE.md)를 그대로 따른다.

1. `scripts/install.sh`
2. `scripts/so101 doctor`(software)
3. 포트 식별
4. 실물·권한 확인
5. `probe`
6. 보정 이전 또는 `calibrate`
7. `teleop`
8. 종료·재실행
9. `docs/CURRENT_STATE.md` 기록

## 실기(모터가 움직이거나 모터에 쓰는 명령) 시작 조건

`setup-motors`, `calibrate`, `teleop`이 해당한다. `probe`·`doctor`·`ports`·`--dry-run`은 읽기 전용이다. 다음이 **모두** 충족될 때만 실행을 제안하거나 실행한다.

1. 사용자가 로봇 옆에 있고, 팔을 받치고 팔로워 전원을 바로 끊을 수 있다고 확인했다.
2. 실물을 확인하고 `docs/HARDWARE.md`에 기록했다: 모터 라벨 전압, 어댑터 정격·극성, 보드, 배선.
3. `scripts/so101 doctor --scope hardware`에 FAIL이 없다(보정 파일 없음 WARN은 보정 단계 전이면 허용).
4. 대화형 명령이다. 공식 CLI가 Enter·`yes` 입력을 요구하므로, 사람이 직접 터미널에서 실행하도록 명령을 안내한다.

사용자가 같은 조건의 실기 실행을 이미 승인하고 현장 준비를 확인했다면 같은 승인을 다시 묻지 않는다.

## 금지

- 전원이 켜진 팔의 USB·모터 케이블을 뽑는 시험을 하지 않는다.
- 통신이 안 된다는 이유로 EEPROM 초기화나 펌웨어 갱신을 하지 않는다. 진단 순서: 전원 → 케이블 → 보드 모드 → 포트 → baud(`probe --scan`).
- 펌웨어 버전이 다르다는 이유만으로 setup·초기화를 강제하지 않는다.
- 다른 팔의 보정 파일을 복사하지 않는다(장치 ID = 물리 팔 라벨).
- force push, 브랜치 삭제, 저장소 공개 범위 변경을 하지 않는다.

## 상태 기록 규칙 (`docs/CURRENT_STATE.md`)

- 날짜, PC(호스트명), 커밋, 실행 명령, 결과를 적는다. **실제로 확인한 것만** 완료로 적는다.
- 상태 라벨:
  - `SOFTWARE_PREPARED` / `SOFTWARE_PARTIAL` / `HARDWARE_VERIFIED` / `REPRODUCED_ON_FRIEND_PC` / `NOT VERIFIED`
  - GitHub: `PUSH_PENDING` / `PUSHED`
- 3분 추종은 `logs/runs.jsonl`의 `teleop_loop.loop_span_s`(첫 루프 출력부터 마지막 루프 출력까지)나 현장 시각 기록으로 판정한다. 프로세스 전체 시간(`duration_s`)만으로는 판정하지 않는다.
- 정량 추종 오차를 측정하지 않았으면 "관찰 기반"으로 적고 수치를 만들지 않는다.
- 커밋에는 `logs/`, `calibration/`, `configs/*.local.yaml`, `.venv/`, `external/`를 넣지 않는다. 의미 있는 결과는 CURRENT_STATE에 요약한다.

## 검증 도구

- `.venv/bin/python tools/verify_offline.py`
  - 하드웨어 없이 검사한다: probe 무쓰기(선로 수준), 독립 정리, 신호·입력, dry-run 무개방, 설정 검증
  - 래퍼 코드를 바꾸면 다시 실행한다.
