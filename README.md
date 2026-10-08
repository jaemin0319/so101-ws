# SO-101 리더→팔로워 추종 환경 (공식 LeRobot 고정 버전)

SO-ARM101 리더 팔을 손으로 움직이면 팔로워 팔이 따라 움직이는(teleoperation) 최소 환경이다.
모터 통신·ID 설정·보정·추종 루프는 **공식 LeRobot v0.6.1 코드를 그대로** 쓴다. 이 저장소에는 다음만 있다.
- 설치기
- 비구동 점검
- 읽기 전용 모터 확인
- 공식 CLI 실행 래퍼와 기록
- 문서

카메라, 데이터셋, 학습(ACT 등), ROS 브리지는 범위 밖이다.

| 항목 | 값 |
|---|---|
| LeRobot | v0.6.1 = `7e241bd630a3719a56157a497ce5d08f244784f1` (PyPI 휠이 이 SHA 소스와 바이트 단위로 같음을 확인. [docs/REFERENCES.md](docs/REFERENCES.md)) |
| Python | 3.12 (Ubuntu 24.04 시스템 `python3.12`) + 작업공간 `.venv` |
| uv | 0.12.23 (작업공간 `.tools/uv`, 공식 체크섬 검증) |
| 의존성 | `lerobot[feetech]==0.6.1`, torch 2.11.0+cpu, torchvision 0.26.0+cpu. GPU·CUDA·ROS 불필요 |
| 잠금 | [lock/uv.lock](lock/uv.lock) (해시 포함, 46개). 설치는 `uv sync --locked`만 하며 재해결하지 않음 |

상태와 검증 결과는 [docs/CURRENT_STATE.md](docs/CURRENT_STATE.md)에 있다. 다른 PC에서 재현하는 절차는 [docs/REPRODUCE.md](docs/REPRODUCE.md)에 있다.

## 0. 준비물

- Ubuntu 24.04 x86_64, `git`, `curl`, `python3.12`(기본 포함), 인터넷
- 작업공간 파일시스템 여유 공간 **약 3 GB 이상**
  - 새로 설치할 때 실측: `.venv` 1.2 GB, uv 캐시 1.1 GB, `external/` 33 MB, `.tools/` 47 MB, 합계 약 2.4 GB
- 실기에는 추가로 다음이 필요하다.
  - 리더·팔로워 각각의 통신 보드, USB, 전원
  - USB 시리얼 권한(아래 3)
  - 로봇 옆에서 팔을 받치고 전원을 끊을 수 있는 작업자

## 1. 설치

```bash
git clone <이 저장소 URL> ~/so101_ws      # 홈에 공간이 없으면 다른 파일시스템에 clone하고 ~/so101_ws 링크
cd ~/so101_ws
scripts/install.sh                         # 다시 실행하면 정상 환경은 재사용하고, 누락·부분 설치는 복구한다
scripts/so101 doctor                        # 소프트웨어 점검(로봇·포트 설정 불필요). 종료 코드 0=OK 1=FAIL 2=WARN만
```

- 설치 파일이 손상된 것 같으면 `scripts/install.sh --repair`를 실행한다. 캐시 없이 다시 받는다.
- `.venv`의 Python이 다르면 `--reset-venv`를 쓴다. 기존 `.venv`는 지우지 않고 `.venv.broken-<시각>`으로 옮긴다.
- ROS가 `.bashrc`에서 source되어 있어도 된다. `scripts/install.sh`, `scripts/so101`이 `PYTHONPATH`·`PYTHONHOME`·`LD_LIBRARY_PATH`를 제거하고 실행한다.
  - `.venv/bin/python`을 직접 실행하면 이 격리가 적용되지 않는다.

## 2. 설정

```bash
cp configs/robot.example.yaml configs/robot.local.yaml   # Git 제외 파일
```

- `leader`/`follower`의 `id`(장치 ID)와 `port`를 채운다.
  - `id`는 물리 팔 라벨과 같은 이름으로 정한다. 보정 파일 이름이 된다(모터 ID 1~6과 다름).
  - `port`는 다른 PC 값을 복사하지 말고 아래 4에서 다시 확인한다.
- 보정 파일은 기본으로 `calibration/`(Git 제외)에 저장된다. 경로는 공식 형식을 따른다.
  - `calibration/robots/so_follower/<follower id>.json`
  - `calibration/teleoperators/so_leader/<leader id>.json`

## 3. USB 시리얼 권한 (실기를 하는 PC에서 한 번)

```bash
sudo usermod -aG dialout $USER     # 사용자가 직접 실행
# 로그아웃 후 다시 로그인(SSH는 재접속). 확인: id -nG | grep dialout
```

- `chmod 666/777`은 쓰지 않는다.
- `brltty`·`ModemManager`는 실제로 포트가 사라지거나 점유되는 증상이 있을 때만 조치한다([docs/HARDWARE.md](docs/HARDWARE.md)).

## 4. 포트 식별

```bash
scripts/so101 ports              # by-id/by-path, 실제 장치, VID:PID, serial (읽기 전용)
scripts/so101 find-port          # 공식 lerobot-find-port: 안내에 따라 해당 보드 USB만 뽑고 Enter
scripts/so101 doctor --scope hardware   # 포트·권한·점유·설정·보정 파일 점검(포트를 열지 않음)
```

- 보드를 한 대씩 연결하며 역할(leader/follower)을 확인한다.
- by-id가 보드마다 고유하면 by-id를 쓴다.
- serial이 없거나 중복이면 by-path를 쓰고, 항상 같은 USB 단자에 꽂는다.

## 5. 실물·모터 확인 (읽기 전용)

먼저 [docs/HARDWARE.md](docs/HARDWARE.md)의 실물 확인표를 채운다(전압·어댑터·배선). 그다음 실행한다.

```bash
scripts/so101 probe follower     # ID·모델·펌웨어·위치·토크·보정 레지스터 읽기. 모터에 쓰지 않음
scripts/so101 probe leader
scripts/so101 probe follower --scan   # 응답이 없을 때만: 공식 scan_port로 baud별 broadcast ping
```

- 6개가 모두 응답하고 모델이 일치하면 모터 설정(setup-motors)을 **건너뛴다**.
- 응답이 없으면 다음 순서로 확인한다: 전원 → 케이블 → 보드 모드 → 포트 → baud.
- ID 누락이나 충돌이 확인됐을 때만 아래를 실행한다. 공식 안내대로 모터를 **하나씩** 연결한다.

```bash
scripts/so101 setup-motors follower   # (필요할 때만)
```

## 6. 보정

```bash
scripts/so101 calibrate follower   # 팔로워는 토크가 켜졌다가 보정 중 꺼진다 → 팔을 받친다
scripts/so101 calibrate leader
```

- 공식 순서: 모든 관절을 가동 범위 가운데에 두고 Enter → wrist_roll을 제외한 관절을 끝에서 끝까지 움직임 → Enter.
- 이미 보정 파일이 있으면 공식 프롬프트가 묻는다: Enter는 파일을 모터에 기록, `c`는 다시 보정.
- 백업은 [docs/HARDWARE.md](docs/HARDWARE.md#보정-백업복원)를 따른다.

## 7. 추종 (공식 lerobot-teleoperate)

```bash
scripts/so101 teleop --dry-run         # 공식 명령·인자 파싱만 확인(장치 열지 않음)
scripts/so101 teleop --time-s 30       # 첫 시험: 짧게, 관절 하나씩 작은 움직임
scripts/so101 teleop                   # 시간 제한 없음(설정 teleop.time_s를 따름)
```

`teleop`은 공식 명령 전에 다음을 확인하고, 하나라도 맞지 않으면 실행하지 않는다.
- 포트, 권한, 점유
- 두 보정 파일이 있는지
- probe로 6개 모터 응답, 펌웨어 동일, 보정 파일과 레지스터 일치

보정 파일이 없거나 레지스터와 다르면 공식 teleop이 대화형 보정으로 넘어가므로, 이 경우는 실행을 거부하고 `calibrate`를 안내한다. 이후 시작 자세 차이 표와 현장 확인 목록을 보여 주고 `yes` 입력을 받는다.

- `max_relative_target`(설정 파일)은 매 주기 **현재 위치 기준 목표 오프셋**만 제한한다.
  - 단위: 몸체는 도, gripper는 0~100
  - 속도나 충돌 안전을 보장하지 않는다.
  - 공식 권장값은 없다. 쓰려면 출처 없는 실기 후보임을 알고 작은 값부터 확인한다.
- 실제 루프 Hz, 추종 루프 구간(첫/마지막 루프 출력 시각), 종료·정리 결과는 `logs/runs.jsonl`에, 원본 출력은 `logs/teleop-*.log`에 남는다.

### 종료

1. 리더를 쉬는 자세로 천천히 옮겨 팔로워도 내려오게 한다.
2. **팔로워를 손으로 받친다.**
3. `Ctrl+C`를 누른다. 공식 코드가 정리하면서 토크를 해제한다.
4. 래퍼는 공식 프로세스가 끝난 뒤 양팔에 토크 해제를 **각각 독립적으로 한 번 더** 시도하고, 결과를 성공/실패/확인 불가로 출력·기록한다.
   - 실패가 있으면 종료 코드는 3이다.
5. 응답이 없거나 오류가 반복되면 팔로워 전원을 차단한다(받친 상태에서).

`--time-s`나 `teleop.time_s`로 시간이 끝나도 토크가 해제되어 팔이 처질 수 있다. 그 전에 쉬는 자세와 지지를 준비한다.

USB·통신이 끊기면 토크 해제가 모터에 전달되지 않을 수 있다. SSH가 끊길 때도, tmux를 쓸 때도 안전은 보장되지 않는다. 물리적 지지와 전원 차단이 기본이다.

### 다시 실행

종료 후 같은 명령을 다시 실행하면 된다. 실행할 때마다 사전 점검이 다시 수행된다.

## 파일

| 경로 | 역할 | Git |
|---|---|---|
| `lock/lerobot.env` | LeRobot 태그·SHA, Python minor, uv 버전·체크섬, CPU 인덱스 | 커밋 |
| `lock/pyproject.toml`, `lock/uv.lock` | 설치기가 소비하는 의존성 정의와 잠금(해시) | 커밋 |
| `configs/robot.example.yaml` | 설정 예시 | 커밋 |
| `scripts/install.sh` | 설치·재사용·복구·(유지보수) `--relock` | 커밋 |
| `scripts/so101`, `scripts/so101.py` | 환경 격리 진입점 / doctor·ports·find-port·probe·setup-motors·calibrate·teleop | 커밋 |
| `tools/verify_offline.py` | 하드웨어 없는 검증(가짜 서보 버스 pty, 신호·정리 시험). 운영 CLI 아님 | 커밋 |
| `tools/lock_tools.py` | 공식 lock과 비교(유지보수) | 커밋 |
| `.venv/`, `.tools/`, `external/lerobot/`, `.cache/`, `.tmp/`, `logs/`, `calibration/`, `configs/*.local.yaml` | PC별 산출물 | 제외 |

## 라이선스·출처

이 저장소의 코드는 공식 LeRobot을 설치·호출만 하고 복사하지 않는다. 출처와 라이선스는 [docs/REFERENCES.md](docs/REFERENCES.md)에 있다(LeRobot·SO-ARM100은 Apache-2.0).
