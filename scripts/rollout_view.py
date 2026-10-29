#!/usr/bin/env python3
"""lerobot-rollout을 그대로 실행하면서, 정책에 들어가는 카메라 화면을 브라우저로 보여 준다.

  bash scripts/rollout.sh            # 보통은 이걸로 실행(config/so101.env 값으로 아래 명령을 만들어 줌)
  python scripts/rollout_view.py <lerobot-rollout 인자 그대로>
  → 브라우저에서 http://127.0.0.1:8765 열기(포트 변경: SO101_VIEW_PORT=8766)

카메라는 한 프로그램만 열 수 있어 cam_monitor.py는 rollout과 같이 못 쓴다. 이 스크립트는 카메라를 따로 열지 않고,
rollout이 읽은 관측(SOFollower.get_observation)을 가로채 보여 준다. rotation이 적용된, 정책 입력과 같은 화면이다.
lerobot의 --display_data는 rerun/foxglove(lerobot viz extra)가 필요한데 이 환경 lock에 없어서 대신 쓴다.
"""
from __future__ import annotations

import os
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2
import numpy as np

W, H = 640, 480
STREAM_FPS = 15  # 브라우저 표시용. 제어 루프(30fps)와 별개
PAGE = b"""<!doctype html><html><head><meta charset="utf-8"><title>SO-101 rollout view</title>
<style>body{margin:0;background:#111;display:flex;justify-content:center}img{max-width:100%;max-height:100vh;object-fit:contain}</style>
</head><body><img src="/stream"></body></html>"""


class View:
    """get_observation이 넘겨준 최신 카메라 프레임(RGB)을 보관하고 JPEG로 합쳐 준다."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.frames: dict[str, np.ndarray] = {}
        self.times: deque[float] = deque(maxlen=60)

    def put(self, obs: dict, cam_keys) -> None:
        with self.lock:
            for k in cam_keys:
                if k in obs:
                    self.frames[k] = obs[k]
            self.times.append(time.time())

    def jpeg(self) -> bytes:
        with self.lock:
            frames = dict(self.frames)
            ts = list(self.times)
        hz = (len(ts) - 1) / (ts[-1] - ts[0]) if len(ts) > 1 and ts[-1] > ts[0] else 0.0
        stale = not ts or time.time() - ts[-1] > 1.0
        panels = []
        for name, f in frames.items() or [("waiting for rollout", None)]:
            img = np.zeros((H, W, 3), np.uint8) if f is None else cv2.cvtColor(f, cv2.COLOR_RGB2BGR)
            if img.shape[:2] != (H, W):
                img = cv2.resize(img, (W, H))
            label = f"{name}  obs {hz:4.1f}Hz" + ("  (STOPPED)" if stale and f is not None else "")
            cv2.putText(img, label, (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 4)
            cv2.putText(img, label, (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
            panels.append(img)
        return cv2.imencode(".jpg", np.vstack(panels), [cv2.IMWRITE_JPEG_QUALITY, 80])[1].tobytes()


VIEW = View()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path != "/stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(PAGE)
            return
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.end_headers()
        try:
            while True:
                jpg = VIEW.jpeg()
                self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(jpg) + jpg + b"\r\n")
                time.sleep(1 / STREAM_FPS)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *args) -> None:  # 접속 로그로 rollout 출력이 묻히지 않게
        pass


def start_server(port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def tap_follower() -> None:
    from lerobot.robots.so_follower.so_follower import SOFollower

    original = SOFollower.get_observation

    def get_observation(self):
        obs = original(self)
        VIEW.put(obs, self.cameras.keys())
        return obs

    SOFollower.get_observation = get_observation


def main() -> None:
    port = int(os.environ.get("SO101_VIEW_PORT", "8765"))
    start_server(port)
    print(f"[rollout_view] 카메라 화면: http://127.0.0.1:{port}", flush=True)
    tap_follower()
    from lerobot.scripts.lerobot_rollout import main as rollout_main

    rollout_main()  # sys.argv의 lerobot-rollout 인자를 그대로 쓴다


if __name__ == "__main__":
    main()
