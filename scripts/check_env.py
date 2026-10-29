#!/usr/bin/env python3
"""SO-101 연구 환경 소프트웨어·CUDA 점검(로봇·카메라·모델·데이터 불필요, 장치를 열지 않음).

  python scripts/check_env.py                 # 기본 --gpu required: GPU·CUDA 연산까지 확인(실패는 FAIL)
  python scripts/check_env.py --gpu skip      # GPU 점검 생략(CPU 전용 CI 등). SKIP으로 표시
  python scripts/check_env.py --gpu auto      # GPU가 없으면 SKIP, 있으면 required와 같이 확인
  python scripts/check_env.py --json out.json # 결과를 JSON으로도 저장

종료 코드: 0 = FAIL 없음(PASS/SKIP만), 1 = FAIL 있음, 2 = 사용법 오류.
점검: Python·conda 환경·OS, lock과 설치 패키지 일치, pip check, LeRobot 버전·설치 출처, 필수 import(정책·SO-101·카메라·영상·데이터셋·학습),
      torch/torchvision 조합, CUDA: GPU 이름·VRAM·compute capability·휠 지원 아키텍처·드라이버 하한·실제 GPU 연산
      (행렬곱, cuDNN conv, ResNet18 weights=None 순전파 — 사전학습 가중치를 받지 않음).
"""
from __future__ import annotations

import argparse
import importlib
import importlib.metadata as md
import json
import os
import platform
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
EXPECT = {"python": "3.12.15", "lerobot": "0.6.1", "torch": "2.11.0+cu128", "torchvision": "0.26.0+cu128", "cuda": "12.8"}
DRIVER_MIN = (570, 86)  # PyTorch cu128 휠 드라이버 하한(공식 LeRobot v0.6.1 pyproject 주석)
IGNORE_EXTRA = {"pip", "setuptools", "wheel"}  # conda가 넣는 기본 도구(setuptools는 lock에도 있음)

RESULTS: list[dict] = []


def rec(status: str, name: str, detail: str = "") -> None:
    RESULTS.append({"status": status, "name": name, "detail": detail})
    print(f"[{status:<4}] {name}" + (f"\n         {detail}" if detail else ""), flush=True)


def norm(n: str) -> str:
    return re.sub(r"[-_.]+", "-", n).lower()


def parse_lock(path: Path) -> dict[str, str]:
    pins = {}
    for line in path.read_text().splitlines():
        m = re.match(r"^([A-Za-z0-9_.\-]+)==([^\s;\\]+)", line)
        if m:
            pins[norm(m.group(1))] = m.group(2)
    return pins


def check_python_os(args) -> None:
    v = platform.python_version()
    rec("PASS" if v == EXPECT["python"] else "FAIL", f"Python {v} ({sys.executable})", "" if v == EXPECT["python"] else f"기대 {EXPECT['python']}")
    env = os.environ.get("CONDA_DEFAULT_ENV") or ""
    prefix = Path(sys.prefix)
    in_conda = (prefix / "conda-meta").is_dir()
    name = prefix.name if prefix.parent.name == "envs" else ("base" if in_conda else "")
    if not in_conda:
        rec("FAIL", "conda 환경이 아님", f"sys.prefix={prefix}. conda 환경의 python으로 실행하세요(scripts/setup_ubuntu.sh)")
    elif name == "base":
        rec("FAIL", "base 환경에서 실행 중", "개인 환경(예: jammin_so-arm)을 쓰세요")
    else:
        rec("PASS", f"conda 환경: {name} ({prefix})", f"CONDA_DEFAULT_ENV={env or '(활성화 안 함: 직접 경로로 실행)'}")
    osr = {}
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            if "=" in line:
                k, val = line.split("=", 1)
                osr[k] = val.strip('"')
    except OSError:
        pass
    target = osr.get("ID") == "ubuntu" and osr.get("VERSION_ID") == "24.04" and platform.machine() == "x86_64"
    desc = f"{osr.get('PRETTY_NAME', platform.system())} {platform.machine()}"
    rec("PASS" if target else ("SKIP" if args.allow_any_os else "FAIL"), f"OS: {desc}",
        "" if target else "대상은 Ubuntu 24.04 x86_64" + (" (--allow-any-os: 판정 제외)" if args.allow_any_os else ""))


def check_lock(lock: Path) -> None:
    if not lock.is_file():
        rec("FAIL", f"lock 파일 없음: {lock}")
        return
    pins = parse_lock(lock)
    inst = {norm(d.metadata["Name"]): d.version for d in md.distributions()}
    missing = [f"{n}=={v}" for n, v in pins.items() if n not in inst]
    wrong = [f"{n} {inst[n]}≠{v}" for n, v in pins.items() if n in inst and inst[n] != v]
    extra = sorted(n for n in inst if n not in pins and n not in IGNORE_EXTRA)
    ok = not missing and not wrong
    rec("PASS" if ok else "FAIL", f"설치 패키지 = lock({len(pins)}개, {lock.name})",
        "; ".join(filter(None, [f"없음 {missing[:8]}" if missing else "", f"버전 다름 {wrong[:8]}" if wrong else ""])))
    if extra:
        rec("PASS", f"lock 밖 추가 패키지 {len(extra)}개(참고, 판정 영향 없음)", ", ".join(extra[:15]))
    r = subprocess.run([sys.executable, "-m", "pip", "check"], capture_output=True, text=True)
    rec("PASS" if r.returncode == 0 else "FAIL", "pip check(의존성 충돌 없음)", (r.stdout + r.stderr).strip()[-300:] if r.returncode else "")


def check_lerobot() -> None:
    try:
        d = md.distribution("lerobot")
    except md.PackageNotFoundError:
        rec("FAIL", "lerobot 미설치")
        return
    du = d.read_text("direct_url.json")
    src = "PyPI 휠(인덱스 설치)" if not du else f"direct_url: {json.loads(du).get('url')}"
    editable = bool(du and json.loads(du).get("dir_info", {}).get("editable"))
    loc = Path(str(d.locate_file(""))).resolve()
    ok = d.version == EXPECT["lerobot"] and not editable and str(loc).startswith(str(Path(sys.prefix).resolve()))
    rec("PASS" if ok else "FAIL", f"LeRobot {d.version} — {src}", f"위치 {loc}" + ("; editable 설치는 기대와 다름" if editable else ""))


def check_imports() -> None:
    mods = [
        ("lerobot.policies.act.modeling_act", "ACT 정책"),
        ("lerobot.robots.so_follower", "SO-101 팔로워"),
        ("lerobot.teleoperators.so_leader", "SO-101 리더"),
        ("lerobot.cameras.opencv", "OpenCV 카메라"),
        ("lerobot.datasets.lerobot_dataset", "LeRobotDataset"),
        ("lerobot.scripts.lerobot_train", "lerobot-train"),
        ("lerobot.scripts.lerobot_record", "lerobot-record"),
        ("lerobot.scripts.lerobot_rollout", "lerobot-rollout"),
        ("scservo_sdk", "Feetech SDK"),
        ("serial", "pyserial"),
        ("cv2", "OpenCV"),
        ("av", "PyAV(영상 인코딩·디코딩)"),
        ("datasets", "HF datasets"),
        ("accelerate", "accelerate"),
        ("wandb", "wandb(학습 로깅, 기본 비활성 사용 가능)"),
    ]
    for m, label in mods:
        t0 = time.time()
        try:
            importlib.import_module(m)
            rec("PASS", f"import {m} — {label}", f"{time.time() - t0:.1f}s")
        except Exception as e:  # noqa: BLE001
            rec("FAIL", f"import {m} — {label}", f"{type(e).__name__}: {str(e)[:200]}")
    try:  # torchcodec: 환경의 FFmpeg(conda-forge ffmpeg) 공유 라이브러리를 실제로 불러오는지
        from torchcodec.decoders import VideoDecoder  # noqa: F401

        rec("PASS", f"torchcodec {md.version('torchcodec')} — FFmpeg 라이브러리 로드")
    except Exception as e:  # noqa: BLE001
        rec("FAIL", "torchcodec FFmpeg 로드 실패", f"{type(e).__name__}: {str(e)[:240]} (environment.yml의 ffmpeg 확인. LeRobot은 PyAV로 대체 가능)")


def check_torch(gpu: str) -> None:
    import torch
    import torchvision

    ok = torch.__version__ == EXPECT["torch"] and torchvision.__version__ == EXPECT["torchvision"] and torch.version.cuda == EXPECT["cuda"]
    rec("PASS" if ok else "FAIL", f"torch {torch.__version__} / torchvision {torchvision.__version__} / CUDA 런타임 {torch.version.cuda}",
        "" if ok else f"기대 {EXPECT['torch']} / {EXPECT['torchvision']} / {EXPECT['cuda']}")
    arch = torch.cuda.get_arch_list() if hasattr(torch.cuda, "get_arch_list") else []
    rec("PASS", f"휠의 CUDA 아키텍처 목록: {' '.join(arch) if arch else '(없음)'}")
    if gpu == "skip":
        rec("SKIP", "GPU 점검(--gpu skip)", "GPU 실증이 아님")
        return
    if not torch.cuda.is_available():
        rec("SKIP" if gpu == "auto" else "FAIL", "torch.cuda.is_available() = False",
            "NVIDIA 드라이버·GPU 상태 확인(nvidia-smi). 드라이버 변경은 관리자에게 요청")
        return
    drv = None
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"], capture_output=True, text=True, timeout=20).stdout
        drv = out.strip().splitlines()[0]
        dv = tuple(int(x) for x in drv.split(".")[:2])
        rec("PASS" if dv >= DRIVER_MIN else "FAIL", f"NVIDIA 드라이버 {drv}", "" if dv >= DRIVER_MIN else f"cu128 하한 {DRIVER_MIN[0]}.{DRIVER_MIN[1]} 이상 필요(관리자 요청)")
    except Exception as e:  # noqa: BLE001
        rec("FAIL", "nvidia-smi로 드라이버 버전 확인 실패", f"{type(e).__name__}: {e}")
    for i in range(torch.cuda.device_count()):
        p = torch.cuda.get_device_properties(i)
        cap = f"sm_{p.major}{p.minor}"
        supported = cap in arch or any(a.startswith("compute_") and int(a.split("_")[1]) <= p.major * 10 + p.minor for a in arch)
        rec("PASS" if supported else "FAIL", f"GPU{i}: {p.name}, VRAM {p.total_memory / 2**30:.1f} GiB, compute capability {p.major}.{p.minor} ({cap})",
            "휠에 해당 아키텍처 커널 있음" if cap in arch else ("PTX JIT로만 실행(성능·호환 확인 필요)" if supported else f"휠이 {cap}을 지원하지 않음 → 다른 CUDA 빌드 필요"))
    dev = torch.device("cuda:0")
    try:
        a = torch.randn(512, 512, generator=torch.Generator().manual_seed(0))
        b = torch.randn(512, 512, generator=torch.Generator().manual_seed(1))
        c_gpu = (a.to(dev) @ b.to(dev)).cpu()
        torch.cuda.synchronize()
        err = float((c_gpu - a @ b).abs().max())
        rec("PASS" if err < 1e-2 else "FAIL", "GPU 행렬곱(512×512) CPU 결과와 일치", f"최대 |Δ| {err:.2e}")
    except Exception as e:  # noqa: BLE001
        rec("FAIL", "GPU 행렬곱 실패", f"{type(e).__name__}: {str(e)[:300]}")
        return
    try:
        conv = torch.nn.Conv2d(3, 16, 3, padding=1).to(dev)
        y = conv(torch.randn(2, 3, 64, 64, device=dev))
        torch.cuda.synchronize()
        rec("PASS", f"cuDNN conv2d (cuDNN {torch.backends.cudnn.version()})", f"출력 {tuple(y.shape)}")
    except Exception as e:  # noqa: BLE001
        rec("FAIL", "cuDNN conv2d 실패", f"{type(e).__name__}: {str(e)[:300]}")
    try:
        from torchvision.models import resnet18

        m = resnet18(weights=None).to(dev).eval()  # 가중치 다운로드 없음
        x = torch.randn(1, 3, 480, 640, device=dev)
        with torch.inference_mode():
            for _ in range(3):
                out = m(x)
            torch.cuda.synchronize()
            t0 = time.perf_counter()
            for _ in range(10):
                out = m(x)
            torch.cuda.synchronize()
        ms = (time.perf_counter() - t0) / 10 * 1000
        rec("PASS" if torch.isfinite(out).all() else "FAIL", "ResNet18(weights=None) 순전파 1×3×480×640 on GPU", f"{ms:.1f} ms/회(참고값, 동기화 포함)")
    except Exception as e:  # noqa: BLE001
        rec("FAIL", "ResNet18 GPU 순전파 실패", f"{type(e).__name__}: {str(e)[:300]}")


def main() -> int:
    ap = argparse.ArgumentParser(description="SO-101 환경 점검(장치를 열지 않음)")
    ap.add_argument("--gpu", choices=("required", "skip", "auto"), default="required")
    ap.add_argument("--lock", type=Path, default=REPO / "requirements" / "linux-cu128.lock.txt")
    ap.add_argument("--json", type=Path, default=None)
    ap.add_argument("--allow-any-os", action="store_true", help="대상 OS가 아니어도 나머지를 점검(OS 항목은 SKIP)")
    args = ap.parse_args()
    print(f"== SO-101 환경 점검 ({time.strftime('%Y-%m-%d %H:%M:%S')}, --gpu {args.gpu})")
    check_python_os(args)
    check_lock(args.lock)
    check_lerobot()
    check_imports()
    try:
        check_torch(args.gpu)
    except Exception as e:  # noqa: BLE001
        rec("FAIL", "torch 점검 중 예외", f"{type(e).__name__}: {str(e)[:300]}")
    n = {s: sum(1 for r in RESULTS if r["status"] == s) for s in ("PASS", "FAIL", "SKIP")}
    print(f"\n결과: PASS {n['PASS']}, FAIL {n['FAIL']}, SKIP {n['SKIP']}  (종료 코드 0=FAIL 없음, 1=FAIL 있음)")
    if args.json:
        args.json.write_text(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "gpu_mode": args.gpu, "results": RESULTS},
                                        ensure_ascii=False, indent=1))
    return 1 if n["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
