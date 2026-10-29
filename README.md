# so101-ws

SO-ARM101(SO-101)로 **공을 집어 그릇에 넣는 모방학습(ACT) 모델을 실행**하는 작업 공간이다.
- 이 저장소를 만든 컴퓨터(아래 0절)에서 실제로 쓴 설정을 그대로 담았다. 포트·카메라 경로, 보정 파일, 명령, 문제 해결 기록이 모두 들어 있다.
- 새 Ubuntu 24.04 x86_64 + NVIDIA GPU에서 1절 순서대로 하면 같은 환경이 만들어진다.
- 모델 가중치와 데이터셋은 Hugging Face에서 받는다. 받을 버전(커밋)은 `config/so101.env`에 고정돼 있다.

작업 경과와 측정값은 [docs/WORKLOG.md](docs/WORKLOG.md)에, 무엇을 어디서 검증했는지는 [docs/CURRENT_STATE.md](docs/CURRENT_STATE.md)에 있다.

## 0. 기준 컴퓨터(2026-10, 실측)

| 항목 | 값 |
|---|---|
| OS | Ubuntu 24.04.5 LTS x86_64, 커널 7.0.0-38-generic |
| GPU | NVIDIA GeForce RTX 5060 Laptop GPU (8 GB, sm_120), 드라이버 595.99.02 |
| conda | Miniforge 26.7.2-1(`~/miniforge3`, conda 26.7.2), 환경 **`jammin_so-arm`** |
| Python 패키지 | Python 3.12.15, LeRobot 0.6.1, torch 2.11.0+cu128, torchvision 0.26.0+cu128 등 99개(해시 고정) |
| 팔 | SO-101 leader 1대 + follower 1대, 각각 Feetech STS3215 모터 6개(ID 1~6), CH343 USB 시리얼 보드 |
| follower 포트 | `/dev/serial/by-id/usb-1a86_USB_Single_Serial_5B90151504-if00` (12V 전원, 모터 12.0~12.2V) |
| leader 포트 | `/dev/serial/by-id/usb-1a86_USB_Single_Serial_5B90151751-if00` (5V 전원, 모터 6.1~6.2V) |
| 카메라 | Innomaker U20CAM-720P 2대, 640×480 30fps **MJPG** |
| front 카메라 | `/dev/v4l/by-path/pci-0000:00:14.0-usb-0:2.4:1.0-video-index0` (작업면 위에서 내려다봄, 180도 회전) |
| wrist 카메라 | `/dev/v4l/by-path/pci-0000:00:14.0-usb-0:2.1:1.0-video-index0` (그리퍼 시점) |
| USB 연결 | 네 장치 모두 노트북 USB 포트 하나에 꽂은 USB 2.0 허브(Genesys 05e3:0610)에 연결: 2.1 = wrist, 2.2 = follower, 2.3 = leader, 2.4 = front |
| 보정 파일 | `calibration/` (2026-10-23 이 컴퓨터에서 보정) |
| 모델 | [jammin0319/act_so101_ball_to_bowl_v1](https://huggingface.co/jammin0319/act_so101_ball_to_bowl_v1) @ `dd639021a17f8e7584baa100d40a5127f72ba14f`. ACT, 체크포인트 020000~140000(학습 140,000 step, batch 8, lr 1e-5) |
| 데이터셋 | [jammin0319/so101_ball_to_bowl_v1](https://huggingface.co/datasets/jammin0319/so101_ball_to_bowl_v1) @ `9a4f2b86df9c171c725fbc7c2ce9af37e4b35f08`. 100 에피소드, 45,467 프레임, 30fps, 작업 "Pick up the ball and place it in the bowl" |
| 데이터 폴더 | `~/jammin_so101_local` (models, hf_home, datasets, logs, snapshots) |

## 1. 처음부터 재현(새 Ubuntu 24.04에서 순서대로)

준비: NVIDIA 드라이버 570.86 이상(`nvidia-smi`로 확인), `git`, `curl`. CUDA Toolkit은 필요 없다(PyTorch 휠에 포함).

```bash
# 1) 받기(공개 저장소)
git clone https://github.com/jaemin0319/so101-ws.git ~/so101-ws
cd ~/so101-ws

# 2) conda 환경 jammin_so-arm 만들기 + lock 설치 + GPU 포함 점검(10~20분, 약 5 GB)
bash scripts/setup_ubuntu.sh --install-miniforge   # conda가 없을 때. 이미 있으면 --install-miniforge 빼고 실행
#   다시 실행해도 안전(같은 환경을 같은 스펙으로 갱신). 끝에 check_env 결과가 FAIL 0이어야 한다.

# 3) USB 시리얼 권한(sudo). 그다음 재부팅(또는 로그아웃 후 다시 로그인)
bash scripts/setup_system.sh --add-dialout

# 4) 모델 받기(재부팅 후). 4.3 GB, 끝나면 원격 파일 78개와 크기를 대조한다
cd ~/so101-ws
bash scripts/download.sh model
```

장치를 0절의 USB 배치대로 꽂고 확인한다.

```bash
bash scripts/check_devices.sh           # 포트·카메라 목록, 권한, config/so101.env 경로가 있는지
source scripts/activate.sh              # conda 환경 + 설정 불러오기(새 터미널마다)
python scripts/find_arms.py             # 팔 두 대의 모터 전압으로 leader/follower 판정(읽기 전용, 안 움직임)
python scripts/cam_diag.py              # front/wrist를 한 장씩 찍고, front를 학습 화면과 나란히 비교한 이미지 저장
```

**다른 PC에서**
- 팔 포트(by-id)는 보드 시리얼 번호 기준이라 그대로 맞는다.
- 카메라 경로(by-path)는 PC의 USB 구조에 따라 달라진다.
  1. `python scripts/cam_diag.py --all`로 모든 카메라를 찍어 front/wrist를 찾는다.
  2. `config/so101.env`의 `SO101_CAM_FRONT`, `SO101_CAM_WRIST`를 고친다.
- 팔 포트를 다시 정해야 하면 `python scripts/find_arms.py --write`로 설정 파일에 바로 기록한다.

## 2. 매번 쓰는 명령

```bash
cd ~/so101-ws
source scripts/activate.sh               # 새 터미널마다. conda init 없이도 된다

python scripts/cam_monitor.py            # 카메라 확인 창: o = 학습 front 화면 겹치기, s = 저장, q = 종료
bash scripts/teleop.sh                   # leader → follower 따라 움직이기(Ctrl+C 종료)
bash scripts/rollout.sh                  # 모델 실행: 체크포인트 100000, 시간 제한 없음(Ctrl+C 종료)
```

- **rollout 화면:** `rollout.sh`를 실행하면 브라우저 **http://127.0.0.1:8765** 에서 모델이 받는 카메라 화면(위 front, 아래 wrist)을 볼 수 있다.
- **카메라 충돌:** 카메라는 한 프로그램만 열 수 있다. `cam_monitor.py` 창은 닫고 `teleop`·`rollout`을 실행한다.
- **종료할 때 팔 동작:**
  - teleop을 `Ctrl+C`로 멈추면 follower 토크가 꺼져 팔이 힘없이 내려온다. 손으로 받친다.
  - rollout을 멈추면 팔이 시작 자세로 천천히 돌아간다.

### rollout 옵션

| 명령 | 뜻 |
|---|---|
| `bash scripts/rollout.sh --step 140000` | 다른 체크포인트(020000 040000 060000 080000 100000 120000 140000) |
| `bash scripts/rollout.sh --n-action-steps 20` | 추론 빈도. 모델은 한 번에 동작 100개(`chunk_size`)를 예측하고, 그중 N개를 실행한 뒤 다시 추론한다. 기본 100 = 30fps에서 3.3초마다, 20 = 0.67초마다, 1 = 매 프레임 |
| `bash scripts/rollout.sh --ensemble` | 매 프레임 추론하고 겹치는 예측을 가중 평균(temporal ensembling, `n_action_steps=1`, 계수 0.01). 반응이 빠르고 부드럽다. 이 GPU에서 추론 1회 15.7 ms(30fps 한 프레임 33 ms 안) |
| `bash scripts/rollout.sh --ensemble 0.05` | ensembling 계수 지정(클수록 이전 예측 비중이 커져 부드럽지만 반응이 느림) |
| `bash scripts/rollout.sh --duration 60` | 60초 뒤 종료(기본 0 = 무제한) |
| `bash scripts/rollout.sh --no-view` | 브라우저 화면 없이 `lerobot-rollout`만 실행 |
| `bash scripts/rollout.sh --dry-run` | 실행할 명령만 출력 |
| `bash scripts/rollout.sh --check` | 로봇·카메라를 열지 않고 lerobot이 해석한 설정(n_action_steps, 카메라 MJPG·회전 등)만 출력 |

### 스크립트가 실제로 실행하는 명령(이 컴퓨터 값)

`source scripts/activate.sh` 다음에 그대로 붙여 넣어도 같다.

```bash
# leader → follower (scripts/teleop.sh)
lerobot-teleoperate \
  --robot.type=so101_follower \
  --robot.port=/dev/serial/by-id/usb-1a86_USB_Single_Serial_5B90151504-if00 \
  --robot.id=so101_follower \
  --teleop.type=so101_leader \
  --teleop.port=/dev/serial/by-id/usb-1a86_USB_Single_Serial_5B90151751-if00 \
  --teleop.id=so101_leader

# 모델 실행 (scripts/rollout.sh --ensemble). 브라우저 화면이 필요 없으면 첫 줄을 lerobot-rollout으로
python scripts/rollout_view.py \
  --strategy.type=base \
  --policy.path=$HOME/jammin_so101_local/models/act_so101_ball_to_bowl_v1/checkpoints/100000/pretrained_model \
  --policy.n_action_steps=1 \
  --policy.temporal_ensemble_coeff=0.01 \
  --device=cuda \
  --robot.type=so101_follower \
  --robot.port=/dev/serial/by-id/usb-1a86_USB_Single_Serial_5B90151504-if00 \
  --robot.id=so101_follower \
  --robot.cameras="{front: {type: opencv, index_or_path: /dev/v4l/by-path/pci-0000:00:14.0-usb-0:2.4:1.0-video-index0, width: 640, height: 480, fps: 30, fourcc: MJPG, rotation: 180}, wrist: {type: opencv, index_or_path: /dev/v4l/by-path/pci-0000:00:14.0-usb-0:2.1:1.0-video-index0, width: 640, height: 480, fps: 30, fourcc: MJPG}}" \
  --fps=30
```

`HF_LEROBOT_CALIBRATION`(보정 파일 위치)은 `activate.sh`가 이 저장소의 `calibration/`으로 정한다.
- 다시 보정하면(`lerobot-calibrate`, 또는 연결할 때 lerobot이 물어볼 때) 이 폴더의 JSON이 바뀐다.
- `git diff`로 확인하고 커밋한다.

## 3. 문제 해결(이 컴퓨터에서 실제로 겪은 것)

| 증상 | 원인 | 해결 |
|---|---|---|
| `conda: command not found`, `lerobot-teleoperate: command not found` | 설치 스크립트가 `~/.bashrc`를 고치지 않음(`conda init` 안 함) | `source scripts/activate.sh`. 또는 `eval "$(~/miniforge3/bin/conda shell.bash hook)" && conda activate jammin_so-arm` |
| 포트가 보이는데(`/dev/ttyACM0` `crw-rw---- root dialout`) 열리지 않음 | 사용자가 dialout 그룹이 아님 | `bash scripts/setup_system.sh --add-dialout` 후 재부팅. 재부팅 전 임시로는 `sg dialout -c "명령"` |
| front 카메라가 깜빡이고 일부가 깨지거나 좌우로 밀려 반전된 것처럼 보임 | 카메라 2대·보드 2개가 USB 2.0 허브 하나를 같이 쓰는데 기본 YUYV(비압축) 두 대는 대역폭 부족. front 150장 중 31~36장 깨짐 | 카메라 설정에 `fourcc: MJPG`(config 기본값). MJPG면 0장. 측정: `python scripts/cam_diag.py --bandwidth` |
| `/dev/v4l/by-id`가 재부팅 후 다른 카메라를 가리킴 | 두 카메라 USB 시리얼이 같음(SN0001) | by-path(USB 포트 위치) 사용. 같은 허브 구멍에 꽂기 |
| `Failed to open OpenCVCamera(...)`, `can't be used to capture by name` | 다른 프로그램(cam_monitor·teleop·rollout)이 카메라 사용 중 | 그 프로그램을 끈다. 누가 쓰는지 보려면 `fuser -v /dev/video*` |
| `cv2.imshow` 창이 안 뜸 | lock의 OpenCV가 `opencv-python-headless` | `scripts/cam_monitor.py`(Tk 창) 사용 |
| `--display_data=true` 실패 | rerun·foxglove(lerobot `viz` extra)가 lock에 없음 | `scripts/rollout_view.py`(rollout.sh 기본)가 브라우저로 보여 줌 |
| `Record loop is running slower (29.8 Hz) than the target FPS (30.0 Hz)` | 카메라 fps가 아니라 제어 루프 한 바퀴(관측→추론→모터 명령)가 33.3 ms를 넘은 그 1회의 속도. 29.8Hz = 33.6 ms(0.3 ms 초과) | 가끔이면 무시. 계속 뜨거나 20Hz대로 떨어지면 원인 확인 |
| 카메라가 USB에서 빠졌다 다시 잡힘(`journalctl -k`에 `error -71`) | 케이블·허브 전원 불안정 | 케이블·허브 점검, 장치 번호가 바뀌어도 by-path 경로는 유지됨 |
| 데이터셋 영상을 OpenCV로 못 읽음 | 데이터셋 영상이 AV1 | PyAV(`import av`)로 디코딩 |
| 연결할 때 보정을 다시 하라고 물음 | 보정 파일과 모터 레지스터 값이 다름 | 차이를 확인하고 필요하면 다시 보정 후 커밋 |
| 모델 동작이 조금씩 빗나감 | 학습 데이터는 맥에서 수집(보정도 맥에서 함). 이 컴퓨터 보정 파일은 2026-10-23에 새로 한 것 | 카메라 위치를 학습 화면에 맞추고(`cam_monitor.py`의 `o`), 보정 차이를 의심 |

## 4. 파일

| 경로 | 내용 |
|---|---|
| `config/so101.env` | 이 컴퓨터 실측 설정: 포트·ID·카메라 경로·MJPG·회전·데이터 위치·HF repo/revision·기본 체크포인트 |
| `calibration/` | LeRobot 보정 파일(follower `robots/so_follower/so101_follower.json`, leader `teleoperators/so_leader/so101_leader.json`) |
| `reference/front_train.jpg` | 학습 데이터 front 카메라 한 장(180도 회전 적용 후). 카메라 위치 맞춤 기준 |
| `scripts/activate.sh` | (source) conda 환경 + 설정 불러오기 |
| `scripts/teleop.sh` | leader → follower |
| `scripts/rollout.sh` | 모델 실행(체크포인트·추론 빈도·ensembling·시간 제한·화면 옵션, `--check`) |
| `scripts/rollout_view.py` | rollout과 함께 브라우저 카메라 화면(카메라를 따로 열지 않음) |
| `scripts/cam_monitor.py` | 카메라 확인 창(Tk), 학습 화면 겹치기 |
| `scripts/cam_diag.py` | 카메라 경로 확인 사진, 학습 화면 비교, YUYV·MJPG 깨짐 측정 |
| `scripts/find_arms.py` | 모터 전압으로 leader/follower 판정(읽기 전용) |
| `scripts/download.sh` | 모델·데이터셋 받기 + 원격과 대조 |
| `scripts/setup_ubuntu.sh` | conda 환경 생성·갱신, lock 설치, 점검 |
| `scripts/setup_system.sh` | (sudo) dialout 그룹, v4l-utils |
| `scripts/check_env.py` | 소프트웨어·CUDA 점검(29개 항목) |
| `scripts/check_devices.sh` | 포트·카메라 목록·권한, 설정 경로 존재 확인(장치를 열지 않음) |
| `environment.yml` | conda 계층: python 3.12.15, pip 26.2.1, ffmpeg 8.1.1 |
| `requirements/` | `pyproject.toml` → `uv.lock` → `linux-cu128.lock.txt`(pip 해시 고정 99개), `build.lock.txt`(feetech 빌드용 setuptools) |
| `scripts/make_lock.sh`, `scripts/check_repo.py` | (유지보수) lock 재생성, 정적 점검 |
| `.github/workflows/ci.yml` | 새 Ubuntu 24.04 러너에서 설치·재실행·점검·rollout 설정 해석 |

## 5. 설치 방식과 유지보수

- **설치 순서**
  1. conda는 python·pip·ffmpeg만 설치한다.
  2. 나머지 Python 패키지는 모두 `pip install --require-hashes --no-deps -r requirements/linux-cu128.lock.txt`로 설치한다. 다시 해결하지 않으므로 torch 등이 다른 버전으로 덮어써지지 않는다.
- **설치 스크립트가 하지 않는 것:** `setup_ubuntu.sh`는 apt·sudo·그룹 변경, 드라이버 변경, `~/.bashrc` 변경을 하지 않는다. 시스템 변경은 `setup_system.sh`의 명시 옵션으로만 한다.
- **의존성 업데이트**
  1. `requirements/pyproject.toml`을 고친다.
  2. `UV=<uv 0.12.23> UV_PYTHON=<python3.12> scripts/make_lock.sh`로 lock을 다시 만든다.
  3. `python3 scripts/check_repo.py`로 점검한다.
  4. 커밋한 뒤 `bash scripts/setup_ubuntu.sh`를 다시 실행한다.
- **CI:** push할 때마다 GitHub Actions가 새 Ubuntu 24.04에서 처음부터 설치해 본다. GPU·USB 장치는 CI로 확인되지 않는다.
- **환경 지우기:** `conda env remove -n jammin_so-arm`
