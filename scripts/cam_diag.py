#!/usr/bin/env python3
"""카메라 진단: 어느 /dev/v4l/by-path가 front/wrist인지 사진으로 확인하고, YUYV·MJPG 프레임 깨짐을 측정한다.

  python scripts/cam_diag.py              # 카메라 목록 + 설정된 front/wrist를 한 장씩 저장 + front를 학습 화면과 나란히 비교
  python scripts/cam_diag.py --all        # 목록의 모든 카메라를 한 장씩(다른 PC·다른 USB 포트에서 front/wrist 경로를 찾을 때)
  python scripts/cam_diag.py --bandwidth  # 두 카메라 동시 스트리밍 시 front 깨진 프레임 수를 YUYV·MJPG로 비교(약 30초, 카메라 앞에서 움직이지 않기)

결과 이미지: $SO101_WORK_DIR/snapshots/cam_diag_<시각>/ (SO101_WORK_DIR 기본 ~/jammin_so101_local)
카메라는 한 프로그램만 열 수 있다. teleop·rollout·cam_monitor를 끄고 실행한다.

2026-10-23 이 장비 실측(front 150장 중 깨진 프레임): YUYV 단독 0, YUYV 두 대 동시 31(lerobot 경로 36), MJPG 두 대 동시 0.
원인: 카메라 2대·모터 보드 2개가 USB 2.0 허브 하나(480 Mbps)를 같이 쓰는데, 비압축 YUYV 640x480 30fps 두 대가 대역폭 한계에 가까움.
"""
from __future__ import annotations

import argparse
import glob
import os
import threading
import time
from pathlib import Path

import cv2
import numpy as np

REPO = Path(__file__).resolve().parent.parent
FRONT = os.environ.get("SO101_CAM_FRONT") or "/dev/v4l/by-path/pci-0000:00:14.0-usb-0:2.4:1.0-video-index0"
WRIST = os.environ.get("SO101_CAM_WRIST") or "/dev/v4l/by-path/pci-0000:00:14.0-usb-0:2.1:1.0-video-index0"
ROTATION = int(os.environ.get("SO101_CAM_FRONT_ROTATION") or 180)
W, H, FPS = 640, 480, 30


def open_cam(path: str, fourcc: str | None) -> tuple[cv2.VideoCapture, str]:
    cap = cv2.VideoCapture(path, cv2.CAP_V4L2)
    if fourcc:  # FOURCC는 해상도보다 먼저 설정해야 적용된다
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*fourcc))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, W)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, H)
    cap.set(cv2.CAP_PROP_FPS, FPS)
    f = int(cap.get(cv2.CAP_PROP_FOURCC))
    return cap, "".join(chr((f >> 8 * i) & 255) for i in range(4))


def usb_cameras() -> list[str]:
    paths = sorted(p for p in glob.glob("/dev/v4l/by-path/*-video-index0") if "usbv2" not in p)
    out = []
    for p in paths:
        name = Path(f"/sys/class/video4linux/{Path(os.path.realpath(p)).name}/name")
        label = name.read_text().strip() if name.exists() else "?"
        print(f"{p} -> {os.path.realpath(p)}  [{label}]")
        out.append(p)
    return out


def snapshots(out: Path, all_cams: bool) -> None:
    listed = usb_cameras()
    targets = listed if all_cams else [p for p in (FRONT, WRIST) if os.path.exists(p)]
    if not targets:
        print("   설정된 front/wrist 경로가 없음 → 위 목록에서 고른 뒤 --all로 전부 찍어 확인(노트북 내장 카메라 포함)")
    for p in targets:
        cap, fcc = open_cam(p, "MJPG")
        ok, frame = False, None
        if cap.isOpened():
            for _ in range(10):
                ok, frame = cap.read()
        cap.release()
        if not ok:
            print(f"   읽기 실패(다른 프로그램 사용 중?): {p}")
            continue
        role = "front" if p == FRONT else "wrist" if p == WRIST else "other"
        f = out / f"{role}__{Path(p).name}.jpg"
        cv2.imwrite(str(f), frame)
        print(f"   {role:5s} {fcc} {frame.shape[1]}x{frame.shape[0]} → {f}")
        if role == "front":
            ref = cv2.imread(str(REPO / "reference" / "front_train.jpg"))
            rot = {180: cv2.ROTATE_180, 90: cv2.ROTATE_90_CLOCKWISE, -90: cv2.ROTATE_90_COUNTERCLOCKWISE, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}
            now = cv2.rotate(frame, rot[ROTATION]) if ROTATION in rot else frame
            if ref is not None:
                ref = cv2.resize(ref, (now.shape[1], now.shape[0]))
                for im, t in ((ref, "train data (front)"), (now, f"now, rotation {ROTATION}")):
                    cv2.putText(im, t, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 255), 2)
                cv2.imwrite(str(out / "front_compare.jpg"), np.hstack([ref, now]))
                print(f"   학습 화면 비교 → {out / 'front_compare.jpg'}")


def bandwidth(out: Path, n: int = 150) -> None:
    def run(tag: str, fourcc: str, with_wrist: bool) -> None:
        front, ff = open_cam(FRONT, fourcc)
        if not front.isOpened():
            print(f"{tag}: front 열기 실패")
            return
        stop, wf, th = False, "-", None
        if with_wrist:
            wrist, wf = open_cam(WRIST, fourcc)

            def loop() -> None:
                while not stop:
                    wrist.read()

            th = threading.Thread(target=loop)
            th.start()
        for _ in range(15):
            front.read()
        frames, t0 = [], time.time()
        for _ in range(n):
            ok, f = front.read()
            if ok:
                frames.append(f.astype(np.int16))
        dt = time.time() - t0
        stop = True
        if th:
            th.join()
            wrist.release()
        front.release()
        med = np.median(np.stack(frames), axis=0)
        frac = np.array([(np.abs(f - med).max(axis=2) > 60).mean() for f in frames])
        cv2.imwrite(str(out / f"worst_{tag}.jpg"), frames[int(frac.argmax())].astype(np.uint8))
        print(f"{tag:18s} front={ff} wrist={wf}  {len(frames)}/{n}장 {len(frames) / dt:5.1f}fps  "
              f"깨진 프레임 {int((frac > 0.02).sum()):3d}  최악 {frac.max() * 100:5.1f}% 픽셀")

    print("측정 중(카메라 앞에서 움직이지 않기). 깨진 프레임 = 중앙값 영상과 2% 넘는 픽셀이 크게 다른 프레임")
    run("yuyv_front+wrist", "YUYV", True)
    run("yuyv_front_only", "YUYV", False)
    run("mjpg_front+wrist", "MJPG", True)
    run("mjpg_front_only", "MJPG", False)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bandwidth", action="store_true", help="YUYV·MJPG 프레임 깨짐 비교")
    ap.add_argument("--all", action="store_true", help="설정된 front/wrist뿐 아니라 목록의 모든 카메라를 한 장씩 찍기(다른 PC에서 경로 찾을 때)")
    args = ap.parse_args()
    out = Path(os.environ.get("SO101_WORK_DIR") or Path.home() / "jammin_so101_local") / "snapshots" / time.strftime("cam_diag_%Y%m%d_%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    print(f"설정: front={FRONT}\n      wrist={WRIST}\n결과 폴더: {out}")
    snapshots(out, args.all)
    if args.bandwidth:
        bandwidth(out)


if __name__ == "__main__":
    main()
