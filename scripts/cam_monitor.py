#!/usr/bin/env python3
"""SO-101 front·wrist 카메라 실시간 확인 창(Tk). 학습 데이터 front 화면을 겹쳐 카메라 위치를 맞출 때 쓴다.

  source scripts/activate.sh && python scripts/cam_monitor.py      # config/so101.env의 SO101_CAM_FRONT/WRIST 사용
  python scripts/cam_monitor.py --front /dev/video4 --wrist /dev/video2

키: o = 학습 front 화면(reference/front_train.jpg) 겹치기 켜기/끄기, s = 현재 화면 저장($SO101_WORK_DIR/snapshots/),
    q 또는 Esc = 종료.
설치된 OpenCV가 headless(창 없음)라 cv2.imshow 대신 Tk 창을 쓴다.
카메라는 한 프로그램만 열 수 있다. lerobot-teleoperate·lerobot-rollout을 실행하기 전에 이 창을 닫는다.
front는 lerobot과 같게 rotation을 적용해서 보여 준다(정책이 보는 화면과 같음).
두 카메라 모두 MJPG로 받는다(YUYV 두 대는 USB 2.0 허브 대역폭 부족으로 프레임이 깨짐). lerobot에서도 fourcc: MJPG를 준다.
"""
from __future__ import annotations

import argparse
import os
import threading
import time
import tkinter as tk
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageTk

REPO = Path(__file__).resolve().parent.parent
WORK = Path(os.environ.get("SO101_WORK_DIR") or Path.home() / "jammin_so101_local")
# 이 PC에서 확인한 값(2026-10-23, config/so101.env와 같음). 두 카메라 시리얼이 같아 by-id 대신 USB 포트 기준 by-path를 쓴다.
DEFAULT_FRONT = "/dev/v4l/by-path/pci-0000:00:14.0-usb-0:2.4:1.0-video-index0"
DEFAULT_WRIST = "/dev/v4l/by-path/pci-0000:00:14.0-usb-0:2.1:1.0-video-index0"
# lerobot Cv2Rotation 값 → cv2.rotate 코드
ROTATE = {0: None, 90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, -90: cv2.ROTATE_90_COUNTERCLOCKWISE, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}
W, H = 640, 480


class Camera:
    """별도 스레드에서 최신 프레임만 계속 읽어 둔다."""

    def __init__(self, name: str, path: str, rotation: int):
        self.name, self.path, self.rot = name, path, ROTATE[rotation]
        self.frame: np.ndarray | None = None
        self.fps = 0.0
        self.cap = cv2.VideoCapture(path, cv2.CAP_V4L2)
        # 두 카메라가 USB 2.0 허브 하나를 같이 쓴다. 기본 YUYV(비압축) 두 대는 대역폭이 모자라 프레임이 깨지므로 MJPG로 받는다.
        # FOURCC는 해상도보다 먼저 설정해야 적용된다.
        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, W)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, H)
        self.cap.set(cv2.CAP_PROP_FPS, 30)
        self.running = self.cap.isOpened()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def _loop(self) -> None:
        n, t0 = 0, time.time()
        while self.running:
            ok, f = self.cap.read()
            if not ok:
                self.frame = None
                time.sleep(0.1)
                continue
            self.frame = cv2.rotate(f, self.rot) if self.rot is not None else f
            n += 1
            if (dt := time.time() - t0) >= 1.0:
                self.fps, n, t0 = n / dt, 0, time.time()

    def close(self) -> None:
        self.running = False
        self.thread.join(timeout=1.0)
        self.cap.release()


def panel(cam: Camera, overlay: np.ndarray | None) -> np.ndarray:
    f = cam.frame
    if f is None:
        img = np.zeros((H, W, 3), np.uint8)
        cv2.putText(img, "NO SIGNAL", (W // 2 - 110, H // 2), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 255), 3)
    else:
        img = cv2.resize(f, (W, H)) if f.shape[:2] != (H, W) else f.copy()
        if overlay is not None:
            img = cv2.addWeighted(img, 0.5, overlay, 0.5, 0)
    label = f"{cam.name}  {cam.fps:4.1f}fps" + ("  +train overlay" if overlay is not None else "")
    cv2.putText(img, label, (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 4)
    cv2.putText(img, label, (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
    return img


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--front", default=os.environ.get("SO101_CAM_FRONT") or DEFAULT_FRONT)
    ap.add_argument("--wrist", default=os.environ.get("SO101_CAM_WRIST") or DEFAULT_WRIST)
    ap.add_argument("--front-rotation", type=int, default=int(os.environ.get("SO101_CAM_FRONT_ROTATION") or 180))
    ap.add_argument("--ref", type=Path, default=REPO / "reference" / "front_train.jpg", help="학습 데이터 front 기준 화면")
    args = ap.parse_args()

    ref = cv2.imread(str(args.ref)) if args.ref.exists() else None
    if ref is not None:
        ref = cv2.resize(ref, (W, H))
    cams = [Camera("front", args.front, args.front_rotation), Camera("wrist", args.wrist, 0)]
    for c in cams:
        print(f"{c.name}: {c.path} → {'열림' if c.running else '열기 실패(경로·권한·다른 프로그램 사용 중 확인)'}")

    state = {"overlay": False, "img": None}
    root = tk.Tk()
    root.title("SO-101 cameras  [o] overlay  [s] save  [q] quit")
    view = tk.Label(root)
    view.pack()

    def quit_(*_):
        for c in cams:
            c.close()
        root.destroy()

    def toggle(*_):
        if ref is None:
            print(f"기준 화면 없음: {args.ref}")
        state["overlay"] = not state["overlay"] and ref is not None

    def save(*_):
        out = WORK / "snapshots"
        out.mkdir(parents=True, exist_ok=True)
        p = out / time.strftime("cam_%Y%m%d_%H%M%S.jpg")
        cv2.imwrite(str(p), state["img"])
        print(f"저장: {p}")

    def tick():
        img = np.hstack([panel(cams[0], ref if state["overlay"] else None), panel(cams[1], None)])
        state["img"] = img
        photo = ImageTk.PhotoImage(Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB)))
        view.configure(image=photo)
        view.image = photo  # 참조를 잡아 두지 않으면 Tk가 이미지를 버린다
        root.after(33, tick)

    root.bind("o", toggle)
    root.bind("s", save)
    root.bind("q", quit_)
    root.bind("<Escape>", quit_)
    root.protocol("WM_DELETE_WINDOW", quit_)
    tick()
    root.mainloop()


if __name__ == "__main__":
    main()
