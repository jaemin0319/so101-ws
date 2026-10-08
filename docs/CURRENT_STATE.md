# CURRENT_STATE

## 요약

| 구분 | 상태 |
|---|---|
| 소프트웨어(연구실) | `SOFTWARE_PARTIAL` — 설치·비구동 검증 통과, 별도 clone 재설치 검증 진행 전 |
| 실기 | `NOT VERIFIED` — 연구실 PC에 로봇 미연결 |
| 친구 PC 재현 | `NOT VERIFIED` |
| GitHub | `PUSH_PENDING: 저장소 URL 또는 신규 저장소 이름/소유자/공개 범위 필요` |

## 연구실 PC 환경 (2026-10-08 확인)

- 호스트 `jammin-MS-7E34`, 사용자 `jammin`, Ubuntu 24.04.5 x86_64, 커널 6.8.0-139, 시스템 Python 3.12.3
- `/`(홈 포함) 여유 약 0.5 GB(99%). 그래서 작업공간을 별도 파일시스템에 둔다.
  - `/mnt/isaac` = `/dev/nvme0n1p6` ext4, 실제 마운트(`findmnt`), 여유 약 25 GB
  - 작업공간 `/mnt/isaac/so101_ws`, 진입 경로 `~/so101_ws`는 심볼릭 링크
- `~/.bashrc`가 ROS Jazzy를 source해서 `PYTHONPATH`·`LD_LIBRARY_PATH`가 설정되어 있다. 래퍼와 설치기가 제거하고 실행한다(doctor로 확인).
- 시리얼: `/dev/serial` 없음, ttyACM/ttyUSB 없음(로봇 미연결). 계정이 `dialout` 그룹이 아니다.
- brltty 설치, ModemManager active. 실제 간섭은 관찰되지 않았다(로봇 미연결).

## 진행 기록

1. 작업공간 생성: `mkdir /mnt/isaac/so101_ws`, `ln -s /mnt/isaac/so101_ws ~/so101_ws`, `git init -b main`(저장소 로컬 user = jaemin0319 noreply, 기존 저장소 관례).
2. 공식 확인
   - 태그 v0.6.1 → `7e241bd6…`(lightweight).
   - 라이선스 원문 확인: LeRobot·SO-ARM100은 Apache-2.0, feetech-servo-sdk는 Unlicense.
   - 공식 코드 경로 확인: connect·calibrate·disconnect·handshake·max_relative_target.
3. uv 0.12.23: GitHub 릴리스 자산과 공식 `.sha256` 일치를 확인하고 `.tools/uv`에 설치. 모든 다운로드·캐시·TMPDIR은 처음부터 작업공간 안(`.cache/`, `.tmp/`)이다.
4. lock 작성 과정
   - path·git 소스로 lerobot을 설치하면 원본의 `tool.uv.sources`(torch=cu128)가 적용되어 CPU 인덱스와 충돌했다(실측). 원본을 수정하지 않기 위해 PyPI lerobot 0.6.1 휠을 쓰기로 했다.
   - 휠의 504개 파일이 고정 SHA 소스와 같음을 확인했다.
   - 제약 없이 해결하면 공식 lock보다 새 버전 13개가 섞였다. `exclude-newer`(커밋 시각)와 `fsspec==2026.2.0` 제약을 주어 다시 해결했다.
   - 결과: 공식 lock과 다른 항목은 torch·torchvision의 CPU 변형뿐이다(docs/REFERENCES.md 표).
5. 설치: `scripts/install.sh`
   - 1차 시도에서 `UV_NO_CONFIG=1`이 `[tool.uv]` 설정을 무시해 `--locked` 오류가 났다. 이 변수를 제거했다.
6. 재실행·복구 검증
   - 정상 재실행: 모든 단계를 재사용했다.
   - lerobot·pyserial 제거(부분 설치) 후 재실행: doctor FAIL(stamp가 있어도 실제 상태로 판정) → `uv sync`로 2개 복구 → OK.
   - 설치된 lerobot 파일 1개를 변조: 일반 재실행은 doctor의 바이트 비교 FAIL로 중단했다. 여기서 uv 기본 하드링크 모드 때문에 **캐시까지 오염**되는 것을 발견했다.
   - 대응: `UV_LINK_MODE=copy`, `--repair = --reinstall --no-cache`로 바꾸고 오염된 캐시 항목을 정리했다(`uv cache clean lerobot`). `--repair`로 복구 → OK.
7. 비구동 검증: 아래 표.

## 비구동 검증 결과 (연구실, 2026-10-08)

| 검사 | 결과 |
|---|---|
| `scripts/so101 doctor`(software) | FAIL 0 / WARN 0 |
| 내용 | Python 3.12.3 venv, ROS·user site 격리, `uv sync --locked --check` 일치, `uv pip check` OK, lerobot 0.6.1, torch 2.11.0+cpu / torchvision 0.26.0+cpu, CUDA 패키지 없음, 설치 파일 504개 = 고정 SHA 소스, 공식 모듈 import 9/9, `lerobot-teleoperate/calibrate/setup-motors --help` exit 0, 공식 모터 맵 = 래퍼 상수, `max_relative_target` float·dict 공식 파싱 OK |
| `scripts/so101 doctor --scope hardware`(설정 없음) | FAIL "설정 파일이 없습니다 → cp …" (의도한 진단) |
| `teleop/calibrate --dry-run`(예시 설정, port null) | 공식 명령 표시, 실행 시 거부 안내 |
| `tools/verify_offline.py` | **58/58 PASS**(가짜 서보 버스 pty) |

`verify_offline` 주요 항목:
- **probe**: 정상·ID 무응답·펌웨어 차이·보정 불일치·장치 무응답·없는 포트·읽기 중 Ctrl+C·객체 소멸 모든 경우에 선로 수준 쓰기 0건, 포트 닫힘. 관절별 진단과 보정 필드 비교가 맞게 나온다.
- **독립 cleanup**
  - leader 포트 실패여도 follower는 성공한다.
  - 쓰기는 ID 1~6의 Torque_Enable(40)=0, Lock(55)=0뿐이고 목표 위치 쓰기는 없다.
  - 모터 하나가 무응답이면 그 모터만 실패로 기록한다.
  - 멈춘 팔은 1.5초 한도에 '확인 불가'로 기록한다.
- **실행기**
  - 자식 대화형 입력이 유지되고 프롬프트도 중계된다.
  - Ctrl+C에서 SIGINT는 1회만 간다(중복 없음).
  - SIGTERM과 SIGHUP(pty 닫힘)은 SIGINT로 전달되고, 자식은 SIGHUP을 무시한다.
  - 부모를 SIGKILL하면 PDEATHSIG로 자식이 정리 후 종료한다(고아 방지).
  - SIGINT를 무시하는 자식은 SIGTERM으로 단계적으로 종료한다.
  - 루프 구간과 Hz를 기록하고, after_exit 결과도 기록한다.
- **dry-run**: /dev open 0건(audit hook), 공식 Robot/Teleoperator 객체 생성 0건, 없는 포트에서도 명령을 표시한다.
- **설정·hardware doctor**: 문자열 time_s, bool fps, 관절 dict 키 누락을 거부한다. 같은 실제 장치를 가리키는 서로 다른 링크, port null, 없는 포트, 권한 없음(테스트 pty chmod 000), 다른 프로세스의 점유를 각각 진단하고, 포트는 열지 않는다.
- **teleop 사전 점검**: 보정 파일 없음·불일치면 공식 명령을 실행하지 않고 모터 쓰기도 0건이다. 조건을 충족하면 공식 인자로 실행한다.

## 미검증·한계

- 실기 전부(`NOT VERIFIED`): 실제 보드 VID:PID·by-id 고유성, 모터 응답, 보정, 추종, 실제 루프 Hz, 3분 추종, 종료·재실행.
- 가짜 서보 버스는 프로토콜 응답 시험용이다. 실제 모터의 타이밍·전기적 동작을 검증하지 않는다.
- `feetech-servo-sdk`는 sdist 빌드다. 빌드 산출물 해시는 고정되지 않는다(REFERENCES 참조).
- 통신 단절·SSH 단절에서 소프트웨어 토크 해제가 전달된다는 보장은 없다.

## 다음 행동

1. (진행 중) 이 커밋을 별도 경로로 clone해 별도 venv·별도 캐시로 재설치·비교
2. GitHub 저장소 대상 지정 → push
3. 실물 확인표 작성(HARDWARE.md) → 실기 PC에서 dialout → probe → 보정 → 추종
