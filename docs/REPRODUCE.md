# 다른 Ubuntu 24.04 PC(친구 노트북)에서 재현하기

이 문서와 [AGENTS.md](../AGENTS.md)만으로 진행할 수 있게 작성했다. 연구실 PC의 사용자명, 마운트 경로, 포트, 캐시 경로는 복사하지 않는다.

## 필요 조건

- Ubuntu 24.04 x86_64(듀얼부팅 가능), 인터넷
- `git`, `curl`, `python3.12`(Ubuntu 24.04 기본). 없으면 `sudo apt install git curl python3.12`
- 작업공간 파일시스템 여유 **약 3 GB**
  - 새로 설치할 때 실측: `.venv` 1.2 GB + uv 캐시 1.1 GB + `external/` 33 MB + `.tools/` 47 MB ≈ 2.4 GB
- 필요 없는 것: ROS, GPU, NVIDIA 드라이버, CUDA, conda
- 실기를 하려면 추가로 필요하다.
  - 리더·팔로워 팔과 각 보드, 어댑터, USB 케이블
  - 팔로워를 책상에 고정할 클램프
  - 팔을 받치고 전원을 끊을 수 있는 현장 작업자

## 1. clone·설치·소프트웨어 점검

```bash
git clone <저장소 URL> ~/so101_ws
cd ~/so101_ws
scripts/install.sh
scripts/so101 doctor          # = --scope software. 종료 코드 0이면 정상(2는 WARN만)
```

- 성공 기준:
  - `doctor`에 FAIL이 없다.
  - 특히 "설치 패키지가 lock/uv.lock과 정확히 일치", "설치된 lerobot 파일 504개가 고정 SHA 소스와 바이트 단위로 일치", "torch 2.11.0+cpu"가 나온다.
- 실패하면:
  - 다운로드 오류는 다시 실행한다. 받은 파일은 `.cache/uv`에서 재사용한다.
  - 파일 손상이 의심되면 `scripts/install.sh --repair`를 실행한다.
  - 공간이 부족하면 다른 파일시스템에 clone하고 `~/so101_ws` 링크를 만든다.

## 2. 시리얼 권한

```bash
sudo usermod -aG dialout $USER
# 로그아웃 후 다시 로그인. 확인: id -nG | grep -w dialout
```

## 3. 설정과 포트 식별 (이 PC 기준으로 다시)

```bash
cp configs/robot.example.yaml configs/robot.local.yaml
scripts/so101 ports             # 보드를 한 대씩 연결하며 역할 확인
scripts/so101 find-port         # 필요 시 공식 USB 분리 방식
```

- `robot.local.yaml`에 이 PC에서 확인한 `port`를 적는다.
  - by-id가 고유하면 by-id, 아니면 by-path를 쓴다.
- `id`는 물리 팔 라벨과 같게 적는다. 연구실과 같은 팔이면 같은 id를 쓴다.
- 그다음 `scripts/so101 doctor --scope hardware`를 실행한다. 보정 전이면 보정 파일 없음 WARN만 남아야 한다.

## 4. 실물·권한·모터 확인

1. [docs/HARDWARE.md](HARDWARE.md) 2절 확인표를 채운다(전압, 어댑터 정격·극성, 보드, 배선, 고정).
2. 팔마다 전원을 넣고 `scripts/so101 probe follower`, `scripts/so101 probe leader`를 실행한다(읽기 전용).
   - 6개 응답과 모델이 일치하면 setup을 건너뛴다.
   - 응답이 없으면 HARDWARE.md 4절 순서를 따른다. setup은 ID 누락·충돌이 확인됐을 때만 한다.

## 5. 보정 이전 또는 보정

**같은 물리 팔이고 연구실 보정 백업이 있으면**

```bash
tar xzf <백업 파일> -C ~/so101_ws      # calibration/... 가 생긴다
scripts/so101 probe both                 # "보정 파일 상태: match" 확인
```

- id가 다르거나 match가 아니면 이전하지 말고 다시 보정한다.

**백업이 없거나 다른 팔이면**

```bash
scripts/so101 calibrate follower   # 팔 받치기
scripts/so101 calibrate leader
```

- 보정 후 백업한다(HARDWARE.md 5절).

## 6. 추종

```bash
scripts/so101 teleop --dry-run
scripts/so101 teleop --time-s 30      # 짧은 첫 시험: 관절 하나씩 작은 움직임, gripper 개폐
scripts/so101 teleop --time-s 240     # 3분 연속 확인용(백업 상한). 180초가 지나면 쉬는 자세 → 받치고 Ctrl+C
```

- 시작 전 래퍼가 보여 주는 자세 차이 표를 보고, 리더를 팔로워 자세 가까이 맞춘다.
- 종료: 리더를 쉬는 자세로 → 팔로워 받치기 → Ctrl+C.
  - 응답이 없으면 팔로워 전원을 차단한다.
  - 시간 상한으로 끝나도 토크가 풀려 팔이 처질 수 있다.
- 종료 후 같은 명령으로 다시 실행해 재실행을 확인한다.

## 7. 결과 기록과 커밋

`logs/runs.jsonl`의 마지막 teleop 항목에서 다음을 확인한다.

| 항목 | 의미 |
|---|---|
| `teleop_loop.loop_span_s` | 첫 루프 출력부터 마지막 루프 출력까지(초). **3분 판정 기준** |
| `teleop_loop.hz_median`, `loop_ms_p95` | 실제 루프 주기 |
| `exit_code`, `user_interrupt` | 종료 방식 |
| `cleanup.leader/follower.status` | 종료 후 토크 해제 재시도 결과(성공/실패/확인 불가) |

`docs/CURRENT_STATE.md`에 다음을 기록한다.
- 날짜, PC, 커밋, `python3.12` 패치 버전
- 실행 명령, `max_relative_target` 값
- 관찰(관절별 방향·대응, gripper), 오류, 위 수치

정량 오차를 재지 않았으면 "관찰 기반"으로 적는다. 통과한 상태만 `HARDWARE_VERIFIED` / `REPRODUCED_ON_FRIEND_PC`로 적고, 별도 커밋으로 push한다. `logs/`, `calibration/`, `robot.local.yaml`은 커밋하지 않는다.
