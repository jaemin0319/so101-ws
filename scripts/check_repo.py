#!/usr/bin/env python3
"""저장소 정적 점검(어느 OS에서나, 표준 라이브러리만): 고정 버전 일관성·lock 형식·macOS 전용 의존성·설정·보정 파일·개인 경로·비밀값·추적 파일.

  python3 scripts/check_repo.py        # 종료 코드 0 = 통과, 1 = 문제 있음
CI(.github/workflows/ci.yml)와 커밋 전에 실행한다. 설치·네트워크·GPU가 필요 없다.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ERR: list[str] = []
OK: list[str] = []


def ok(cond: bool, msg: str) -> None:
    (OK if cond else ERR).append(msg)


def read(p: str) -> str:
    return (REPO / p).read_text()


def tracked_files() -> list[str]:
    try:
        out = subprocess.run(["git", "ls-files", "-co", "--exclude-standard"], cwd=REPO, capture_output=True, text=True, check=True).stdout
        return [x for x in out.splitlines() if x]
    except Exception:  # noqa: BLE001
        return [str(p.relative_to(REPO)) for p in REPO.rglob("*") if p.is_file() and ".git" not in p.parts]


def main() -> int:
    env = read("environment.yml")
    py = re.search(r"^\s*-\s*python=([\d.]+)\s*$", env, re.M)
    ok(bool(py) and py.group(1) == "3.12.15", f"environment.yml python=3.12.15 (현재 {py.group(1) if py else '없음'})")
    ok(bool(re.search(r"^\s*-\s*pip=\d+\.\d+(\.\d+)?\s*$", env, re.M)), "environment.yml pip 버전 고정")
    ok(bool(re.search(r"^\s*-\s*ffmpeg=8\.\d+\.\d+\s*$", env, re.M)), "environment.yml ffmpeg 8.x.y 고정(torchcodec 0.11 지원 범위)")
    ok("conda-forge" in env and "nodefaults" in env, "environment.yml 채널 conda-forge + nodefaults")
    bad_conda = [p for p in ("torch", "pytorch", "numpy", "lerobot", "torchvision", "opencv") if re.search(rf"^\s*-\s*{p}\b", env, re.M)]
    ok(not bad_conda, f"conda에서 Python 패키지를 설치하지 않음(pip lock과 중복 방지) {bad_conda}")
    name = re.search(r"^name:\s*(\S+)", env, re.M)

    setup = read("scripts/setup_ubuntu.sh")
    chk = read("scripts/check_env.py")
    ok(f'PY_VERSION="{py.group(1) if py else "?"}"' in setup, "setup_ubuntu.sh PY_VERSION = environment.yml python")
    ok(bool(name) and f'ENV_NAME="{name.group(1)}"' in setup, "setup_ubuntu.sh 기본 환경 이름 = environment.yml name")
    drv_sh = re.search(r'DRIVER_MIN="(\d+)\.(\d+)"', setup)
    drv_py = re.search(r"DRIVER_MIN = \((\d+), (\d+)\)", chk)
    ok(bool(drv_sh and drv_py) and drv_sh.groups() == drv_py.groups(), "드라이버 하한이 setup·check_env에서 같음")
    sha = re.search(r'MINIFORGE_SHA256="([0-9a-f]{64})"', setup)
    ok(bool(sha), "Miniforge 설치기 sha256 고정")
    ok("conda init" not in re.sub(r"#.*", "", setup) or "init 하지 않음" in setup, "setup이 conda init(셸 설정 변경)을 실행하지 않음")
    for bad in ("apt-get", "usermod", "sudo ", "chmod 777", "pip install --user"):
        ok(bad not in re.sub(r"#.*", "", setup), f"setup_ubuntu.sh에 '{bad}' 없음(시스템 변경은 setup_system.sh로 분리)")

    lock = read("requirements/linux-cu128.lock.txt")
    reqs = re.findall(r"^([A-Za-z0-9_.\-]+)==(\S+?)(?:\s|$)", lock, re.M)
    pins = {n.lower(): v for n, v in reqs}
    blocks = re.split(r"\n(?=[A-Za-z0-9_.\-]+==)", lock.split("\n\n", 1)[1]) if "\n\n" in lock else []
    nohash = [b.split("==")[0] for b in blocks if b.strip() and "--hash=sha256:" not in b]
    ok(len(pins) >= 90 and not nohash, f"lock 패키지 {len(pins)}개 모두 sha256 해시 있음 {nohash[:5]}")
    for n, v in (("lerobot", "0.6.1"), ("torch", "2.11.0+cu128"), ("torchvision", "0.26.0+cu128"), ("feetech-servo-sdk", "1.0.0"),
                 ("torchcodec", "0.11.1"), ("av", "15.1.0"), ("setuptools", "81.0.0")):
        ok(pins.get(n) == v, f"lock {n}=={v} (현재 {pins.get(n)})")
    exp = dict(re.findall(r'"(python|lerobot|torch|torchvision)": "([^"]+)"', chk))
    ok(exp.get("torch") == pins.get("torch") and exp.get("torchvision") == pins.get("torchvision") and exp.get("lerobot") == pins.get("lerobot"),
       "check_env 기대 버전 = lock")
    mac_only = [n for n in pins if n == "coremltools" or n.startswith("pyobjc") or n in ("appnope",)]
    ok(not mac_only, f"macOS 전용 패키지 없음 {mac_only}")
    markers = set(re.findall(r";\s*([^\\\n]+?)\s*\\", lock))
    ok(markers <= {"platform_machine == 'x86_64' and sys_platform == 'linux'"}, f"모든 항목이 Linux x86_64 전용 marker {sorted(markers)[:3]}")
    ok("--extra-index-url https://download.pytorch.org/whl/cu128" in lock and "--index-url https://pypi.org/simple" in lock, "lock 인덱스: PyPI + PyTorch cu128")
    m = re.search(r"uv\.lock \(sha256 ([0-9a-f]{64})\)", lock)
    real = hashlib.sha256((REPO / "requirements/uv.lock").read_bytes()).hexdigest()
    ok(bool(m) and m.group(1) == real, "lock.txt 머리말의 uv.lock sha256 = 실제 uv.lock")
    build = read("requirements/build.lock.txt")
    st = re.search(r"^setuptools==\S+ ;.*?(?=\n[^ ]|\Z)", lock, re.M | re.S)
    st_block = "\n".join(x for x in st.group(0).splitlines() if not x.strip().startswith("#")).strip() if st else ""
    ok(bool(st_block) and st_block in build, "build.lock.txt setuptools = lock의 setuptools(버전·해시)")
    pyproj = read("requirements/pyproject.toml")
    ok('"lerobot[feetech,training]==0.6.1"' in pyproj and 'exclude-newer = "2026-08-03T14:05:01Z"' in pyproj, "pyproject: lerobot[feetech,training]==0.6.1, 기준 시각 고정")

    wf = read(".github/workflows/ci.yml")
    uses = re.findall(r"uses:\s*([^\s#]+)", wf)
    unpinned = [u for u in uses if not re.search(r"@[0-9a-f]{40}$", u)]
    ok(bool(uses) and not unpinned, f"CI 액션이 커밋 SHA로 고정 {unpinned}")

    conf = read("config/so101.env")
    keys = dict(re.findall(r"^([A-Z_0-9]+)=(.*)$", conf, re.M))
    need = ("SO101_ENV_NAME", "SO101_FOLLOWER_PORT", "SO101_LEADER_PORT", "SO101_FOLLOWER_ID", "SO101_LEADER_ID",
            "SO101_CAM_FRONT", "SO101_CAM_WRIST", "SO101_CAM_FOURCC", "SO101_CAM_FRONT_ROTATION", "HF_LEROBOT_CALIBRATION",
            "SO101_MODEL_REPO_ID", "SO101_MODEL_REVISION", "SO101_CHECKPOINT")
    ok(all(k in keys for k in need), f"config/so101.env 필수 키 {[k for k in need if k not in keys]}")
    ok(bool(name) and keys.get("SO101_ENV_NAME") == f"${{SO101_ENV_NAME:-{name.group(1)}}}", "config 기본 환경 이름 = environment.yml name")
    ok(keys.get("SO101_CAM_FOURCC") == "MJPG", "카메라 MJPG(YUYV 두 대는 USB 2.0 허브 대역폭 부족으로 프레임 깨짐)")
    ok(re.fullmatch(r"[0-9a-f]{40}", keys.get("SO101_MODEL_REVISION", "")) is not None, "모델 revision이 커밋 SHA로 고정")
    for kind, sub, key in (("robots", "so_follower", "SO101_FOLLOWER_ID"), ("teleoperators", "so_leader", "SO101_LEADER_ID")):
        p = REPO / "calibration" / kind / sub / f"{keys.get(key, '?')}.json"
        try:
            cal = json.loads(p.read_text())
            good = sorted(m["id"] for m in cal.values()) == [1, 2, 3, 4, 5, 6]
        except (OSError, ValueError, KeyError, TypeError):
            good = False
        ok(good, f"보정 파일 {p.relative_to(REPO)}: 모터 6개(id 1~6)")
    ok((REPO / "reference" / "front_train.jpg").is_file(), "reference/front_train.jpg(카메라 위치 맞춤용 학습 화면)")

    files = tracked_files()
    forbid = re.compile(r"(\.safetensors|\.pt|\.pth|\.ckpt|\.bin|\.onnx|\.mlmodelc|\.mlpackage|\.mp4|\.avi|\.parquet|\.arrow|\.npz|\.npy|\.h5|\.zip|\.tar(\.gz)?|\.sh\.partial)$|(^|/)(\.cache|\.venv|logs|datasets|models|outputs|wandb|snapshots|hf_home)/|(^|/)\.env$|\.git-credentials|(^|/)token$")
    bad_files = [f for f in files if forbid.search(f)]
    ok(not bad_files, f"추적 파일에 데이터·가중치·캐시·토큰 없음 {bad_files[:5]}")
    big = [f for f in files if (REPO / f).is_file() and (REPO / f).stat().st_size > 1_000_000]
    ok(not big, f"1 MB 넘는 추적 파일 없음 {big}")
    secret = re.compile(r"hf_[A-Za-z0-9]{30,}|ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----")
    leaked = [f for f in files if (REPO / f).is_file() and f != "scripts/check_repo.py" and secret.search((REPO / f).read_text(errors="ignore"))]
    ok(not leaked, f"비밀값 패턴 없음 {leaked}")
    personal = re.compile(r"/Users/[A-Za-z]|/home/[a-z][a-z0-9_-]*/")
    code = [f for f in files if not f.startswith("docs/") and f not in ("README.md",) and f != "scripts/check_repo.py"]
    hard = [f for f in code if (REPO / f).is_file() and personal.search((REPO / f).read_text(errors="ignore"))]
    ok(not hard, f"스크립트·설정에 개인 절대경로 없음(문서 제외) {hard}")

    for m_ in OK:
        print(f"[PASS] {m_}")
    for m_ in ERR:
        print(f"[FAIL] {m_}")
    print(f"\n결과: PASS {len(OK)}, FAIL {len(ERR)}")
    return 1 if ERR else 0


if __name__ == "__main__":
    sys.exit(main())
