#!/usr/bin/env python3
"""SO-101 리더→팔로워 추종용 얇은 래퍼.

모터 통신·설정·보정·추종 루프는 공식 LeRobot(lock/lerobot.env의 고정 버전) CLI/API를 그대로 쓴다.
이 파일이 하는 일: 설정 검증, 비구동 점검, 읽기 전용 모터 확인(probe), 공식 CLI 인자 생성·실행·기록,
추종 종료 후 양팔 토크 해제 재시도(독립 정리).

직접 실행하지 말고 scripts/so101 (환경 격리 진입점)로 실행한다. 사용법: scripts/so101 --help
"""
from __future__ import annotations

import argparse
import ctypes
import grp
import hashlib
import json
import math
import os
import platform
import pwd
import re
import select
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

WS = Path(os.environ.get("SO101_WS") or Path(__file__).resolve().parent.parent)
VENV = WS / ".venv"
VENV_BIN = VENV / "bin"
LOCK_ENV = WS / "lock" / "lerobot.env"
DEFAULT_CONFIG = WS / "configs" / "robot.local.yaml"
LOG_DIR = WS / "logs"
RUNS_LOG = LOG_DIR / "runs.jsonl"
STAMP = VENV / "so101_install_stamp.json"

# 공식 SO101 모터 맵의 관절 이름(lerobot/robots/so_follower/so_follower.py, teleoperators/so_leader/so_leader.py).
# doctor가 공식 클래스에서 다시 읽어 같은지 확인한다(official_motor_map).
JOINTS = ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper")
ROLE_TYPES = {"leader": "so101_leader", "follower": "so101_follower"}
OFFICIAL_CLIS = ("lerobot-teleoperate", "lerobot-calibrate", "lerobot-setup-motors", "lerobot-find-port", "lerobot-info")
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")

# 종료 처리 한도
INTERRUPT_GRACE_S = 20.0  # SIGINT 후 자식 종료 대기
TERM_GRACE_S = 5.0  # SIGTERM 후 대기, 그다음 SIGKILL
CLEANUP_TIMEOUT_S = 10.0  # 팔 하나의 토크 해제 재시도 한도
CLEANUP_PACKET_TIMEOUT_MS = 100  # 정리 시 버스 패킷 timeout(공식 기본 1000ms보다 짧게)

OK, WARN, FAIL, INFO = "OK", "WARN", "FAIL", "INFO"


# --------------------------------------------------------------------------------------------
# 공통 출력/기록
# --------------------------------------------------------------------------------------------
@dataclass
class Report:
    items: list[tuple[str, str, str]] = field(default_factory=list)

    def add(self, status: str, title: str, detail: str = "") -> None:
        self.items.append((status, title, detail))
        mark = {OK: "  OK ", WARN: " WARN", FAIL: " FAIL", INFO: " INFO"}[status]
        print(f"[{mark}] {title}" + (f"\n         {detail}" if detail else ""), flush=True)

    @property
    def n_fail(self) -> int:
        return sum(1 for s, _, _ in self.items if s == FAIL)

    @property
    def n_warn(self) -> int:
        return sum(1 for s, _, _ in self.items if s == WARN)

    def exit_code(self) -> int:
        """0: 모두 OK, 1: FAIL 있음(실행 불가), 2: WARN만 있음(참고)."""
        return 1 if self.n_fail else (2 if self.n_warn else 0)


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def append_run_log(entry: dict) -> None:
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(RUNS_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as e:  # 기록 실패가 원래 결과를 가리지 않게 경고만 한다
        print(f"[so101] 경고: 실행 기록 저장 실패: {e}", file=sys.stderr)


def read_lock_env() -> dict[str, str]:
    out: dict[str, str] = {}
    for line in LOCK_ENV.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


# --------------------------------------------------------------------------------------------
# 설정
# --------------------------------------------------------------------------------------------
@dataclass
class ArmCfg:
    role: str  # leader | follower
    type: str
    id: str | None
    port: str | None


@dataclass
class Config:
    path: Path
    leader: ArmCfg
    follower: ArmCfg
    disable_torque_on_disconnect: bool
    max_relative_target: float | dict[str, float] | None
    fps: int
    time_s: float | None
    calibration_root: Path

    def arm(self, role: str) -> ArmCfg:
        return self.leader if role == "leader" else self.follower


def _is_number(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(float(x))


def load_config(path: Path) -> tuple[Config | None, list[str]]:
    """YAML 설정을 읽고 타입을 검증한다. 하드웨어 전제(null 금지, 포트 존재)는 여기서 보지 않는다."""
    import yaml  # draccus 의존성으로 venv에 있다

    errors: list[str] = []
    if not path.is_file():
        return None, [f"설정 파일이 없습니다: {path}  →  cp configs/robot.example.yaml configs/robot.local.yaml 후 값을 채우세요."]
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as e:
        return None, [f"YAML 파싱 실패: {e}"]
    if not isinstance(raw, dict):
        return None, ["설정 최상위는 mapping이어야 합니다."]
    known_top = {"leader", "follower", "teleop", "calibration_root"}
    for k in raw:
        if k not in known_top:
            errors.append(f"알 수 없는 최상위 키: {k}")

    arms: dict[str, ArmCfg] = {}
    extra: dict[str, Any] = {}
    for role, expected_type in ROLE_TYPES.items():
        sec = raw.get(role)
        if not isinstance(sec, dict):
            errors.append(f"'{role}' 섹션이 없거나 mapping이 아닙니다.")
            arms[role] = ArmCfg(role, expected_type, None, None)
            continue
        allowed = {"type", "id", "port"} | ({"disable_torque_on_disconnect", "max_relative_target"} if role == "follower" else set())
        for k in sec:
            if k not in allowed:
                errors.append(f"{role}.{k}: 지원하지 않는 키입니다.")
        t = sec.get("type", expected_type)
        if t != expected_type:
            errors.append(f"{role}.type은 '{expected_type}'이어야 합니다(현재 {t!r}).")
        dev_id = sec.get("id")
        if dev_id is not None and (not isinstance(dev_id, str) or not ID_RE.match(dev_id)):
            errors.append(f"{role}.id는 null 또는 영문/숫자/_.- 문자열이어야 합니다(현재 {dev_id!r}).")
            dev_id = None
        port = sec.get("port")
        if port is not None and (not isinstance(port, str) or not os.path.isabs(port)):
            errors.append(f"{role}.port는 null 또는 절대경로 문자열이어야 합니다(예: /dev/serial/by-id/...; 현재 {port!r}).")
            port = None
        arms[role] = ArmCfg(role, expected_type, dev_id, port)
        if role == "follower":
            extra = sec

    dtod = extra.get("disable_torque_on_disconnect", True)
    if not isinstance(dtod, bool):
        errors.append("follower.disable_torque_on_disconnect는 true/false여야 합니다.")
        dtod = True
    elif dtod is False:
        errors.append("follower.disable_torque_on_disconnect=false는 허용하지 않습니다(종료 시 토크 해제가 공식 동작).")

    mrt = extra.get("max_relative_target")
    if mrt is not None:
        if _is_number(mrt):
            if float(mrt) <= 0:
                errors.append("follower.max_relative_target 숫자는 0보다 커야 합니다.")
            mrt = float(mrt)
        elif isinstance(mrt, dict):
            keys = set(mrt)
            if keys != set(JOINTS):
                errors.append(
                    "follower.max_relative_target dict는 6개 관절 키가 정확히 있어야 합니다"
                    f"(공식 ensure_safe_goal_position 조건). 누락={sorted(set(JOINTS) - keys)}, 불필요={sorted(keys - set(JOINTS))}"
                )
            bad = [k for k, v in mrt.items() if not _is_number(v) or float(v) <= 0]
            if bad:
                errors.append(f"follower.max_relative_target 값은 양수여야 합니다: {bad}")
            mrt = {k: float(v) for k, v in mrt.items() if _is_number(v)}
        else:
            errors.append("follower.max_relative_target은 null, 양수, 또는 6개 관절 dict여야 합니다.")
            mrt = None

    tsec = raw.get("teleop") or {}
    if not isinstance(tsec, dict):
        errors.append("'teleop' 섹션은 mapping이어야 합니다.")
        tsec = {}
    for k in tsec:
        if k not in {"fps", "time_s"}:
            errors.append(f"teleop.{k}: 지원하지 않는 키입니다.")
    fps = tsec.get("fps", 60)
    if not isinstance(fps, int) or isinstance(fps, bool) or fps <= 0:
        errors.append(f"teleop.fps는 양의 정수여야 합니다(현재 {fps!r}).")
        fps = 60
    time_s = tsec.get("time_s")
    if time_s is not None and (not _is_number(time_s) or float(time_s) <= 0):
        errors.append(f"teleop.time_s는 null 또는 양수(초)여야 합니다(현재 {time_s!r}).")
        time_s = None

    croot = raw.get("calibration_root")
    if croot is None:
        calib_root = WS / "calibration"
    elif isinstance(croot, str) and croot:
        calib_root = Path(os.path.expanduser(croot))
        if not calib_root.is_absolute():
            calib_root = WS / calib_root
    else:
        errors.append("calibration_root는 null 또는 경로 문자열이어야 합니다.")
        calib_root = WS / "calibration"

    cfg = Config(
        path=path,
        leader=arms["leader"],
        follower=arms["follower"],
        disable_torque_on_disconnect=True,
        max_relative_target=mrt,
        fps=fps,
        time_s=float(time_s) if time_s is not None else None,
        calibration_root=calib_root,
    )
    return cfg, errors


def apply_calibration_env(cfg: Config | None) -> None:
    """공식 코드가 import 시 읽는 HF_LEROBOT_CALIBRATION을 lerobot import 전에 설정한다."""
    root = cfg.calibration_root if cfg else WS / "calibration"
    os.environ["HF_LEROBOT_CALIBRATION"] = str(root)


def calibration_file(cfg: Config, role: str) -> Path | None:
    arm = cfg.arm(role)
    if not arm.id:
        return None
    # 공식 경로 규칙: HF_LEROBOT_CALIBRATION/{robots|teleoperators}/{클래스 name}/{id}.json
    # (lerobot/robots/robot.py, lerobot/teleoperators/teleoperator.py). 클래스 name은 so_follower/so_leader.
    sub = ("robots", "so_follower") if role == "follower" else ("teleoperators", "so_leader")
    return cfg.calibration_root / sub[0] / sub[1] / f"{arm.id}.json"


# --------------------------------------------------------------------------------------------
# 공식 클래스/명령
# --------------------------------------------------------------------------------------------
def official_device(role: str, port: str, dev_id: str, calibration_dir: Path | None = None):
    """공식 SO101Leader/SO101Follower 객체를 *생성만* 한다(connect하지 않음 → 포트를 열지 않음).
    모터 맵과 공식 보정 파일 로더 결과(obj.calibration)를 얻는 용도다. 생성자는 보정 폴더를 mkdir한다."""
    if role == "follower":
        from lerobot.robots.so_follower import SO101Follower, SO101FollowerConfig

        return SO101Follower(SO101FollowerConfig(port=port, id=dev_id, calibration_dir=calibration_dir))
    from lerobot.teleoperators.so_leader import SO101Leader, SO101LeaderConfig

    return SO101Leader(SO101LeaderConfig(port=port, id=dev_id, calibration_dir=calibration_dir))


def make_bus(role: str, port: str, dev_id: str | None):
    """공식 모터 맵·보정으로 별도 FeetechMotorsBus를 만든다.
    공식 Robot/Teleoperator의 connect/configure/calibrate 경로와 그 객체의 bus는 쓰지 않는다
    (객체 bus는 열리지 않으므로 소멸자의 disconnect(토크 쓰기)도 실행되지 않는다)."""
    from copy import deepcopy

    from lerobot.motors.feetech import FeetechMotorsBus

    obj = official_device(role, port, dev_id or "so101-unnamed")
    motors = deepcopy(obj.bus.motors)
    calib = deepcopy(obj.calibration) if dev_id else {}
    assert not obj.bus.is_connected
    del obj
    return FeetechMotorsBus(port=port, motors=motors, calibration=calib or None)


def official_cmd(name: str) -> str:
    return str(VENV_BIN / name)


def build_teleop_argv(cfg: Config, time_s: float | None, *, placeholder: bool = False) -> list[str]:
    def val(x: str | None, what: str) -> str:
        if x is None:
            if placeholder:
                return f"<{what} 미지정>"
            raise ValueError(f"{what} 미지정")
        return x

    argv = [
        official_cmd("lerobot-teleoperate"),
        "--robot.type=so101_follower",
        f"--robot.port={val(cfg.follower.port, 'follower.port')}",
        f"--robot.id={val(cfg.follower.id, 'follower.id')}",
        "--robot.disable_torque_on_disconnect=true",
        "--teleop.type=so101_leader",
        f"--teleop.port={val(cfg.leader.port, 'leader.port')}",
        f"--teleop.id={val(cfg.leader.id, 'leader.id')}",
        f"--fps={cfg.fps}",
        "--display_data=false",
    ]
    mrt = cfg.max_relative_target
    if isinstance(mrt, float):
        argv.append(f"--robot.max_relative_target={mrt!r}")
    elif isinstance(mrt, dict):
        argv.append("--robot.max_relative_target=" + json.dumps({k: float(v) for k, v in mrt.items()}))
    if time_s is not None:
        argv.append(f"--teleop_time_s={float(time_s)!r}")
    return argv


def build_device_argv(cli: str, cfg: Config, role: str, *, placeholder: bool = False) -> list[str]:
    arm = cfg.arm(role)
    key = "robot" if role == "follower" else "teleop"
    port = arm.port or ("<port 미지정>" if placeholder else None)
    dev_id = arm.id or ("<id 미지정>" if placeholder else None)
    if port is None or dev_id is None:
        raise ValueError(f"{role}.port/id 미지정")
    return [official_cmd(cli), f"--{key}.type={arm.type}", f"--{key}.port={port}", f"--{key}.id={dev_id}"]


def official_parse_check(argv: list[str]) -> tuple[bool, str]:
    """공식 TeleoperateConfig를 draccus로 파싱해 인자 타입을 확인한다(객체 생성·장치 접근 없음)."""
    import draccus

    from lerobot.scripts.lerobot_teleoperate import TeleoperateConfig

    try:
        c = draccus.parse(config_class=TeleoperateConfig, args=argv[1:])
    except BaseException as e:  # draccus는 SystemExit을 낼 수 있다
        return False, f"{type(e).__name__}: {e}"
    m = c.robot.max_relative_target
    if m is not None and not (isinstance(m, float) or (isinstance(m, dict) and all(isinstance(v, float) for v in m.values()))):
        return False, f"max_relative_target 파싱 타입이 float/dict[float]가 아님: {m!r}"
    return True, f"robot.max_relative_target={m!r}, fps={c.fps}, teleop_time_s={c.teleop_time_s!r}"


# --------------------------------------------------------------------------------------------
# 포트 정보(읽기 전용: 파일시스템/sysfs/proc만 본다. 장치를 열지 않는다)
# --------------------------------------------------------------------------------------------
def usb_info(real_dev: str) -> dict[str, str]:
    name = os.path.basename(real_dev)
    info: dict[str, str] = {}
    p = Path(f"/sys/class/tty/{name}/device")
    try:
        p = p.resolve(strict=True)
    except OSError:
        return info
    for d in [p, *p.parents]:
        if (d / "idVendor").is_file():
            for k in ("idVendor", "idProduct", "serial", "manufacturer", "product", "busnum", "devpath"):
                f = d / k
                if f.is_file():
                    try:
                        info[k] = f.read_text().strip()
                    except OSError:
                        pass
            info["usb_path"] = d.name
            break
    return info


def serial_links() -> dict[str, list[str]]:
    """realpath → [by-id/by-path 링크]"""
    links: dict[str, list[str]] = {}
    for sub in ("by-id", "by-path"):
        d = Path("/dev/serial") / sub
        if d.is_dir():
            for e in sorted(d.iterdir()):
                links.setdefault(os.path.realpath(e), []).append(str(e))
    return links


def serial_devices() -> list[str]:
    devs = set(serial_links())
    for pat in ("ttyACM*", "ttyUSB*"):
        devs.update(str(p) for p in Path("/dev").glob(pat))
    return sorted(devs)


def port_users(real_dev: str) -> tuple[list[str], int]:
    """/proc/*/fd에서 장치를 연 프로세스를 찾는다(권한상 볼 수 없는 프로세스 수도 반환)."""
    users, hidden = [], 0
    for pid_dir in Path("/proc").iterdir():
        if not pid_dir.name.isdigit() or int(pid_dir.name) == os.getpid():
            continue
        try:
            for fd in (pid_dir / "fd").iterdir():
                try:
                    if os.readlink(fd) == real_dev:
                        comm = (pid_dir / "comm").read_text().strip()
                        users.append(f"pid {pid_dir.name} ({comm})")
                        break
                except OSError:
                    continue
        except PermissionError:
            hidden += 1
        except OSError:
            continue
    return users, hidden


def check_port(rep: Report, role: str, port: str | None) -> str | None:
    """포트 경로를 열지 않고 확인한다. 성공 시 realpath 반환."""
    if not port:
        rep.add(FAIL, f"{role} 포트 미지정", "configs/robot.local.yaml의 port를 지정하세요(scripts/so101 ports로 확인).")
        return None
    if not os.path.lexists(port):
        rep.add(FAIL, f"{role} 포트 경로 없음: {port}", "USB 연결·보드 전원(보드에 따라 필요)·경로 오타를 확인하고 scripts/so101 ports로 다시 찾으세요.")
        return None
    real = os.path.realpath(port)
    try:
        st = os.stat(real)
    except OSError as e:
        rep.add(FAIL, f"{role} 포트 확인 실패: {port}", str(e))
        return None
    if not stat.S_ISCHR(st.st_mode):
        rep.add(FAIL, f"{role} 포트가 문자 장치가 아님: {port} → {real}")
        return None
    if not os.access(real, os.R_OK | os.W_OK):
        gname = grp.getgrgid(st.st_gid).gr_name if st.st_gid is not None else "?"
        user = pwd.getpwuid(os.getuid()).pw_name
        member = user in grp.getgrgid(st.st_gid).gr_mem or pwd.getpwuid(os.getuid()).pw_gid == st.st_gid
        if member and st.st_gid not in os.getgroups():
            how = f"'{gname}' 그룹에는 가입되어 있지만 현재 세션에 반영되지 않았습니다. 로그아웃 후 다시 로그인(SSH 재접속)하세요."
        elif member:
            how = f"'{gname}' 그룹 권한이 있지만 장치 모드({oct(st.st_mode & 0o777)})가 접근을 막습니다."
        else:
            how = f"'{gname}' 그룹에 가입되어 있지 않습니다. 사용자가 직접: sudo usermod -aG {gname} {user}  → 로그아웃 후 다시 로그인."
        rep.add(FAIL, f"{role} 포트 읽기/쓰기 권한 없음: {real}", how)
        return None
    users, hidden = port_users(real)
    if users:
        rep.add(FAIL, f"{role} 포트를 다른 프로세스가 사용 중: {real}", ", ".join(users) + "  → 해당 프로세스를 종료한 뒤 다시 실행하세요.")
        return None
    rep.add(OK, f"{role} 포트 {port} → {real}", f"점유 프로세스 없음(볼 수 없는 프로세스 {hidden}개 제외)")
    return real


# --------------------------------------------------------------------------------------------
# doctor
# --------------------------------------------------------------------------------------------
def doctor_software(rep: Report) -> None:
    lock = read_lock_env()
    print("== 소프트웨어 점검(로봇·포트 설정 불필요, 장치를 열지 않음)")
    # 1) Python·venv·격리
    pyver = platform.python_version()
    if sys.version_info[:2] == tuple(int(x) for x in lock["PYTHON_MINOR"].split(".")):
        rep.add(OK, f"Python {pyver}")
    else:
        rep.add(FAIL, f"Python {pyver} (필요: {lock['PYTHON_MINOR']})", "scripts/install.sh --reset-venv")
    if Path(sys.prefix).resolve() == VENV.resolve():
        rep.add(OK, f"작업공간 venv 사용: {VENV}")
    else:
        rep.add(FAIL, f"venv가 아님: sys.prefix={sys.prefix}", "scripts/so101로 실행하세요.")
    leaked = [k for k in ("PYTHONPATH", "PYTHONHOME", "LD_LIBRARY_PATH") if os.environ.get(k)]
    ros_paths = [p for p in sys.path if "/opt/ros" in p]
    import site

    if leaked or ros_paths or site.ENABLE_USER_SITE:
        rep.add(FAIL, "실행 환경 격리 실패", f"env={leaked}, sys.path ROS={ros_paths}, user site={site.ENABLE_USER_SITE}")
    else:
        rep.add(OK, "ROS·사용자 site-packages 격리", "PYTHONPATH/PYTHONHOME/LD_LIBRARY_PATH 없음, sys.path에 /opt/ros 없음, user site 비활성")

    # 2) lock과 실제 설치 상태(stamp만 믿지 않고 uv로 다시 확인)
    uv = WS / ".tools/uv/uv"
    if uv.is_file():
        r = subprocess.run([str(uv), "--version"], capture_output=True, text=True)
        uvv = r.stdout.split()[1] if r.returncode == 0 else "?"
        rep.add(OK if uvv == lock["UV_VERSION"] else FAIL, f"uv {uvv} (기준 {lock['UV_VERSION']})")
        env = dict(os.environ, UV_PROJECT_ENVIRONMENT=str(VENV), UV_CACHE_DIR=os.environ.get("UV_CACHE_DIR", str(WS / ".cache/uv")),
                   UV_PYTHON_DOWNLOADS="never", UV_OFFLINE="1")
        r = subprocess.run([str(uv), "sync", "--project", str(WS / "lock"), "--locked", "--check", "--python", sys.executable],
                           capture_output=True, text=True, env=env)
        if r.returncode == 0:
            rep.add(OK, "설치 패키지가 lock/uv.lock과 정확히 일치(uv sync --locked --check)")
        else:
            rep.add(FAIL, "설치 패키지가 lock과 다름", (r.stderr or r.stdout).strip()[-600:] + "\n         → scripts/install.sh 재실행")
        r = subprocess.run([str(uv), "pip", "check", "--python", sys.executable], capture_output=True, text=True, env=env)
        rep.add(OK if r.returncode == 0 else FAIL, "의존성 일관성(uv pip check)", "" if r.returncode == 0 else (r.stdout + r.stderr).strip()[-600:])
    else:
        rep.add(FAIL, "uv 고정 바이너리 없음", "scripts/install.sh 실행")
    if STAMP.is_file():
        st = json.loads(STAMP.read_text())
        cur = hashlib.sha256((WS / "lock/uv.lock").read_bytes()).hexdigest()
        if st.get("uv_lock_sha256") == cur and st.get("lerobot_sha") == lock["LEROBOT_SHA"]:
            rep.add(OK, "설치 stamp가 현재 lock·SHA와 일치", f"설치 시각 {st.get('installed_at')}")
        else:
            rep.add(WARN, "설치 stamp가 현재 lock과 다름", "scripts/install.sh 재실행 권장(위 sync 확인이 실제 판정)")
    else:
        rep.add(WARN, "설치 stamp 없음", "scripts/install.sh가 끝까지 완료되지 않았을 수 있습니다.")

    # 3) 패키지 버전·위치·CPU torch
    from importlib import metadata

    def ver(name: str) -> str | None:
        try:
            return metadata.version(name)
        except metadata.PackageNotFoundError:
            return None

    lv = ver("lerobot")
    rep.add(OK if lv == lock["LEROBOT_VERSION"] else FAIL, f"lerobot {lv} (기준 {lock['LEROBOT_VERSION']})")
    tv, tvv = ver("torch"), ver("torchvision")
    if tv and tv.endswith("+cpu") and tvv and tvv.endswith("+cpu"):
        rep.add(OK, f"torch {tv}, torchvision {tvv} (CPU 빌드)")
    else:
        rep.add(FAIL, f"torch {tv}, torchvision {tvv}", "CPU 빌드(+cpu)가 아닙니다.")
    cuda_pkgs = sorted(d.metadata["Name"] for d in metadata.distributions() if re.match(r"^(nvidia-|triton)", d.metadata["Name"] or "", re.I))
    rep.add(OK if not cuda_pkgs else FAIL, "CUDA 전용 패키지 유입 없음" if not cuda_pkgs else f"CUDA 패키지 발견: {cuda_pkgs}")
    for n in ("feetech-servo-sdk", "pyserial", "deepdiff", "draccus"):
        v = ver(n)
        rep.add(OK if v else FAIL, f"{n} {v}")

    # 4) 설치된 lerobot 파일 = 고정 SHA checkout의 src/lerobot (바이트 비교)
    ext = WS / "external/lerobot"
    try:
        head = subprocess.run(["git", "-C", str(ext), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(ext), "status", "--porcelain"], capture_output=True, text=True).stdout.strip()
    except OSError:
        head, dirty = "", ""
    if head == lock["LEROBOT_SHA"] and not dirty:
        rep.add(OK, f"external/lerobot @ {head} (수정 없음)")
        dist = metadata.distribution("lerobot")
        files = [f for f in (dist.files or []) if str(f).startswith("lerobot/")]
        mism, missing = [], []
        for f in files:
            src = ext / "src" / str(f)
            inst = Path(dist.locate_file(f))
            if not src.is_file():
                missing.append(str(f))
            elif hashlib.sha256(src.read_bytes()).digest() != hashlib.sha256(inst.read_bytes()).digest():
                mism.append(str(f))
        if files and not mism and not missing:
            rep.add(OK, f"설치된 lerobot 파일 {len(files)}개가 고정 SHA 소스와 바이트 단위로 일치")
        else:
            rep.add(FAIL, "설치된 lerobot이 고정 SHA 소스와 다름", f"다름 {mism[:5]}, 소스에 없음 {missing[:5]}")
    else:
        rep.add(FAIL, "external/lerobot이 고정 SHA가 아니거나 수정됨", f"HEAD={head or '없음'}, dirty={bool(dirty)} → scripts/install.sh")

    # 5) 공식 모듈 import, CLI
    mods = ["lerobot.scripts.lerobot_teleoperate", "lerobot.scripts.lerobot_calibrate", "lerobot.scripts.lerobot_setup_motors",
            "lerobot.scripts.lerobot_find_port", "lerobot.robots.so_follower", "lerobot.teleoperators.so_leader",
            "lerobot.motors.feetech", "scservo_sdk", "serial"]
    import importlib

    failed = []
    for m in mods:
        try:
            importlib.import_module(m)
        except Exception as e:  # noqa: BLE001
            failed.append(f"{m}: {type(e).__name__}: {e}")
    rep.add(OK if not failed else FAIL, f"공식 모듈 import {len(mods) - len(failed)}/{len(mods)}", "; ".join(failed))
    for cli in OFFICIAL_CLIS:
        if not (VENV_BIN / cli).is_file():
            rep.add(FAIL, f"공식 CLI 없음: {cli}")
    for cli in ("lerobot-teleoperate", "lerobot-calibrate", "lerobot-setup-motors"):
        r = subprocess.run([official_cmd(cli), "--help"], capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=180)
        ok = r.returncode == 0 and ("--robot" in r.stdout or "--teleop" in r.stdout)
        rep.add(OK if ok else FAIL, f"{cli} --help (exit {r.returncode})", "" if ok else (r.stderr or r.stdout)[-400:])
    rep.add(INFO, "lerobot-find-port는 USB 분리를 input()으로 기다리므로 자동 실행하지 않음(진입점 존재만 확인)")

    # 6) 공식 SO101 모터 맵과 래퍼 상수 일치(포트를 열지 않는 객체 생성, 임시 보정 폴더 사용)
    tmp_cal = WS / ".tmp" / "doctor_calibration"
    try:
        for role in ("leader", "follower"):
            obj = official_device(role, "/dev/so101-doctor-nonexistent", "so101-doctor-check", tmp_cal)
            motors = obj.bus.motors
            got = [(n, m.id, m.model) for n, m in motors.items()]
            want = [(n, i + 1, "sts3215") for i, n in enumerate(JOINTS)]
            assert not obj.bus.is_connected
            del obj
            rep.add(OK if got == want else FAIL, f"공식 {ROLE_TYPES[role]} 모터 맵: ID1~6 = {', '.join(n for n, _, _ in got)}",
                    "" if got == want else f"래퍼 상수와 다름: {got}")
    except Exception as e:  # noqa: BLE001
        rep.add(FAIL, "공식 모터 맵 확인 실패", f"{type(e).__name__}: {e}")

    # 7) 공식 CLI 인자 파싱(max_relative_target float/dict, time_s)
    probe_cfg = Config(WS / "-", ArmCfg("leader", "so101_leader", "l", "/dev/x"), ArmCfg("follower", "so101_follower", "f", "/dev/y"),
                       True, None, 60, None, WS / ".tmp")
    for label, mrt in (("float 5", 5.0), ("dict 6관절", {j: 5.0 for j in JOINTS})):
        probe_cfg.max_relative_target = mrt
        ok, msg = official_parse_check(build_teleop_argv(probe_cfg, 30.0))
        rep.add(OK if ok else FAIL, f"공식 teleop 인자 파싱: max_relative_target {label}", msg)

    # 8) 작업공간
    usage = os.statvfs(WS)
    free_gb = usage.f_bavail * usage.f_frsize / 1e9
    rep.add(OK if free_gb > 1 else WARN, f"작업공간 {WS} 여유 {free_gb:.1f} GB")


def doctor_hardware(rep: Report, cfg: Config | None, cfg_errors: list[str]) -> None:
    print("== 하드웨어 전제 점검(포트를 열지 않음. 모터 레지스터 확인은 probe가 담당)")
    if cfg is None or cfg_errors:
        for e in cfg_errors:
            rep.add(FAIL, "설정 오류", e)
        if cfg is None:
            return
    for role in ("leader", "follower"):
        arm = cfg.arm(role)
        if not arm.id:
            rep.add(FAIL, f"{role}.id 미지정", "물리 팔 라벨과 같은 장치 ID를 정하세요(예: so101_leader_a).")
    reals = {}
    for role in ("leader", "follower"):
        r = check_port(rep, role, cfg.arm(role).port)
        if r:
            reals[role] = r
    if len(reals) == 2 and reals["leader"] == reals["follower"]:
        rep.add(FAIL, "leader와 follower가 같은 실제 장치를 가리킴", f"{cfg.leader.port}, {cfg.follower.port} → {reals['leader']}")
    for role, real in reals.items():
        info = usb_info(real)
        if info:
            rep.add(INFO, f"{role} USB {info.get('idVendor')}:{info.get('idProduct')} serial={info.get('serial', '없음')} 경로={info.get('usb_path')}")
    for role in ("leader", "follower"):
        f = calibration_file(cfg, role)
        if f is None:
            continue
        if f.is_file():
            ok, msg = validate_calibration_file(f)
            rep.add(OK if ok else FAIL, f"{role} 보정 파일 {f}", msg)
        else:
            rep.add(WARN, f"{role} 보정 파일 없음: {f}", "teleop 전에 scripts/so101 calibrate " + role + " (또는 같은 팔의 보정 파일 복원)")
    if cfg.max_relative_target is None:
        rep.add(WARN, "follower.max_relative_target 미설정", "제한 없이 시작하면 첫 주기에 리더 위치가 그대로 지령됩니다. 두 팔 자세를 맞춘 뒤 시작하세요.")
    for pkg, svc in (("brltty", None), ("modemmanager", "ModemManager")):
        if Path(f"/var/lib/dpkg/info/{pkg}.list").exists():
            rep.add(INFO, f"{pkg} 설치됨", "실제로 포트가 사라지거나 점유되는 증상이 있을 때만 조치하세요(docs/HARDWARE.md).")


def validate_calibration_file(f: Path) -> tuple[bool, str]:
    """공식 형식(draccus JSON → dict[str, MotorCalibration])으로 읽고 관절·ID를 확인한다."""
    import draccus

    from lerobot.motors import MotorCalibration

    try:
        with open(f) as fh, draccus.config_type("json"):
            cal = draccus.load(dict[str, MotorCalibration], fh)
    except Exception as e:  # noqa: BLE001
        return False, f"공식 형식으로 읽기 실패: {type(e).__name__}: {e}"
    if set(cal) != set(JOINTS):
        return False, f"관절 키 불일치: {sorted(cal)}"
    bad = [j for i, j in enumerate(JOINTS) if cal[j].id != i + 1]
    if bad:
        return False, f"모터 ID 불일치: {bad}"
    return True, "공식 형식, 6개 관절, ID 1~6"


def cmd_doctor(args, cfg, cfg_errors) -> int:
    rep = Report()
    doctor_software(rep)
    if args.scope == "hardware":
        doctor_hardware(rep, cfg, cfg_errors)
    print(f"\n결과: FAIL {rep.n_fail}, WARN {rep.n_warn}  (종료 코드 0=OK, 1=FAIL, 2=WARN만)")
    return rep.exit_code()


# --------------------------------------------------------------------------------------------
# ports
# --------------------------------------------------------------------------------------------
def cmd_ports(args, cfg, cfg_errors) -> int:
    links = serial_links()
    devs = serial_devices()
    if not devs:
        print("USB 시리얼 장치가 없습니다(/dev/serial, /dev/ttyACM*, /dev/ttyUSB*). 보드 USB 연결과 보드 전원 필요 여부를 확인하세요.")
        return 0
    seen_serial: dict[tuple, list[str]] = {}
    for real in devs:
        info = usb_info(real)
        print(f"\n{real}")
        print(f"  USB {info.get('idVendor', '?')}:{info.get('idProduct', '?')}  {info.get('manufacturer', '')} {info.get('product', '')}".rstrip())
        print(f"  serial={info.get('serial', '(없음)')}  USB 경로={info.get('usb_path', '?')}")
        for l in links.get(real, []):
            print(f"  ← {l}")
        if len([l for l in links.get(real, []) if '/by-id/' in l]) == 0:
            print("  (by-id 링크 없음 → by-path 사용)")
        key = (info.get("idVendor"), info.get("idProduct"), info.get("serial"))
        if info.get("serial"):
            seen_serial.setdefault(key, []).append(real)
    dup = {k: v for k, v in seen_serial.items() if len(v) > 1}
    if dup:
        print("\n[WARN] 같은 VID:PID:serial을 가진 장치가 여러 개입니다. by-id가 하나만 남거나 바뀔 수 있으니 by-path를 쓰세요:", dup)
    if cfg:
        print("\n설정(configs/robot.local.yaml) 매핑:")
        for role in ("leader", "follower"):
            p = cfg.arm(role).port
            print(f"  {role}: {p} → {os.path.realpath(p) if p and os.path.lexists(p) else '(없음)'}")
    return 0


# --------------------------------------------------------------------------------------------
# probe (명시적 실기 읽기. 모터에 쓰지 않는다)
# --------------------------------------------------------------------------------------------
PROBE_REGS = ("Present_Position", "Torque_Enable", "Operating_Mode", "Homing_Offset", "Min_Position_Limit",
              "Max_Position_Limit", "Firmware_Major_Version", "Firmware_Minor_Version")


def probe_arm(role: str, port: str, dev_id: str | None, *, bus_factory: Callable | None = None) -> dict:
    """읽기 전용 모터 확인.
    호출하는 공식 API: FeetechMotorsBus(생성), bus.connect(handshake=False)(포트 열기+timeout 설정),
    bus.ping / bus.read / bus.sync_read(읽기), bus.disconnect(disable_torque=False)(포트 닫기만).
    공식 Robot/Teleoperator의 connect/configure/calibrate와 bus.connect(handshake=True)는 쓰지 않는다
    (handshake=True는 읽기뿐이지만 펌웨어가 다르면 예외로 끝나서 관절별 진단을 못 함)."""
    res: dict[str, Any] = {"role": role, "port": port, "id": dev_id, "joints": {}, "errors": []}
    bus = (bus_factory or make_bus)(role, port, dev_id)
    file_cal = dict(bus.calibration or {})
    try:
        try:
            bus.connect(handshake=False)
        except Exception as e:  # noqa: BLE001
            res["errors"].append(f"포트 열기 실패: {type(e).__name__}: {e}")
            res["port_open"] = False
            return res
        res["port_open"] = True
        expected_model = bus.model_number_table["sts3215"]
        for name in JOINTS:
            j: dict[str, Any] = {"id": bus.motors[name].id}
            try:
                j["model"] = bus.ping(name, num_retry=1, raise_on_error=True)
            except Exception as e:  # noqa: BLE001
                j["ping_error"] = str(e).strip()
                res["joints"][name] = j
                continue
            for reg in PROBE_REGS:
                try:
                    j[reg] = bus.read(reg, name, normalize=False, num_retry=1)
                except Exception as e:  # noqa: BLE001
                    j.setdefault("read_errors", {})[reg] = str(e).strip()[:120]
            j["model_ok"] = j["model"] == expected_model
            res["joints"][name] = j
        # 보정 파일 대 모터 레지스터(공식 is_calibrated가 비교하는 필드: homing_offset, range_min, range_max)
        cmp: dict[str, Any] = {}
        for name in JOINTS:
            j = res["joints"][name]
            if name not in file_cal or "ping_error" in j or j.get("read_errors"):
                continue
            c = file_cal[name]
            diffs = {k: (getattr(c, a), j[r]) for k, a, r in (("homing_offset", "homing_offset", "Homing_Offset"),
                                                               ("range_min", "range_min", "Min_Position_Limit"),
                                                               ("range_max", "range_max", "Max_Position_Limit")) if getattr(c, a) != j[r]}
            if c.id != j["id"]:
                diffs["id"] = (c.id, j["id"])
            cmp[name] = diffs
        res["calib_compare"] = cmp
        all_ok = all(res["joints"][n].get("model_ok") and not res["joints"][n].get("read_errors") for n in JOINTS)
        res["comm_ok"] = all_ok
        fw = {n: f"{res['joints'][n].get('Firmware_Major_Version')}.{res['joints'][n].get('Firmware_Minor_Version')}"
              for n in JOINTS if "ping_error" not in res["joints"][n]}
        res["firmware"] = fw
        res["firmware_same"] = len(set(fw.values())) <= 1
        if not file_cal:
            res["calib_status"] = "missing"
        elif all_ok and len(cmp) == 6:
            res["calib_status"] = "match" if not any(cmp.values()) else "mismatch"
        else:
            res["calib_status"] = "unknown"
        if all_ok and res["calib_status"] == "match":
            try:
                res["normalized"] = bus.sync_read("Present_Position", normalize=True, num_retry=1)
            except Exception as e:  # noqa: BLE001
                res["errors"].append(f"정규화 위치 sync_read 실패: {e}")
        return res
    finally:
        if bus.is_connected:
            bus.disconnect(disable_torque=False)  # 포트만 닫는다(토크 쓰기 없음)


def print_probe(res: dict) -> None:
    print(f"\n== probe {res['role']} ({res['id']}) {res['port']}")
    for e in res["errors"]:
        print(f"  [FAIL] {e}")
    if not res.get("port_open"):
        return
    print(f"  {'관절':<14}{'ID':>3} {'모델':>6} {'펌웨어':>7} {'위치(raw)':>9} {'토크':>4} {'모드':>4} {'offset':>7} {'min':>5} {'max':>5}  보정파일")
    for name in JOINTS:
        j = res["joints"][name]
        if "ping_error" in j:
            print(f"  {name:<14}{j['id']:>3}  응답 없음/오류: {j['ping_error'][:90]}")
            continue
        g = lambda r: j.get(r, "ERR")  # noqa: E731
        cmpd = res.get("calib_compare", {}).get(name)
        cs = "-" if cmpd is None else ("일치" if not cmpd else "불일치 " + ", ".join(f"{k}:파일 {a}≠모터 {b}" for k, (a, b) in cmpd.items()))
        mark = "" if j.get("model_ok") else "  ←모델 번호 다름"
        print(f"  {name:<14}{j['id']:>3} {g('model') if 'model' in j else '?':>6} {g('Firmware_Major_Version')}.{g('Firmware_Minor_Version'):<5}"
              f" {g('Present_Position'):>9} {g('Torque_Enable'):>4} {g('Operating_Mode'):>4} {g('Homing_Offset'):>7}"
              f" {g('Min_Position_Limit'):>5} {g('Max_Position_Limit'):>5}  {cs}{mark}")
        if j.get("read_errors"):
            print(f"      읽기 오류: {j['read_errors']}")
    n_ok = sum(1 for n in JOINTS if res["joints"][n].get("model_ok"))
    print(f"  응답·모델 일치: {n_ok}/6")
    if not res.get("firmware_same"):
        print("  [WARN] 펌웨어 버전이 서로 다릅니다:", res.get("firmware"))
        print("         공식 connect의 handshake(_assert_same_firmware)가 이 조건에서 실패하므로 공식 calibrate/teleop이 연결되지 않습니다.")
        print("         펌웨어 갱신은 Feetech 공식 도구로 사용자가 판단해 진행하세요. 이 래퍼는 갱신·초기화를 하지 않습니다.")
    if n_ok < 6:
        print("  [FAIL] 응답하지 않거나 모델이 다른 모터가 있습니다. 점검 순서: 전원 → 케이블(보드↔1번, 체인) → 보드 모드 → 포트 →")
        print("         baud(`probe --scan`). 그다음에도 ID 누락/충돌이면 공식 setup-motors(모터 하나씩 연결)를 검토하세요.")
    print(f"  보정 파일 상태: {res.get('calib_status')}  (match=공식 is_calibrated와 같은 필드 일치)")
    if res.get("normalized"):
        print("  현재 위치(정규화, 몸체=도, gripper=0~100):", {k: round(v, 1) for k, v in res["normalized"].items()})
    print("  참고: 정상 응답은 '해당 ID가 응답했다'는 뜻이며, 중복 ID 부재·관절 물리 배치·6개 실물 존재를 완전히 증명하지 않습니다.")


def cmd_probe(args, cfg, cfg_errors) -> int:
    if cfg is None or cfg_errors:
        for e in cfg_errors:
            print("[FAIL] 설정:", e)
        return 1
    roles = ["leader", "follower"] if args.role == "both" else [args.role]
    rc = 0
    for role in roles:
        rep = Report()
        arm = cfg.arm(role)
        if not arm.id:
            rep.add(FAIL, f"{role}.id 미지정")
        real = check_port(rep, role, arm.port)
        if rep.n_fail:
            rc = 1
            continue
        if args.scan:
            from lerobot.motors.feetech import FeetechMotorsBus

            print(f"[{role}] 공식 FeetechMotorsBus.scan_port: 호스트 포트 baud를 바꿔 가며 broadcast ping(모터 EEPROM baud 쓰기 아님)")
            print(FeetechMotorsBus.scan_port(real))
            continue
        res = probe_arm(role, arm.port, arm.id)
        print_probe(res)
        append_run_log({"ts": now_iso(), "host": socket.gethostname(), "cmd": "probe", "role": role, "port": arm.port, "id": arm.id,
                        "comm_ok": res.get("comm_ok"), "firmware": res.get("firmware"), "calib_status": res.get("calib_status"),
                        "errors": res["errors"]})
        if not res.get("comm_ok"):
            rc = 1
    return rc


# --------------------------------------------------------------------------------------------
# 공식 프로세스 실행(신호 전달·출력 중계·기록)
# --------------------------------------------------------------------------------------------
LOOP_RE = re.compile(rb"Teleop loop time: ([0-9.]+)ms")


def _child_preexec(parent_pid: int) -> None:
    # 터미널 hangup(SSH 끊김)에서 자식이 정리 없이 즉시 죽지 않게 SIGHUP을 무시하고, 부모가 SIGINT를 전달한다.
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    # 부모(래퍼)가 비정상 종료하면 자식에게 SIGINT를 보내 고아로 계속 구동되지 않게 한다(Linux prctl).
    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        libc.prctl(1, signal.SIGINT)  # PR_SET_PDEATHSIG
    except OSError:
        pass
    if os.getppid() != parent_pid:
        os.kill(os.getpid(), signal.SIGINT)


def run_official(argv: list[str], *, env: dict[str, str], log_name: str, parse_loop: bool = False,
                 after_exit: Callable[[], dict] | None = None, interrupt_grace: float = INTERRUPT_GRACE_S,
                 term_grace: float = TERM_GRACE_S, extra: dict | None = None) -> dict:
    """공식 CLI를 자식으로 실행한다.
    - stdin은 터미널을 그대로 물려준다(공식 input() 프롬프트 유지). 같은 프로세스 그룹이라 Ctrl+C는 자식이 직접 받는다.
      부모는 SIGINT를 전달하지 않는다(중복 SIGINT 방지).
    - SIGTERM/SIGHUP을 받으면 자식에게 SIGINT를 한 번 전달하고, 한도 시간 뒤 SIGTERM → SIGKILL로 올린다.
    - stdout/stderr는 부모가 터미널과 logs/<log_name>.log로 중계한다. 추종 루프 출력으로 실제 루프 구간과 Hz를 기록한다.
    - 자식 종료 후 after_exit(예: 양팔 토크 해제 재시도)를 실행하고 결과를 함께 기록한다."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    raw_path = LOG_DIR / f"{log_name}.log"
    rec: dict[str, Any] = {"ts_start": now_iso(), "host": socket.gethostname(), "argv": argv, "log": str(raw_path.relative_to(WS)),
                           **(extra or {})}
    t0 = time.time()
    state = {"interrupt_at": None, "forwarded": None, "tty_ok": True, "escalated": []}
    loop = {"first": None, "last": None, "n": 0, "ms": []}
    parent_pid = os.getpid()
    proc = subprocess.Popen(argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0,
                            preexec_fn=lambda: _child_preexec(parent_pid))

    def on_sigint(signum, frame):  # 터미널 Ctrl+C: 자식도 직접 받았으므로 기록만
        if state["interrupt_at"] is None:
            state["interrupt_at"] = time.time()

    def on_forward(signum, frame):
        if signum == signal.SIGHUP:
            state["tty_ok"] = False
        if state["forwarded"] is None and proc.poll() is None:
            state["forwarded"] = signal.Signals(signum).name
            state["interrupt_at"] = state["interrupt_at"] or time.time()
            try:
                proc.send_signal(signal.SIGINT)
            except ProcessLookupError:
                pass

    old = {s: signal.signal(s, h) for s, h in ((signal.SIGINT, on_sigint), (signal.SIGTERM, on_forward), (signal.SIGHUP, on_forward))}
    fd = proc.stdout.fileno()
    buf = b""
    try:
        with open(raw_path, "ab") as raw:
            raw.write(f"\n===== {rec['ts_start']} {' '.join(argv)}\n".encode())
            eof = False
            while True:
                if not eof:
                    r, _, _ = select.select([fd], [], [], 0.5)
                    if r:
                        chunk = os.read(fd, 65536)
                        if not chunk:
                            eof = True
                        else:
                            raw.write(chunk)
                            raw.flush()
                            if state["tty_ok"]:
                                try:
                                    sys.stdout.buffer.write(chunk)
                                    sys.stdout.buffer.flush()
                                except OSError:
                                    state["tty_ok"] = False
                            if parse_loop:
                                buf += chunk
                                *lines, buf = buf.split(b"\n")
                                for ln in lines:
                                    m = LOOP_RE.search(ln)
                                    if m:
                                        tnow = time.time()
                                        loop["first"] = loop["first"] or tnow
                                        loop["last"] = tnow
                                        loop["n"] += 1
                                        loop["ms"].append(float(m.group(1)))
                if eof and proc.poll() is not None:
                    break
                if proc.poll() is not None and eof:
                    break
                ia = state["interrupt_at"]
                if ia is not None and proc.poll() is None:
                    el = time.time() - ia
                    if el > interrupt_grace + term_grace and "SIGKILL" not in state["escalated"]:
                        proc.kill()
                        state["escalated"].append("SIGKILL")
                    elif el > interrupt_grace and "SIGTERM" not in state["escalated"]:
                        proc.terminate()
                        state["escalated"].append("SIGTERM")
                if eof:
                    try:
                        proc.wait(timeout=0.5)
                    except subprocess.TimeoutExpired:
                        pass
    finally:
        if proc.poll() is None:  # 래퍼 내부 예외 등으로 빠져나온 경우에도 자식을 남기지 않는다
            proc.send_signal(signal.SIGINT)
            try:
                proc.wait(timeout=interrupt_grace)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        for s, h in old.items():
            signal.signal(s, h)
    rc = proc.returncode
    rec.update({"ts_end": now_iso(), "duration_s": round(time.time() - t0, 2), "exit_code": rc,
                "exit_signal": signal.Signals(-rc).name if rc is not None and rc < 0 else None,
                "user_interrupt": state["interrupt_at"] is not None and state["forwarded"] is None,
                "forwarded_signal": state["forwarded"], "escalated": state["escalated"]})
    if parse_loop:
        ms = sorted(loop["ms"])
        rec["teleop_loop"] = {
            "loop_lines": loop["n"],
            "first_loop_at": time.strftime("%H:%M:%S", time.localtime(loop["first"])) if loop["first"] else None,
            "last_loop_at": time.strftime("%H:%M:%S", time.localtime(loop["last"])) if loop["last"] else None,
            "loop_span_s": round(loop["last"] - loop["first"], 2) if loop["first"] else 0.0,
            "loop_ms_median": ms[len(ms) // 2] if ms else None,
            "loop_ms_p95": ms[int(len(ms) * 0.95)] if ms else None,
            "loop_ms_max": ms[-1] if ms else None,
            "hz_median": round(1000 / ms[len(ms) // 2], 1) if ms and ms[len(ms) // 2] > 0 else None,
        }
    if after_exit is not None:
        rec["cleanup"] = after_exit()
    append_run_log(rec)
    return rec


# --------------------------------------------------------------------------------------------
# 추종 종료 후 독립 정리(양팔 토크 해제 재시도)
# --------------------------------------------------------------------------------------------
def disable_torque_one(role: str, port: str, dev_id: str | None, bus_factory: Callable | None = None) -> dict:
    """공식 bus API만 사용: connect(handshake=False) → set_timeout → 모터별 disable_torque(Torque_Enable=0, Lock=0)
    → disconnect(disable_torque=False). Robot/Teleoperator connect/configure·목표 위치 쓰기는 하지 않는다."""
    bus = (bus_factory or make_bus)(role, port, dev_id)
    out: dict[str, Any] = {"motors": {}}
    try:
        bus.connect(handshake=False)
        bus.set_timeout(CLEANUP_PACKET_TIMEOUT_MS)
        for name in JOINTS:
            try:
                bus.disable_torque(name, num_retry=2)
                out["motors"][name] = "ok"
            except Exception as e:  # noqa: BLE001
                out["motors"][name] = f"{type(e).__name__}: {str(e).strip()[:100]}"
    except Exception as e:  # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {str(e).strip()[:200]}"
    finally:
        try:
            if bus.is_connected:
                bus.disconnect(disable_torque=False)
        except Exception as e:  # noqa: BLE001
            out["close_error"] = str(e)[:100]
    ok = "error" not in out and all(v == "ok" for v in out["motors"].values()) and len(out["motors"]) == len(JOINTS)
    out["status"] = "성공" if ok else "실패"
    return out


def cleanup_arms(arms: list[ArmCfg], *, timeout_s: float = CLEANUP_TIMEOUT_S, one: Callable | None = None) -> dict:
    """팔마다 별도 스레드로 독립 시도한다. 한쪽 실패·지연이 다른 쪽을 막지 않으며, 한도 시간을 넘기면 '확인 불가'."""
    one = one or disable_torque_one
    results: dict[str, dict] = {}

    def worker(a: ArmCfg) -> None:
        try:
            results[a.role] = one(a.role, a.port, a.id)
        except BaseException as e:  # noqa: BLE001
            results[a.role] = {"status": "실패", "error": f"{type(e).__name__}: {e}"}

    threads = [threading.Thread(target=worker, args=(a,), daemon=True) for a in arms]
    deadline = time.time() + timeout_s
    for t in threads:
        t.start()
    for t in threads:
        t.join(max(0.0, deadline - time.time()))
    for a in arms:
        results.setdefault(a.role, {"status": "확인 불가", "error": f"{timeout_s}s 안에 끝나지 않음"})
    return dict(results)


# --------------------------------------------------------------------------------------------
# 공식 명령 래핑 서브커맨드
# --------------------------------------------------------------------------------------------
def child_env(cfg: Config | None) -> dict[str, str]:
    env = dict(os.environ)
    env["HF_LEROBOT_CALIBRATION"] = str(cfg.calibration_root if cfg else WS / "calibration")
    env["PYTHONUNBUFFERED"] = "1"
    for k in ("PYTHONPATH", "PYTHONHOME", "LD_LIBRARY_PATH"):
        env.pop(k, None)
    return env


def print_cmd(argv: list[str], env: dict[str, str]) -> None:
    import shlex

    print("공식 명령:")
    print("  " + " \\\n    ".join(shlex.quote(a) for a in argv))
    print("실행 환경(래퍼가 설정하는 값만): HF_LEROBOT_CALIBRATION=" + env["HF_LEROBOT_CALIBRATION"]
          + ", PYTHONUNBUFFERED=1, PYTHONNOUSERSITE=1, PYTHONPATH/PYTHONHOME/LD_LIBRARY_PATH 제거")


def require_cfg(cfg, cfg_errors) -> bool:
    if cfg is None or cfg_errors:
        for e in cfg_errors:
            print("[FAIL] 설정:", e)
        return False
    return True


def stamp_name(kind: str) -> str:
    return f"{kind}-{time.strftime('%Y%m%d_%H%M%S')}"


def cmd_find_port(args, cfg, cfg_errors) -> int:
    print("공식 lerobot-find-port를 실행합니다. 안내가 나오면 해당 보드의 USB만 뽑고 Enter를 누르세요.")
    print("(서보 전원을 넣기 전에 USB가 보이는 보드라면 전원 투입 전에 하는 것이 안전합니다.)")
    rec = run_official([official_cmd("lerobot-find-port")], env=child_env(cfg), log_name=stamp_name("find-port"))
    return rec["exit_code"] or 0


def cmd_setup_motors(args, cfg, cfg_errors) -> int:
    if not require_cfg(cfg, cfg_errors):
        return 1
    argv = build_device_argv("lerobot-setup-motors", cfg, args.role, placeholder=args.dry_run)
    env = child_env(cfg)
    if args.dry_run:
        print_cmd(argv, env)
        return 0
    rep = Report()
    if not cfg.arm(args.role).id:
        rep.add(FAIL, f"{args.role}.id 미지정")
    check_port(rep, args.role, cfg.arm(args.role).port)
    if rep.n_fail:
        return 1
    print("공식 setup-motors: gripper부터 안내되는 모터 *하나만* 보드에 연결하고 Enter. ID와 baud를 그 모터 EEPROM에 씁니다.")
    print("probe에서 ID 누락·충돌이 확인된 경우에만 실행하세요. 정상 응답한 조립 팔에는 필요 없습니다.")
    print("케이블 재배선 시 전원 처리는 보드/제조사 안내를 따르세요.")
    if input("계속하려면 yes 입력: ").strip() != "yes":
        print("취소")
        return 1
    rec = run_official(argv, env=env, log_name=stamp_name(f"setup-motors-{args.role}"))
    return rec["exit_code"] or 0


def cmd_calibrate(args, cfg, cfg_errors) -> int:
    if not require_cfg(cfg, cfg_errors):
        return 1
    argv = build_device_argv("lerobot-calibrate", cfg, args.role, placeholder=args.dry_run)
    env = child_env(cfg)
    if args.dry_run:
        print_cmd(argv, env)
        return 0
    rep = Report()
    if not cfg.arm(args.role).id:
        rep.add(FAIL, f"{args.role}.id 미지정")
    check_port(rep, args.role, cfg.arm(args.role).port)
    if rep.n_fail:
        return 1
    f = calibration_file(cfg, args.role)
    print(f"보정 파일 위치: {f}" + (" (이미 있음: 공식 프롬프트에서 Enter=파일을 모터에 기록, c=다시 보정)" if f and f.is_file() else " (새로 생성)"))
    if args.role == "follower":
        print("주의: 공식 calibrate는 연결 시 팔로워 설정을 쓰고 토크를 켠 뒤, 재보정에서 토크를 끕니다. 팔이 처질 수 있으니 손으로 받치세요.")
    print("공식 순서: 모든 관절을 가동 범위 가운데에 두고 Enter → wrist_roll을 제외한 관절을 끝에서 끝까지 움직임 → Enter.")
    rec = run_official(argv, env=env, log_name=stamp_name(f"calibrate-{args.role}"))
    if rec["exit_code"] == 0 and f and f.is_file():
        ok, msg = validate_calibration_file(f)
        print(f"[{'OK' if ok else 'FAIL'}] 보정 파일 {f}: {msg}")
        print("백업 예: tar czf <백업폴더>/so101_calibration_$(date +%Y%m%d).tar.gz -C <작업공간> calibration  (docs/HARDWARE.md)")
    return rec["exit_code"] or 0


CHECKLIST = (
    "역할 라벨 확인: leader/follower 보드·팔이 설정 파일의 port/id와 같다",
    "팔로워가 책상에 고정되어 있고 작업 반경에 사람·물체가 없다",
    "팔로워 전원 차단 수단(어댑터 스위치/DC 잭)이 손 닿는 곳에 있다",
    "종료 시 팔로워를 받칠 수 있다(토크 해제로 처질 수 있음, time_s 자동 종료 포함)",
    "리더를 팔로워 현재 자세 가까이 맞췄다(아래 자세 차이 표 확인)",
)


def cmd_teleop(args, cfg, cfg_errors) -> int:
    if not require_cfg(cfg, cfg_errors):
        return 1
    time_s = args.time_s if args.time_s is not None else cfg.time_s
    env = child_env(cfg)
    if args.dry_run:
        argv = build_teleop_argv(cfg, time_s, placeholder=True)
        print_cmd(argv, env)
        if cfg.leader.port and cfg.follower.port and cfg.leader.id and cfg.follower.id:
            ok, msg = official_parse_check(argv)
            print(f"[{'OK' if ok else 'FAIL'}] 공식 TeleoperateConfig 파싱: {msg}")
            return 0 if ok else 1
        print("[INFO] port/id 미지정 항목이 있어 공식 파싱 확인은 생략했습니다(실행 시에는 거부됨).")
        return 0

    rep = Report()
    print("== 추종 사전 점검")
    for role in ("leader", "follower"):
        if not cfg.arm(role).id:
            rep.add(FAIL, f"{role}.id 미지정")
    reals = {r: check_port(rep, r, cfg.arm(r).port) for r in ("leader", "follower")}
    if reals["leader"] and reals["leader"] == reals["follower"]:
        rep.add(FAIL, "leader와 follower가 같은 실제 장치", str(reals["leader"]))
    for role in ("leader", "follower"):
        f = calibration_file(cfg, role)
        if f and not f.is_file():
            rep.add(FAIL, f"{role} 보정 파일 없음: {f}",
                    f"공식 teleop은 이 경우 대화형 보정을 자동 시작합니다. 먼저 scripts/so101 calibrate {role} 또는 같은 팔의 보정 파일을 복원하세요.")
        elif f:
            ok, msg = validate_calibration_file(f)
            if not ok:
                rep.add(FAIL, f"{role} 보정 파일 형식 오류", msg)
    argv = build_teleop_argv(cfg, time_s, placeholder=True)
    ok, msg = official_parse_check(argv)
    rep.add(OK if ok else FAIL, "공식 teleop 인자 파싱", msg)
    if rep.n_fail:
        print("사전 점검 실패로 추종을 시작하지 않습니다.")
        return 1
    probes = {}
    for role in ("leader", "follower"):
        res = probe_arm(role, cfg.arm(role).port, cfg.arm(role).id)
        print_probe(res)
        probes[role] = res
        if not res.get("comm_ok"):
            rep.add(FAIL, f"{role} 모터 6개 응답·모델 확인 실패")
        elif not res.get("firmware_same"):
            rep.add(FAIL, f"{role} 펌웨어 버전 불일치(공식 handshake 실패 조건)", str(res.get("firmware")))
        elif res.get("calib_status") != "match":
            rep.add(FAIL, f"{role} 보정 파일과 모터 레지스터 불일치({res.get('calib_status')})",
                    f"공식 teleop이 Enter 프롬프트로 파일을 모터에 쓰는 경로를 피합니다. scripts/so101 calibrate {role}로 처리하세요.")
    if rep.n_fail:
        print("사전 점검 실패로 추종을 시작하지 않습니다.")
        return 1
    ln, fn = probes["leader"].get("normalized") or {}, probes["follower"].get("normalized") or {}
    print("\n== 시작 자세 차이(리더 - 팔로워, 몸체=도, gripper=0~100). 자동 판정 없음: 사람이 보고 판단")
    for j in JOINTS:
        if j in ln and j in fn:
            print(f"  {j:<14} 리더 {ln[j]:8.1f}  팔로워 {fn[j]:8.1f}  차이 {ln[j] - fn[j]:+8.1f}")
    print(f"\n이동량 제한(max_relative_target): {cfg.max_relative_target!r}"
          " — 매 주기 '현재 위치 기준 목표 오프셋'만 제한합니다. 속도·충돌 안전을 보장하지 않습니다.")
    print(f"시간 상한(teleop_time_s): {time_s!r} — 상한에 도달해도 공식 코드가 토크를 해제하므로 팔이 처질 수 있습니다.")
    print("\n현장 확인:")
    for i, c in enumerate(CHECKLIST, 1):
        print(f"  {i}. {c}")
    if args.onsite_confirmed:
        print("(--onsite-confirmed: 현장 확인 완료로 기록)")
    else:
        if not sys.stdin.isatty():
            print("터미널 입력이 아니므로 현장 확인을 받을 수 없습니다. 로봇 옆 터미널에서 실행하세요.")
            return 1
        if input("모두 확인했으면 yes 입력: ").strip() != "yes":
            print("취소")
            return 1
    print("\n종료: 리더를 쉬는 자세로 천천히 옮기고 → 팔로워를 받치고 → Ctrl+C. 응답이 없으면 팔로워 전원을 차단하세요.\n")
    argv = build_teleop_argv(cfg, time_s)
    arms = [cfg.leader, cfg.follower]
    pose = {j: round(ln[j] - fn[j], 2) for j in JOINTS if j in ln and j in fn}
    rec = run_official(argv, env=env, log_name=stamp_name("teleop"), parse_loop=True, after_exit=lambda: cleanup_arms(arms),
                       extra={"cmd": "teleop", "onsite_confirmed_flag": bool(args.onsite_confirmed),
                              "start_pose_diff_leader_minus_follower": pose,
                              "firmware": {r: probes[r].get("firmware") for r in probes}})
    print("\n== 종료 요약")
    print(f"  공식 프로세스 exit={rec['exit_code']} signal={rec['exit_signal']} 전체 {rec['duration_s']}s")
    tl = rec.get("teleop_loop", {})
    print(f"  추종 루프 출력 {tl.get('loop_lines')}줄, 첫/마지막 {tl.get('first_loop_at')}~{tl.get('last_loop_at')}"
          f" (루프 구간 {tl.get('loop_span_s')}s), 중앙값 {tl.get('loop_ms_median')}ms ≈ {tl.get('hz_median')}Hz")
    clean = rec.get("cleanup", {})
    for role in ("leader", "follower"):
        c = clean.get(role, {})
        print(f"  {role} 토크 해제 재시도: {c.get('status')}  {c.get('error', '')}")
    print(f"  기록: {RUNS_LOG.relative_to(WS)}, {rec['log']}")
    if rec["exit_code"] not in (0, None):
        return rec["exit_code"] if rec["exit_code"] > 0 else 128 - rec["exit_code"]
    if any(c.get("status") != "성공" for c in clean.values()):
        print("  [FAIL] 토크 해제 재시도가 성공하지 못한 팔이 있습니다. 팔을 받치고 전원 상태를 확인하세요.")
        return 3
    return 0


# --------------------------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="scripts/so101", description="SO-101 공식 LeRobot 래퍼")
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="설정 파일(기본 configs/robot.local.yaml)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("doctor", help="비구동 점검. software=로봇 불필요, hardware=포트·권한·설정·보정 파일(포트를 열지 않음)")
    p.add_argument("--scope", choices=("software", "hardware"), default="software")
    sub.add_parser("ports", help="USB 시리얼 장치·by-id/by-path·식별정보 목록(읽기 전용)")
    sub.add_parser("find-port", help="공식 lerobot-find-port 실행(USB 분리 방식)")
    p = sub.add_parser("probe", help="모터 읽기 전용 확인(ID·모델·펌웨어·위치·토크·보정 레지스터)")
    p.add_argument("role", choices=("leader", "follower", "both"))
    p.add_argument("--scan", action="store_true", help="공식 scan_port로 모든 baud에서 broadcast ping(명시적으로만)")
    for name, helptext in (("setup-motors", "공식 lerobot-setup-motors(ID·baud 쓰기, 필요할 때만)"), ("calibrate", "공식 lerobot-calibrate")):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("role", choices=("leader", "follower"))
        p.add_argument("--dry-run", action="store_true", help="공식 명령만 표시(포트·보정 파일 확인 생략, 장치 열지 않음)")
    p = sub.add_parser("teleop", help="공식 lerobot-teleoperate(카메라 없음) + 사전 점검 + 종료 후 양팔 토크 해제 재시도")
    p.add_argument("--time-s", type=float, default=None, help="공식 --teleop_time_s(초). 설정 파일 값보다 우선")
    p.add_argument("--dry-run", action="store_true", help="공식 명령과 인자 파싱만 확인(장치 열지 않음, 로봇 객체 생성 없음)")
    p.add_argument("--onsite-confirmed", action="store_true", help="현장 체크리스트를 이미 확인했음(프롬프트 생략, 기록됨)")
    args = ap.parse_args(argv)

    cfg, cfg_errors = (None, [])
    if args.cmd != "doctor" or args.scope == "hardware" or args.config != DEFAULT_CONFIG or DEFAULT_CONFIG.is_file():
        cfg, cfg_errors = load_config(args.config)
    if args.cmd == "doctor" and args.scope == "software":
        cfg_errors = []  # software 점검은 로컬 설정과 무관
    apply_calibration_env(cfg)
    if args.cmd == "teleop" and args.time_s is not None and args.time_s <= 0:
        print("--time-s는 양수여야 합니다.")
        return 2
    handler = {"doctor": cmd_doctor, "ports": cmd_ports, "find-port": cmd_find_port, "probe": cmd_probe,
               "setup-motors": cmd_setup_motors, "calibrate": cmd_calibrate, "teleop": cmd_teleop}[args.cmd]
    return handler(args, cfg, cfg_errors)


if __name__ == "__main__":
    sys.exit(main())
