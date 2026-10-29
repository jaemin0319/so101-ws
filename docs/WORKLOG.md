# 작업 기록

기준 컴퓨터(README 0절)에서 SO-101 모방학습 모델을 실행하기까지 한 일과 실측값이다. 시간은 한국 시간(KST)이다.
학습 데이터는 이전에 맥에서 수집했고(학습 위치는 이 기록 범위 밖, 모델 config의 device는 cuda), 이 기록은 Ubuntu 노트북으로 옮겨 실행한 과정이다.

## 2026-10-23

### 환경 설치
- 별도 세팅 저장소(`so-arm101_setting`)의 `setup_ubuntu.sh`로 conda 환경 `jammin_so-arm`을 만들었다. 그 설치 방식이 지금 이 저장소의 `setup_ubuntu.sh`·lock이다.
  - Python 3.12.15, LeRobot 0.6.1, torch 2.11.0+cu128
- `check_env.py`(GPU 포함)는 FAIL 없이 통과했다. RTX 5060 Laptop은 sm_120으로 인식됐다.

### 모델 다운로드
- `jammin0319/act_so101_ball_to_bowl_v1`을 `~/jammin_so101_local/models/act_so101_ball_to_bowl_v1`로 받았다.
- 첫 다운로드는 중간에 끊겨 080000까지만 받아져 있었다.
  - 문서에는 체크포인트가 100000까지라고 돼 있었지만, 원격에는 140000까지 있었다.
- 같은 폴더로 이어 받았다(revision `dd639021a17f8e7584baa100d40a5127f72ba14f`).
- 원격 파일 78개와 이름·크기를 대조해 불일치 0개를 확인했다(4.34 GB).
  - 체크포인트는 020000~140000 7개다. 각 체크포인트에 `pretrained_model`(가중치 206.7 MB)과 `training_state`(optimizer 412.8 MB)가 있다.
- 모델 설정
  - ACT, `chunk_size=100`, `n_action_steps=100`, `temporal_ensemble_coeff=None`
  - 입력: `observation.state`(6), `observation.images.front`·`wrist`(3×480×640). 출력: `action`(6)
  - 학습: 140,000 step, batch 8, lr 1e-5, seed 1000

### 장치 인식
- **USB 구조**(`lsusb -t`): 노트북 USB 2.0 포트 하나에 꽂은 4포트 허브(Genesys 05e3:0610, 480M)에 네 장치가 모두 있다.
  - 1번: 카메라(wrist)
  - 2번: 시리얼 보드(follower)
  - 3번: 시리얼 보드(leader)
  - 4번: 카메라(front)
- **시리얼:** QinHeng CH343(1a86:55d3) 2개가 `/dev/ttyACM0`(`5B90151504`), `/dev/ttyACM1`(`5B90151751`)로 잡혔다.
- **시리얼 권한:** 장치가 `crw-rw---- root dialout`인데 사용자가 dialout 그룹이 아니어서 열 수 없었다.
  - `sudo usermod -aG dialout`을 실행했다. 재부팅 전에는 `sg dialout`으로 임시 확인했고, 재부팅 후에는 그냥 열렸다.
- **모터 응답:** 두 포트 모두 모터 ID 1~6이 응답했다(모델 번호 777 = STS3215).
- **카메라:** Innomaker U20CAM-720P(0c45:6367) 2대. 둘 다 640×480 30fps로 열리는 것을 실제 프레임으로 확인했다.
  - 화면으로 판별: 그리퍼 손가락이 보이는 쪽이 wrist, 팔·그릇·공을 위에서 보는 쪽이 front.
  - 두 카메라의 USB 시리얼이 둘 다 `SN0001`이라 `/dev/v4l/by-id` 링크가 하나만 생겼다. 재부팅 전에는 front를, 재부팅 후에는 wrist를 가리켰다.
  - 그래서 USB 포트 위치 기준 `/dev/v4l/by-path`를 쓰기로 했다(허브 2.4번 = front, 2.1번 = wrist).

### leader/follower 구분
- 두 팔 모두 모델 번호가 같아(777) 모델 번호로는 구분할 수 없다.
- 모터 레지스터를 읽기만 해서 구분했다. 토크는 모두 꺼져 있었고 팔은 움직이지 않았다.
  - 읽은 레지스터: Present_Voltage(62), Torque_Enable(40), Present_Position(56)

| 포트 | 모터 전압 | 판정 |
|---|---|---|
| `5B90151504` (ttyACM0, 허브 2.2) | 12.0~12.2V | follower(12V 전원) |
| `5B90151751` (ttyACM1, 허브 2.3) | 6.1~6.2V | leader(5V 전원) |

- 같은 방법을 `scripts/find_arms.py`로 만들었다.

### 보정
- 19:10에 leader, 19:12에 follower를 이 컴퓨터에서 처음 연결할 때 lerobot 안내에 따라 보정했다(`calibration/`).
- 맥에서 데이터를 수집할 때 쓴 보정 파일을 옮겨 온 것이 아니다.

### 학습 화면과 front 카메라 비교
- 데이터셋 `jammin0319/so101_ball_to_bowl_v1`(revision `9a4f2b86df9c171c725fbc7c2ce9af37e4b35f08`)의 front 영상에서 한 프레임을 뽑았다.
  - 데이터셋: 100 에피소드, 45,467 프레임, 30fps, 작업 "Pick up the ball and place it in the bowl"
  - 영상은 AV1이라 OpenCV로는 못 읽었고 PyAV로 디코딩했다.
- 지금 front 화면에 rotation 180을 적용하면 학습 화면과 방향이 같았다. 회전 설정은 맞다.
- 처음에는 팔이 화면 왼쪽 끝에 붙어 있었다. 학습 때는 팔 받침이 아래쪽 왼쪽 1/4쯤, 그릇이 가운데 위쪽이었다.
- 카메라를 옮긴 뒤 겹쳐 보니 팔 위치가 학습 화면과 거의 일치했다. 그릇은 약간 왼쪽 위였다.
- 학습 화면 한 장을 `reference/front_train.jpg`로 저장했다.

### 카메라 확인 창
- lock의 OpenCV가 `opencv-python-headless`라 `cv2.imshow`를 쓸 수 없었다.
- Tk + PIL로 `cam_monitor.py`를 만들었다. 학습 화면 겹치기(`o`)와 저장(`s`)을 지원한다.

### front 카메라 깜빡임·깨짐(원인 확인과 해결)
- **증상:** front 화면이 깜빡이면서 일부가 깨지고, 좌우로 반전된 것 같은 노이즈가 보였다.
- **측정:** 카메라 앞을 정지시키고 front 150장을 받았다. 중앙값 영상과 비교해 2% 넘는 픽셀이 크게 다른 프레임을 깨진 것으로 셌다.

| 조건 | 깨진 프레임 |
|---|---|
| YUYV, front만 | 0 / 150 |
| YUYV, front와 wrist 동시 | **31 / 150** (최악 12.7% 픽셀) |
| lerobot `OpenCVCamera`, 기본값(YUYV), 동시 | **36 / 150** (최악 15.7%) |
| MJPG, front만 | 0 / 150 |
| MJPG, 동시 | **0 / 150** |
| lerobot `OpenCVCamera`, `fourcc=MJPG`, 동시 | **0 / 150** |

- **원인:** 카메라 두 대가 USB 2.0 허브 하나를 같이 쓴다. 비압축 YUYV 640×480 30fps(약 18 MB/s) 두 대는 대역폭 한계에 가까워 패킷이 빠졌다.
  - 깨진 프레임은 아래쪽 절반이 옆으로 밀려 있었다.
- **해결:** 카메라 설정에 `fourcc: MJPG`를 넣었다(`config/so101.env`의 `SO101_CAM_FOURCC`). 같은 측정은 `scripts/cam_diag.py --bandwidth`로 할 수 있다.

### 모델 실행 옵션
- **추론 빈도:** `--policy.n_action_steps=N`(1~100, `chunk_size` 100 이하)으로 바꿀 수 있다. CLI override로 값이 실제로 바뀌는 것을 확인했다.
- **temporal ensembling:** `--policy.n_action_steps=1 --policy.temporal_ensemble_coeff=0.01`. RTX 5060에서 가짜 입력으로 `select_action` 1회에 15.7 ms가 걸렸다.
- **시간 제한:** `--duration`을 빼면 무제한이다(lerobot 기본 0).

### rollout 중 카메라 보기
- lerobot의 `--display_data`는 rerun 또는 foxglove가 필요한데, 둘 다 `viz` extra라 lock에 없다.
- 그래서 `rollout_view.py`를 만들었다. `SOFollower.get_observation`을 가로채 정책 입력 프레임을 브라우저(MJPEG, 127.0.0.1:8765)로 보여 준다. 카메라를 따로 열지 않는다.
- 사용자가 실제 rollout 중에 브라우저로 화면을 확인했다. 그 뒤 요청에 따라 front 위, wrist 아래 배치로 바꿨다.

### 확인한 경고와 이벤트
- **루프 속도 경고:** `Record loop is running slower (29.8 Hz) than the target FPS (30.0 Hz)`가 떴다.
  - lerobot base 전략은 한 바퀴(관측→추론→동작 전송)가 1/fps를 넘을 때마다 그 한 바퀴 기준으로 경고한다.
  - 29.8Hz는 33.6 ms이니 0.3 ms 늦은 것이다. 카메라 fps와는 별개다.
- **카메라 재연결:** 19:21에 두 카메라가 USB에서 빠졌다가 다시 잡혔다(`error -71`). 장치 번호가 video2/4에서 video3/5로 바뀌었지만 by-path 경로는 그대로 맞았다.

## 2026-10-29

- **계정 이름 변경:** 이 컴퓨터의 로그인 계정이 `robotics`에서 `jammin`(홈 `/home/jammin`)으로 바뀌었다.
  - conda는 경로가 바뀌면 동작하지 않아 `~/miniforge3`와 `jammin_so-arm` 환경을 다시 설치했다.
  - `check_env.py --gpu required` 결과: PASS 29, FAIL 0.
    - 드라이버 595.99.02, sm_120
    - 행렬곱·cuDNN·ResNet18 GPU 순전파 통과
- **저장소 정리:** 이 저장소의 이전 내용(맥 연구용 설치기·래퍼·문서, 커밋 `ae8bb23`까지)을 지우고 위 작업 전체를 새로 담았다.
  - 이전 내용은 git 이력에 남아 있다.
- **새로 만든 스크립트:** 실행 명령을 스크립트로 정리했다(`activate.sh`, `teleop.sh`, `rollout.sh`, `download.sh`, `find_arms.py`, `cam_diag.py`).
  - 장치 없이 할 수 있는 확인을 했다: `--dry-run`, `--check`(lerobot 설정 해석), 다운로드 대조.
