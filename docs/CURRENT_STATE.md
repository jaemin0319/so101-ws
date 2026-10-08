# CURRENT_STATE

## 요약

| 구분 | 상태 |
|---|---|
| 소프트웨어(연구실) | **`SOFTWARE_PREPARED`** — 고정 설치·비구동 검사·별도 clone 재설치(별도 venv·별도 캐시) 통과 |
| 실기 | `NOT VERIFIED` — 연구실 PC에 로봇 미연결 |
| 친구 PC 재현 | `NOT VERIFIED` |
| Ubuntu 22.04 / ROS 2 Humble | 지원 검증 전(검증 환경은 Ubuntu 24.04 x86_64뿐). 22.04 기본 Python은 3.10이라 `python3.12`가 없으면 설치기가 중단된다 |
| GitHub | `PUSH_PENDING: 저장소 URL 또는 신규 저장소 이름/소유자/공개 범위 필요` |

## 기준 버전과 설치 방식

- LeRobot 태그 `v0.6.1` = `7e241bd630a3719a56157a497ce5d08f244784f1`. 원본 참조와 검증용으로 `external/lerobot/`(Git 제외)에 받는다.
- 설치는 PyPI `lerobot-0.6.1` 휠(해시 고정)로 한다.
  - 이유: path·git 설치 시 원본 `tool.uv.sources`(torch=cu128)가 CPU 인덱스와 충돌하기 때문이다.
  - 동일성: 휠 파일 504개 = 고정 SHA `src/lerobot`(sha256)이다. doctor가 매번 다시 비교한다.
- 의존성: `lock/pyproject.toml`과 `lock/uv.lock`(46개, 해시 포함), `uv sync --locked`. Python 3.12, uv 0.12.23, torch 2.11.0+cpu, torchvision 0.26.0+cpu, extras `[feetech]`.

## 명령 요약 (각각 별도로 실행)

```bash
scripts/install.sh                       # 설치·재사용·부분 설치 복구
scripts/install.sh --repair              # 파일 손상 의심 시 캐시 없이 재설치
scripts/so101 doctor                     # 소프트웨어 점검(로봇 불필요)
scripts/so101 doctor --scope hardware    # 포트·권한·점유·설정·보정 파일(포트를 열지 않음)
scripts/so101 ports                      # USB 시리얼 장치 목록(읽기 전용)
scripts/so101 find-port                  # 공식 lerobot-find-port
scripts/so101 probe follower             # 모터 읽기 전용 확인
scripts/so101 probe leader
scripts/so101 setup-motors follower      # ID 누락·충돌이 확인된 경우에만
scripts/so101 setup-motors leader        # ID 누락·충돌이 확인된 경우에만
scripts/so101 calibrate follower         # 공식 보정(팔 받치기)
scripts/so101 calibrate leader
scripts/so101 teleop --dry-run           # 공식 명령·인자 파싱만 확인
scripts/so101 teleop --time-s 30         # 공식 추종(짧은 첫 시험)
```

종료: 리더를 쉬는 자세로 → 팔로워를 받침 → `Ctrl+C`. 래퍼가 양팔 토크 해제를 독립적으로 재시도하고 결과를 기록한다(실패 시 종료 코드 3). 응답이 없으면 팔로워 전원을 차단한다.

## 연구실 PC 환경 (2026-10-08 확인)

- 호스트 `jammin-MS-7E34`, 사용자 `jammin`, Ubuntu 24.04.5 x86_64, 커널 6.8.0-139, 시스템 Python 3.12.3
- `/`(홈 포함) 여유 약 0.5 GB(99%). 그래서 작업공간을 별도 파일시스템에 둔다.
  - `/mnt/isaac` = `/dev/nvme0n1p6` ext4, 실제 마운트(`findmnt`), 여유 약 25 GB
  - 작업공간 `/mnt/isaac/so101_ws`, 진입 경로 `~/so101_ws`는 심볼릭 링크
- `~/.bashrc`가 ROS Jazzy를 source해서 `PYTHONPATH`·`LD_LIBRARY_PATH`가 설정되어 있다. 래퍼와 설치기가 제거하고 실행한다(doctor로 확인).
- 시리얼: `/dev/serial` 없음, ttyACM/ttyUSB 없음(로봇 미연결). 계정이 `dialout` 그룹이 아니다.
- brltty 설치, ModemManager active. 실제 간섭은 관찰되지 않았다(로봇 미연결).

## 커밋

- `b549589`: 검증용 커밋(설치기·래퍼·문서)
- `fa45b20`: clone 검증에서 발견한 stamp 경고 순서 수정
- `68767d5`: SOFTWARE_PREPARED와 clone 재설치 결과 기록
- 이후: GitHub 업로드용 문서 정리, 업로드 상태 기록
- 브랜치 `main`

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

## 별도 clone 재설치 검증 (2026-10-08)

| 회차 | 대상 커밋 | 결과 |
|---|---|---|
| 1 | `b549589` → `/mnt/isaac/so101_verify_20261008_153641` | uv·LeRobot 소스·패키지를 새 캐시로 받아 설치 42초, doctor FAIL 0. WARN 1(설치기가 doctor 뒤에 stamp를 쓰는 순서 문제) → `fa45b20`에서 수정 |
| 2 | `fa45b20` → `/mnt/isaac/so101_verify_20261008_153907` | 설치 43초. doctor FAIL 0 / WARN 0, exit 0. 재실행 시 전 단계 재사용. `verify_offline` 58/58 PASS. 설치 후 clone의 `git status` 깨끗함 |

두 환경(연구실 작업공간 vs 회차 2 clone) 비교 결과:
- Python 3.12.3, torch 2.11.0+cpu, torchvision 0.26.0+cpu, `torch.version.cuda=None`이 같다.
- 배포본 46개 모두 **버전과 설치 파일 내용 해시가 같다**(RECORD 기준, 경로 메타데이터 제외). sdist에서 빌드한 feetech-servo-sdk도 같다.
- `lerobot-teleoperate --help` 출력이 같다(작업공간 경로 치환 후).
- stamp(SHA, uv.lock·pyproject sha256, uv, Python)가 같다.

임시 clone은 검증 후 정리했다. 결과 파일은 연구실 작업공간 `.tmp/verify_results/`에 있다(Git 제외).

### 실측 디스크 사용량

- 새로 설치할 때: `.venv` 1.2 GB + `.cache/uv` 1.1 GB + `external/` 33 MB + `.tools/` 47 MB ≈ 2.4 GB
- 연구실 작업공간: 2.6 GB(초기 하드링크 캐시 포함)
- 설치기 사전 점검 기준은 여유 3,000 MB다.

## 미검증·한계

- 실기 전부(`NOT VERIFIED`): 실제 보드 VID:PID·by-id 고유성, 모터 응답, 보정, 추종, 실제 루프 Hz, 3분 추종, 종료·재실행.
- 가짜 서보 버스는 프로토콜 응답 시험용이다. 실제 모터의 타이밍·전기적 동작을 검증하지 않는다.
- `feetech-servo-sdk`는 sdist 빌드다. 빌드 산출물 해시는 고정되지 않는다(REFERENCES 참조).
- 통신 단절·SSH 단절에서 소프트웨어 토크 해제가 전달된다는 보장은 없다.

## 다음 행동

1. 사용자: GitHub 저장소 대상 지정(기존 URL, 또는 새 저장소의 소유자·이름·공개 범위) → 일반 push
2. 실물 확인표 작성(docs/HARDWARE.md): 모터 라벨 전압, 어댑터 정격·극성, 보드 모델, 조립·판매자 기설정 여부
3. 실기 PC(친구 PC 또는 로봇을 연결한 연구실 PC)에서 docs/REPRODUCE.md 순서 진행
   - dialout → ports → probe → 보정 → teleop 30초 → 3분 → 재실행
   - 결과를 별도 커밋으로 남긴다
