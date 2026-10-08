#!/usr/bin/env python3
"""하드웨어 없이 래퍼의 안전 관련 동작을 검증한다(검증 전용 도구, 운영 CLI에 노출하지 않음).

실행:  .venv/bin/python tools/verify_offline.py        (ROS 변수는 스스로 제거하고 다시 실행한다)

가짜 장치: pty 위에서 Feetech protocol 0(PING/READ/WRITE/SYNC_READ/SYNC_WRITE 등)에 응답하는 가짜 서보 버스.
공식 FeetechMotorsBus와 scservo_sdk가 실제 pyserial 경로로 이 pty를 연다. 가짜 장치가 받은 모든 쓰기 계열 명령
(WRITE, REG_WRITE, ACTION, RESET, SYNC_WRITE 등)을 *선로 수준*에서 기록하므로, 코드에 write 문자열이 있는지와
무관하게 실제로 모터 쓰기가 전송됐는지를 판정한다. 실제 /dev/tty* 장치는 열지 않는다.

검사 항목은 아래 TESTS 목록과 같다. 결과는 표준출력과 .tmp/verify/result.json에 남는다.
"""
from __future__ import annotations

import json
import os
import pty
import signal
import subprocess
import sys
import tempfile
import threading
import time
import tty
from pathlib import Path

WS = Path(__file__).resolve().parent.parent

# ---- 실행 환경 격리: ROS 경로가 있으면 지운 환경으로 다시 실행 ----
if any(os.environ.get(k) for k in ("PYTHONPATH", "PYTHONHOME", "LD_LIBRARY_PATH")) or os.environ.get("PYTHONNOUSERSITE") != "1":
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME", "LD_LIBRARY_PATH")}
    env.update(PYTHONNOUSERSITE="1", SO101_WS=str(WS), HF_HOME=str(WS / ".cache/huggingface"), HF_HUB_OFFLINE="1",
               TMPDIR=str(WS / ".tmp"))
    os.execve(sys.executable, [sys.executable, *sys.argv], env)

sys.path.insert(0, str(WS / "scripts"))
VERIFY_DIR = WS / ".tmp" / "verify"

INST_PING, INST_READ, INST_WRITE, INST_REG_WRITE, INST_ACTION, INST_RESET = 1, 2, 3, 4, 5, 6
INST_SYNC_READ, INST_SYNC_WRITE = 0x82, 0x83
WRITE_INSTS = {INST_WRITE: "WRITE", INST_REG_WRITE: "REG_WRITE", INST_ACTION: "ACTION", INST_RESET: "RESET",
               INST_SYNC_WRITE: "SYNC_WRITE", 0x08: "REBOOT", 0x0A: "RECOVERY"}


# ============================================================================================
# 가짜 Feetech 서보 버스(pty)
# ============================================================================================
class FakeServoBus:
    def __init__(self, motors: dict[int, dict] | None, *, silent_ids: set[int] = frozenset()):
        """motors: id -> {"fw": (major, minor), "pos": int, "offset": int, "min": int, "max": int, "model": int}
        motors=None이면 아무 응답도 하지 않는 버스(장치 무응답)."""
        self.master, slave = pty.openpty()
        tty.setraw(self.master)
        self.slave_path = os.ttyname(slave)
        os.close(slave)  # 테스트 대상이 경로로 연다
        self.silent = set(silent_ids)
        self.writes: list[tuple[str, int, list[int]]] = []  # (inst, id, params)
        self.instructions: list[tuple[int, int]] = []
        self.regs: dict[int, bytearray] = {}
        for mid, m in (motors or {}).items():
            r = bytearray(256)
            r[0], r[1] = m.get("fw", (3, 10))
            self._put(r, 3, m.get("model", 777), 2)
            r[5] = mid
            r[6] = 0  # baud index (1M)
            self._put(r, 9, m.get("min", 1000), 2)
            self._put(r, 11, m.get("max", 3000), 2)
            off = m.get("offset", 0)
            self._put(r, 31, (-off | (1 << 11)) if off < 0 else off, 2)
            r[33] = 0
            r[40] = m.get("torque", 0)
            r[55] = 0
            self._put(r, 56, m.get("pos", 2048), 2)
            self.regs[mid] = r
        self._stop = False
        self.th = threading.Thread(target=self._run, daemon=True)
        self.th.start()

    @staticmethod
    def _put(r: bytearray, addr: int, val: int, n: int) -> None:
        for i in range(n):
            r[addr + i] = (val >> (8 * i)) & 0xFF

    def _status(self, mid: int, params: list[int]) -> bytes:
        body = [mid, len(params) + 2, 0, *params]
        return bytes([0xFF, 0xFF, *body, (~sum(body)) & 0xFF])

    def _run(self) -> None:
        buf = b""
        while not self._stop:
            try:
                chunk = os.read(self.master, 1024)
            except OSError as e:
                if e.errno == 5 and not self._stop:  # EIO: 아직 아무도 slave를 열지 않음 → 기다림
                    time.sleep(0.005)
                    continue
                return
            if not chunk:
                return
            buf += chunk
            while True:
                i = buf.find(b"\xff\xff")
                if i < 0 or len(buf) < i + 4:
                    break
                ln = buf[i + 3]
                if len(buf) < i + 4 + ln:
                    break
                pkt = buf[i:i + 4 + ln]
                buf = buf[i + 4 + ln:]
                self._handle(pkt[2], pkt[4], list(pkt[5:-1]))

    def _reply(self, data: bytes) -> None:
        try:
            os.write(self.master, data)
        except OSError:
            pass

    def _handle(self, mid: int, inst: int, params: list[int]) -> None:
        self.instructions.append((inst, mid))
        if inst in WRITE_INSTS:
            self.writes.append((WRITE_INSTS[inst], mid, params))
        targets = list(self.regs) if mid == 0xFE else [mid]
        if inst == INST_SYNC_READ:
            addr, n, ids = params[0], params[1], params[2:]
            out = b""
            for t in ids:
                if t in self.regs and t not in self.silent:
                    out += self._status(t, list(self.regs[t][addr:addr + n]))
            self._reply(out)
            return
        if inst == INST_SYNC_WRITE:
            return  # 응답 없음(기록만)
        for t in targets:
            if t not in self.regs or t in self.silent:
                continue
            r = self.regs[t]
            if inst == INST_PING:
                self._reply(self._status(t, []))
            elif inst == INST_READ:
                addr, n = params[0], params[1]
                self._reply(self._status(t, list(r[addr:addr + n])))
            elif inst == INST_WRITE:
                addr, data = params[0], params[1:]
                r[addr:addr + len(data)] = bytes(data)
                if mid != 0xFE:
                    self._reply(self._status(t, []))

    def write_summary(self) -> list[str]:
        return [f"{inst} id={mid} addr={p[0] if p else '-'} data={p[1:]}" for inst, mid, p in self.writes]

    def close(self) -> None:
        self._stop = True
        try:
            os.close(self.master)
        except OSError:
            pass


def open_fds_to(path: str) -> int:
    n = 0
    for fd in Path("/proc/self/fd").iterdir():
        try:
            if os.readlink(fd) == path:
                n += 1
        except OSError:
            pass
    return n


# ============================================================================================
# 공용
# ============================================================================================
RESULTS: list[dict] = []


def check(name: str, cond: bool, detail: str = "") -> bool:
    RESULTS.append({"test": name, "pass": bool(cond), "detail": detail})
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  -- {detail}" if detail else ""), flush=True)
    return cond


def setup_module():
    import so101

    VERIFY_DIR.mkdir(parents=True, exist_ok=True)
    so101.LOG_DIR = VERIFY_DIR / "logs"
    so101.RUNS_LOG = so101.LOG_DIR / "runs.jsonl"
    os.environ["HF_LEROBOT_CALIBRATION"] = str(VERIFY_DIR / "calibration")
    return so101


def std_motors(**over) -> dict[int, dict]:
    m = {i: {"fw": (3, 10), "pos": 2000 + 10 * i, "offset": -50 + 10 * i, "min": 800 + i, "max": 3200 + i} for i in range(1, 7)}
    for k, v in over.items():
        m.update(v) if k == "replace" else None
    return m


def write_calibration(so101, role: str, dev_id: str, motors: dict[int, dict]) -> Path:
    import draccus

    from lerobot.motors import MotorCalibration

    sub = ("robots", "so_follower") if role == "follower" else ("teleoperators", "so_leader")
    p = Path(os.environ["HF_LEROBOT_CALIBRATION"]) / sub[0] / sub[1] / f"{dev_id}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    cal = {j: MotorCalibration(id=i + 1, drive_mode=0, homing_offset=motors[i + 1]["offset"], range_min=motors[i + 1]["min"],
                               range_max=motors[i + 1]["max"]) for i, j in enumerate(so101.JOINTS)}
    with open(p, "w") as f, draccus.config_type("json"):
        draccus.dump(cal, f, indent=4)
    return p


# ============================================================================================
# 테스트: probe (읽기 전용)
# ============================================================================================
def test_probe(so101):
    motors = std_motors()
    write_calibration(so101, "follower", "vf_ok", motors)
    fake = FakeServoBus(motors)
    try:
        res = so101.probe_arm("follower", fake.slave_path, "vf_ok")
        check("probe 정상: 6개 응답·모델 일치", res.get("comm_ok") is True, str(res.get("errors")))
        check("probe 정상: 보정 파일=레지스터 일치", res.get("calib_status") == "match", str(res.get("calib_compare")))
        check("probe 정상: 정규화 위치 sync_read", bool(res.get("normalized")), str(res.get("normalized")))
        check("probe 정상: 모터 쓰기 명령 0건(선로 수준)", not fake.writes, "; ".join(fake.write_summary()))
        check("probe 정상: 종료 후 포트 닫힘", open_fds_to(fake.slave_path) == 0)
    finally:
        fake.close()

    # 보정 불일치 + 펌웨어 다름 + ID 3 무응답
    m2 = std_motors()
    m2[5]["fw"] = (3, 9)
    m2[2]["max"] = 3999
    write_calibration(so101, "leader", "vl_bad", std_motors())
    fake = FakeServoBus(m2, silent_ids={3})
    try:
        res = so101.probe_arm("leader", fake.slave_path, "vl_bad")
        j = res["joints"]
        check("probe 진단: ID3 무응답을 관절별로 표시", "ping_error" in j["elbow_flex"] and j["shoulder_pan"].get("model_ok"),
              j["elbow_flex"].get("ping_error", "")[:80])
        check("probe 진단: 펌웨어 차이 감지(강제 조치 없음)", res.get("firmware_same") is False, str(res.get("firmware")))
        check("probe 진단: 보정 불일치 필드 표시", res["calib_compare"].get("shoulder_lift", {}).get("range_max") == (3202, 3999),
              str(res["calib_compare"].get("shoulder_lift")))
        check("probe 진단: 쓰기 0건", not fake.writes, "; ".join(fake.write_summary()))
        check("probe 진단: 포트 닫힘", open_fds_to(fake.slave_path) == 0)
    finally:
        fake.close()

    # 장치 무응답
    fake = FakeServoBus(None)
    try:
        res = so101.probe_arm("follower", fake.slave_path, "vf_ok")
        check("probe 무응답: comm_ok False, 예외 없이 결과 반환", res.get("comm_ok") is False)
        check("probe 무응답: 쓰기 명령 전송 0건", not fake.writes, "; ".join(fake.write_summary()))
        check("probe 무응답: 포트 닫힘", open_fds_to(fake.slave_path) == 0)
    finally:
        fake.close()

    # 없는 포트
    res = so101.probe_arm("follower", "/dev/so101-verify-nonexistent", "vf_ok")
    check("probe 없는 포트: 포트 열기 실패로 보고", res.get("port_open") is False and res["errors"], str(res["errors"])[:100])

    # Ctrl+C(KeyboardInterrupt)가 읽기 도중 발생
    fake = FakeServoBus(motors)
    try:
        def factory(role, port, dev_id):
            bus = so101.make_bus(role, port, dev_id)
            orig, n = bus.read, {"c": 0}

            def read(*a, **k):
                n["c"] += 1
                if n["c"] == 5:
                    raise KeyboardInterrupt
                return orig(*a, **k)

            bus.read = read
            return bus

        got = None
        try:
            so101.probe_arm("follower", fake.slave_path, "vf_ok", bus_factory=factory)
        except KeyboardInterrupt:
            got = "KeyboardInterrupt"
        check("probe Ctrl+C: 예외가 전파되고(원래 오류 보존)", got == "KeyboardInterrupt")
        check("probe Ctrl+C: 정리 경로에서도 쓰기 0건", not fake.writes, "; ".join(fake.write_summary()))
        check("probe Ctrl+C: 포트 닫힘", open_fds_to(fake.slave_path) == 0)
    finally:
        fake.close()

    # 객체 소멸자: make_bus가 만든 공식 객체/버스를 GC해도 쓰기 없음
    fake = FakeServoBus(motors)
    try:
        import gc

        bus = so101.make_bus("follower", fake.slave_path, "vf_ok")
        bus.connect(handshake=False)
        bus.disconnect(disable_torque=False)
        del bus
        gc.collect()
        time.sleep(0.2)
        check("make_bus: 공식 객체·버스 소멸 시 쓰기 0건", not fake.writes, "; ".join(fake.write_summary()))
    finally:
        fake.close()


# ============================================================================================
# 테스트: 독립 cleanup
# ============================================================================================
def test_cleanup(so101):
    motors = std_motors()
    fake = FakeServoBus({k: dict(v, torque=1) for k, v in motors.items()})
    try:
        arms = [so101.ArmCfg("leader", "so101_leader", "vl", "/dev/so101-verify-missing"),
                so101.ArmCfg("follower", "so101_follower", "vf", fake.slave_path)]
        t0 = time.time()
        res = so101.cleanup_arms(arms)
        dt = time.time() - t0
        check("cleanup: leader 실패가 follower 정리를 막지 않음", res["leader"]["status"] == "실패" and res["follower"]["status"] == "성공",
              json.dumps({k: v["status"] for k, v in res.items()}, ensure_ascii=False))
        addrs = sorted({(w[1], w[2][0], tuple(w[2][1:])) for w in fake.writes})
        expect = sorted({(i, a, (0,)) for i in range(1, 7) for a in (40, 55)})
        check("cleanup: 쓰기는 Torque_Enable(40)=0, Lock(55)=0 뿐", addrs == expect and all(w[0] == "WRITE" for w in fake.writes),
              "; ".join(fake.write_summary()[:4]) + " ...")
        check("cleanup: 목표 위치(42) 쓰기 없음", not any(w[2][0] == 42 for w in fake.writes))
        check("cleanup: 포트 닫힘", open_fds_to(fake.slave_path) == 0)
        check("cleanup: 시간 한도 안에 종료", dt < so101.CLEANUP_TIMEOUT_S, f"{dt:.2f}s")
    finally:
        fake.close()

    # 한 모터 무응답
    fake = FakeServoBus(motors, silent_ids={4})
    try:
        t0 = time.time()
        res = so101.cleanup_arms([so101.ArmCfg("follower", "so101_follower", "vf", fake.slave_path)])
        dt = time.time() - t0
        m = res["follower"].get("motors", {})
        check("cleanup 부분 실패: 무응답 모터만 실패로 기록, 나머지 성공", res["follower"]["status"] == "실패"
              and m.get("wrist_flex") != "ok" and all(m[j] == "ok" for j in so101.JOINTS if j != "wrist_flex"), f"{dt:.2f}s")
    finally:
        fake.close()

    # 한쪽이 멈춤 → 확인 불가, 다른 쪽은 성공, 전체는 한도 시간에 종료
    def one(role, port, dev_id):
        if role == "leader":
            time.sleep(60)
        return {"status": "성공"}

    t0 = time.time()
    res = so101.cleanup_arms([so101.ArmCfg("leader", "so101_leader", "a", "/dev/x"), so101.ArmCfg("follower", "so101_follower", "b", "/dev/y")],
                             timeout_s=1.5, one=one)
    dt = time.time() - t0
    check("cleanup 지연: 멈춘 팔은 '확인 불가', 다른 팔은 성공, 한도 시간에 반환",
          res["leader"]["status"] == "확인 불가" and res["follower"]["status"] == "성공" and dt < 2.5, f"{dt:.2f}s")


# ============================================================================================
# 테스트: 공식 프로세스 실행기(신호·입력·고아 방지) — 가짜 자식 사용
# ============================================================================================
def fake_child(mode: str, marker: str) -> None:
    """검증 전용 가짜 자식 프로세스."""
    n = {"sigint": 0}
    info = {"mode": mode, "sighup_ignored": signal.getsignal(signal.SIGHUP) == signal.SIG_IGN}

    def on_int(s, f):
        n["sigint"] += 1
        if n["sigint"] == 1:
            raise KeyboardInterrupt

    if mode == "stubborn":
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        while True:
            time.sleep(0.1)
    signal.signal(signal.SIGINT, on_int)
    try:
        if mode == "interactive":
            sys.stdout.write("입력: ")
            sys.stdout.flush()
            info["line"] = sys.stdin.readline().strip()
            return
        while True:
            print("Teleop loop time: 16.50ms (61 Hz)\x1b[1A", flush=True)
            time.sleep(0.05)
    except KeyboardInterrupt:
        info["got_keyboardinterrupt"] = True
        time.sleep(0.6)  # 중복 SIGINT가 오는지 관찰
    finally:
        info["sigint_count"] = n["sigint"]
        Path(marker).write_text(json.dumps(info))


def harness(scenario: str, outdir: str) -> None:
    """pty의 세션 리더로 실행되어 so101.run_official을 호출한다."""
    so101 = setup_module()
    so101.LOG_DIR = Path(outdir)
    so101.RUNS_LOG = Path(outdir) / "runs.jsonl"
    mode = {"interactive": "interactive", "ctrlc": "loop", "sigterm": "loop", "sighup": "loop", "parentkill": "loop",
            "stubborn": "stubborn"}[scenario]
    marker = str(Path(outdir) / "child.json")
    argv = [sys.executable, __file__, "--fake-child", mode, marker]
    Path(outdir, "harness.pid").write_text(str(os.getpid()))
    rec = so101.run_official(argv, env=dict(os.environ), log_name="fake", parse_loop=True,
                             after_exit=lambda: {"after_exit": "called"}, interrupt_grace=2.0, term_grace=1.0)
    Path(outdir, "rec.json").write_text(json.dumps(rec, ensure_ascii=False))


def run_pty_scenario(scenario: str, action) -> tuple[dict | None, dict | None, str]:
    outdir = tempfile.mkdtemp(prefix=f"run_{scenario}_", dir=VERIFY_DIR)
    pid, master = pty.fork()
    if pid == 0:
        os.execv(sys.executable, [sys.executable, __file__, "--harness", scenario, outdir])
    out = b""

    def drain(t):
        nonlocal out
        end = time.time() + t
        while time.time() < end:
            try:
                import select

                r, _, _ = select.select([master], [], [], 0.05)
                if r:
                    out += os.read(master, 4096)
            except OSError:
                return

    drain(1.5)
    action(master, pid, drain, outdir)
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            wpid, _ = os.waitpid(pid, os.WNOHANG)
            if wpid:
                break
        except ChildProcessError:
            break
        drain(0.1)
    try:
        os.close(master)
    except OSError:
        pass
    rec = json.loads(Path(outdir, "rec.json").read_text()) if Path(outdir, "rec.json").exists() else None
    child = json.loads(Path(outdir, "child.json").read_text()) if Path(outdir, "child.json").exists() else None
    return rec, child, out.decode(errors="replace")


def test_runner():
    # 1) 대화형 입력 유지
    rec, child, out = run_pty_scenario("interactive", lambda m, p, d, o: (os.write(m, "abc\n".encode()), d(1.0)))
    check("runner: 자식이 터미널 입력을 그대로 받음", child is not None and child.get("line") == "abc", str(child))
    check("runner: 자식 프롬프트가 터미널에 중계됨", "입력:" in out)
    check("runner: after_exit 실행·기록", rec is not None and rec.get("cleanup") == {"after_exit": "called"})

    # 2) Ctrl+C: 같은 프로세스 그룹 → 자식이 한 번만 SIGINT를 받음
    rec, child, out = run_pty_scenario("ctrlc", lambda m, p, d, o: (d(1.0), os.write(m, b"\x03"), d(2.0)))
    check("runner Ctrl+C: 자식 KeyboardInterrupt로 정상 정리", child is not None and child.get("got_keyboardinterrupt"), str(child))
    check("runner Ctrl+C: SIGINT 중복 없음(1회)", child is not None and child.get("sigint_count") == 1, str(child))
    check("runner Ctrl+C: 기록에 사용자 중단·exit 0", rec is not None and rec.get("user_interrupt") and rec.get("exit_code") == 0,
          json.dumps({k: rec.get(k) for k in ("user_interrupt", "exit_code", "forwarded_signal")} if rec else {}, ensure_ascii=False))
    tl = (rec or {}).get("teleop_loop", {})
    check("runner: 추종 루프 출력으로 루프 구간·Hz 기록", tl.get("loop_lines", 0) > 5 and tl.get("loop_span_s", 0) > 0.3 and tl.get("hz_median"),
          json.dumps(tl, ensure_ascii=False))

    # 3) SIGTERM → 자식에게 SIGINT 전달
    def term(m, p, d, o):
        d(1.0)
        os.kill(int(Path(o, "harness.pid").read_text()), signal.SIGTERM)
        d(2.0)

    rec, child, _ = run_pty_scenario("sigterm", term)
    check("runner SIGTERM: 자식에게 SIGINT 1회 전달 후 정상 정리", child is not None and child.get("sigint_count") == 1
          and rec is not None and rec.get("forwarded_signal") == "SIGTERM" and rec.get("cleanup"), str(child))

    # 4) SIGHUP(터미널 끊김: pty master 닫기) → 자식은 SIGHUP 무시, 부모가 SIGINT 전달, 정리·기록 완료
    def hup(m, p, d, o):
        d(1.0)
        os.close(m)

    rec, child, _ = run_pty_scenario("sighup", hup)
    check("runner SIGHUP: 자식이 SIGHUP을 무시하도록 실행됨", child is not None and child.get("sighup_ignored"), str(child))
    check("runner SIGHUP: 부모가 SIGINT를 전달해 자식이 정상 정리", child is not None and child.get("got_keyboardinterrupt")
          and rec is not None and rec.get("forwarded_signal") == "SIGHUP", json.dumps(rec.get("forwarded_signal") if rec else None))
    check("runner SIGHUP: 터미널이 없어도 after_exit와 기록 완료", rec is not None and rec.get("cleanup") == {"after_exit": "called"})

    # 5) 부모 비정상 종료(SIGKILL) → PDEATHSIG로 자식에게 SIGINT → 고아로 남지 않음
    def kill_parent(m, p, d, o):
        d(1.0)
        os.kill(int(Path(o, "harness.pid").read_text()), signal.SIGKILL)
        d(2.0)

    rec, child, _ = run_pty_scenario("parentkill", kill_parent)
    check("runner 부모 SIGKILL: 자식이 SIGINT를 받아 정리하고 종료(고아 방지)", child is not None and child.get("got_keyboardinterrupt"), str(child))

    # 6) SIGINT를 무시하는 자식 → SIGTERM → SIGKILL 단계적 종료(한도 시간)
    def stub(m, p, d, o):
        d(0.5)
        os.write(m, b"\x03")
        d(5.0)

    t0 = time.time()
    rec, child, _ = run_pty_scenario("stubborn", stub)
    check("runner 무응답 자식: 한도 후 SIGTERM/SIGKILL로 끝내고 원래 종료 상태 기록",
          rec is not None and rec.get("escalated") and rec.get("exit_signal") in ("SIGTERM", "SIGKILL"),
          json.dumps({k: rec.get(k) for k in ("escalated", "exit_signal")} if rec else {}, ensure_ascii=False) + f" {time.time() - t0:.1f}s")


# ============================================================================================
# 테스트: dry-run이 장치를 열지 않고 객체를 만들지 않음, 설정 검증, hardware doctor 진단
# ============================================================================================
def write_cfg(path: Path, **kw) -> Path:
    import yaml

    base = {"leader": {"type": "so101_leader", "id": "vl", "port": None},
            "follower": {"type": "so101_follower", "id": "vf", "port": None, "max_relative_target": None},
            "teleop": {"fps": 60, "time_s": None}, "calibration_root": str(VERIFY_DIR / "calibration")}
    for k, v in kw.items():
        sec, key = k.split("__")
        base[sec][key] = v
    path.write_text(yaml.safe_dump(base, allow_unicode=True))
    return path


def test_dryrun_and_config(so101):
    import contextlib
    import io

    fake = FakeServoBus(std_motors())
    fake2 = FakeServoBus(std_motors())
    try:
        cfgp = write_cfg(VERIFY_DIR / "cfg_dry.yaml", leader__port=fake.slave_path, follower__port=fake2.slave_path,
                         follower__max_relative_target={j: 5.0 for j in so101.JOINTS})
        opened, created = [], []

        def hook(event, args):
            if event == "open" and args and isinstance(args[0], (str, bytes)) and str(args[0]).startswith("/dev/") \
                    and str(args[0]) not in ("/dev/null", "/dev/urandom"):
                opened.append(str(args[0]))

        sys.addaudithook(hook)
        from lerobot.robots.so_follower import SOFollower
        from lerobot.teleoperators.so_leader import SOLeader

        orig_f, orig_l = SOFollower.__init__, SOLeader.__init__
        SOFollower.__init__ = lambda self, *a, **k: (created.append("follower"), orig_f(self, *a, **k))[1]
        SOLeader.__init__ = lambda self, *a, **k: (created.append("leader"), orig_l(self, *a, **k))[1]
        rcs = {}
        try:
            for cmd in (["teleop", "--dry-run"], ["calibrate", "follower", "--dry-run"], ["setup-motors", "leader", "--dry-run"]):
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    rcs[" ".join(cmd)] = so101.main(["--config", str(cfgp), *cmd])
        finally:
            SOFollower.__init__, SOLeader.__init__ = orig_f, orig_l
        check("dry-run 3종 exit 0", all(v == 0 for v in rcs.values()), str(rcs))
        check("dry-run: /dev 장치 open 0건(audit hook)", not opened, str(opened))
        check("dry-run: 공식 Robot/Teleoperator 객체 생성 0건", not created, str(created))
        check("dry-run: 포트 쪽 선로에 아무 패킷도 없음", not fake.instructions and not fake2.instructions)
        # 없는 포트에서도 명령 확인 가능
        cfg_missing = write_cfg(VERIFY_DIR / "cfg_missing.yaml", leader__port="/dev/so101-nope-a", follower__port="/dev/so101-nope-b")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = so101.main(["--config", str(cfg_missing), "teleop", "--dry-run"])
        check("dry-run: 없는 포트여도 명령 표시·파싱 확인", rc == 0 and "lerobot-teleoperate" in buf.getvalue(), buf.getvalue().splitlines()[-1][:120])

        # 설정 타입 검증
        bad = write_cfg(VERIFY_DIR / "cfg_bad.yaml", teleop__time_s="<null 또는 초>", teleop__fps=True,
                        follower__max_relative_target={"shoulder_pan": 5.0})
        cfg, errs = so101.load_config(bad)
        joined = " | ".join(errs)
        check("설정 검증: 문자열 time_s 거부", "teleop.time_s" in joined)
        check("설정 검증: bool fps 거부", "teleop.fps" in joined)
        check("설정 검증: 관절 dict 키 누락 거부", "6개 관절 키" in joined, joined[:200])

        # hardware doctor 진단(포트를 열지 않음)
        rep = so101.Report()
        buf = io.StringIO()
        link_a, link_b = VERIFY_DIR / "link_a", VERIFY_DIR / "link_b"
        for l in (link_a, link_b):
            l.unlink(missing_ok=True)
            l.symlink_to(fake.slave_path)
        same = write_cfg(VERIFY_DIR / "cfg_same.yaml", leader__port=str(link_a), follower__port=str(link_b))
        cfg, errs = so101.load_config(same)
        opened.clear()
        with contextlib.redirect_stdout(buf):
            so101.doctor_hardware(rep, cfg, errs)
        titles = " | ".join(t for s, t, _ in rep.items if s == "FAIL")
        check("hardware doctor: 서로 다른 링크가 같은 실제 장치를 가리키면 FAIL", "같은 실제 장치" in titles, titles[:200])
        check("hardware doctor: 포트를 열지 않음", not opened, str(opened))
        check("hardware doctor: 보정 파일 없음은 WARN(teleop에서 거부)",
              any(s == "WARN" and "보정 파일 없음" in t for s, t, _ in rep.items))

        nul = write_cfg(VERIFY_DIR / "cfg_null.yaml")
        cfg, errs = so101.load_config(nul)
        rep = so101.Report()
        with contextlib.redirect_stdout(io.StringIO()):
            so101.doctor_hardware(rep, cfg, errs)
        check("hardware doctor: port null → FAIL '포트 미지정'", any("포트 미지정" in t for s, t, _ in rep.items if s == "FAIL"))

        miss = write_cfg(VERIFY_DIR / "cfg_m.yaml", leader__port="/dev/so101-nope-a", follower__port="/dev/so101-nope-b")
        cfg, errs = so101.load_config(miss)
        rep = so101.Report()
        with contextlib.redirect_stdout(io.StringIO()):
            so101.doctor_hardware(rep, cfg, errs)
        check("hardware doctor: 없는 포트 → FAIL '경로 없음'", any("경로 없음" in t for s, t, _ in rep.items if s == "FAIL"))

        os.chmod(fake2.slave_path, 0)
        try:
            perm = write_cfg(VERIFY_DIR / "cfg_p.yaml", leader__port=fake.slave_path, follower__port=fake2.slave_path)
            cfg, errs = so101.load_config(perm)
            rep = so101.Report()
            with contextlib.redirect_stdout(io.StringIO()):
                so101.doctor_hardware(rep, cfg, errs)
            msgs = [(t, d) for s, t, d in rep.items if s == "FAIL" and "권한" in t]
            check("hardware doctor: 권한 없음 진단(테스트용 pty, chmod 000)", bool(msgs), str(msgs)[:200])
        finally:
            os.chmod(fake2.slave_path, 0o620)

        # 점유 감지: 다른 프로세스가 포트를 열고 있음
        holder = subprocess.Popen([sys.executable, "-c", f"import os,time; fd=os.open({fake.slave_path!r}, os.O_RDWR|os.O_NOCTTY); time.sleep(30)"])
        time.sleep(0.5)
        try:
            busy = write_cfg(VERIFY_DIR / "cfg_b.yaml", leader__port=fake.slave_path, follower__port=fake2.slave_path)
            cfg, errs = so101.load_config(busy)
            rep = so101.Report()
            with contextlib.redirect_stdout(io.StringIO()):
                so101.doctor_hardware(rep, cfg, errs)
            check("hardware doctor: 다른 프로세스 점유 감지", any("사용 중" in t for s, t, _ in rep.items if s == "FAIL"))
        finally:
            holder.kill()
            holder.wait()
    finally:
        fake.close()
        fake2.close()


def test_teleop_refusal(so101):
    """보정 파일 없음/불일치이면 공식 teleop을 실행하지 않는다(자동 보정 진입 방지)."""
    import contextlib
    import io

    m = std_motors()
    fl, ff = FakeServoBus(m), FakeServoBus(m)
    launched = []
    orig = so101.run_official
    so101.run_official = lambda *a, **k: launched.append(a) or {
        "exit_code": 0, "exit_signal": None, "duration_s": 0.0, "teleop_loop": {}, "log": "logs/x.log",
        "cleanup": {"leader": {"status": "성공"}, "follower": {"status": "성공"}}}
    try:
        cfgp = write_cfg(VERIFY_DIR / "cfg_t.yaml", leader__port=fl.slave_path, follower__port=ff.slave_path,
                         leader__id="vt_leader", follower__id="vt_follower")
        for p in (VERIFY_DIR / "calibration").rglob("vt_*.json"):
            p.unlink()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = so101.main(["--config", str(cfgp), "teleop", "--onsite-confirmed"])
        check("teleop: 보정 파일 없으면 공식 명령 실행 거부", rc == 1 and not launched, buf.getvalue().strip().splitlines()[-1])
        write_calibration(so101, "leader", "vt_leader", m)
        mm = std_motors()
        mm[1]["offset"] = 99
        write_calibration(so101, "follower", "vt_follower", mm)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = so101.main(["--config", str(cfgp), "teleop", "--onsite-confirmed"])
        check("teleop: 보정 불일치면 공식 명령 실행 거부", rc == 1 and not launched and "불일치" in buf.getvalue())
        check("teleop 사전 점검: 모터 쓰기 0건", not fl.writes and not ff.writes, "; ".join(fl.write_summary() + ff.write_summary()))
        write_calibration(so101, "follower", "vt_follower", m)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = so101.main(["--config", str(cfgp), "teleop", "--onsite-confirmed", "--time-s", "30"])
        argv = launched[0][0] if launched else []
        check("teleop: 조건 충족 시 공식 lerobot-teleoperate 인자로 실행",
              bool(launched) and argv[0].endswith("lerobot-teleoperate") and "--teleop_time_s=30.0" in argv
              and f"--robot.port={ff.slave_path}" in argv, " ".join(argv[1:4]))
        check("teleop: 시작 자세 차이 표 출력", "시작 자세 차이" in buf.getvalue())
    finally:
        so101.run_official = orig
        fl.close()
        ff.close()


def main() -> int:
    if len(sys.argv) >= 2 and sys.argv[1] == "--fake-child":
        fake_child(sys.argv[2], sys.argv[3])
        return 0
    if len(sys.argv) >= 2 and sys.argv[1] == "--harness":
        harness(sys.argv[2], sys.argv[3])
        return 0
    import shutil

    if VERIFY_DIR.is_dir():  # 이 도구 전용 임시 폴더만 비운다
        shutil.rmtree(VERIFY_DIR)
    so101 = setup_module()
    print("== probe(읽기 전용)")
    test_probe(so101)
    print("== 독립 cleanup")
    test_cleanup(so101)
    print("== 실행기 신호·입력")
    test_runner()
    print("== dry-run·설정·hardware doctor")
    test_dryrun_and_config(so101)
    print("== teleop 사전 점검 거부")
    test_teleop_refusal(so101)
    n_fail = sum(1 for r in RESULTS if not r["pass"])
    (VERIFY_DIR / "result.json").write_text(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "results": RESULTS},
                                                       ensure_ascii=False, indent=1))
    print(f"\n합계 PASS {len(RESULTS) - n_fail} / {len(RESULTS)}, FAIL {n_fail}")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
