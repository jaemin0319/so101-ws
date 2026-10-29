#!/usr/bin/env python3
"""연결된 SO-101 팔 두 대 중 어느 쪽이 leader/follower인지 모터 전원 전압으로 구분한다(읽기 전용, 팔이 움직이지 않음).

  python scripts/find_arms.py           # 시리얼 포트마다 모터 ID 1~6 응답·전압·토크·위치를 읽고 판정
  python scripts/find_arms.py --write   # 판정 결과를 config/so101.env의 SO101_FOLLOWER_PORT/SO101_LEADER_PORT에 기록

판정 기준(2026-10-23 이 장비 실측): follower는 12V 전원(모터 12.0~12.2V), leader는 5V 전원(6.1~6.2V).
SO-101 키트 구성상 두 팔의 모터 모델(STS3215, 777)이 같아 모델 번호로는 구분할 수 없다.
읽는 레지스터: Present_Voltage(62, 0.1V), Torque_Enable(40), Present_Position(56). 쓰기·토크·동작 명령을 보내지 않는다.
포트를 다른 프로그램(teleop·rollout)이 쓰고 있으면 열리지 않는다.
"""
from __future__ import annotations

import argparse
import glob
import os
import re
from pathlib import Path

import scservo_sdk as scs

REPO = Path(__file__).resolve().parent.parent
BAUD = 1_000_000
FOLLOWER_MIN_V = 9.0  # 이보다 높으면 12V 전원(follower)
LEADER_MAX_V = 7.5  # 이보다 낮으면 5V 전원(leader)


def scan(port: str) -> dict | None:
    ph, pk = scs.PortHandler(port), scs.PacketHandler(0)
    if not ph.openPort():
        print(f"{port}: 열기 실패(권한·다른 프로그램 사용 중 확인)")
        return None
    ph.setBaudRate(BAUD)
    motors = {}
    try:
        for i in range(1, 7):
            v, r1, _ = pk.read1ByteTxRx(ph, i, 62)
            if r1 != scs.COMM_SUCCESS:
                continue
            t, _, _ = pk.read1ByteTxRx(ph, i, 40)
            p, _, _ = pk.read2ByteTxRx(ph, i, 56)
            motors[i] = {"volt": v / 10, "torque": t, "pos": p}
    finally:
        ph.closePort()
    volts = [m["volt"] for m in motors.values()]
    avg = sum(volts) / len(volts) if volts else 0.0
    role = "follower" if avg >= FOLLOWER_MIN_V else "leader" if 0 < avg <= LEADER_MAX_V else "unknown"
    return {"motors": motors, "avg": avg, "role": role}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true", help="config/so101.env에 포트 기록")
    args = ap.parse_args()

    ports = sorted(glob.glob("/dev/serial/by-id/*")) or sorted(glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*"))
    if not ports:
        raise SystemExit("시리얼 장치 없음: 팔 USB·전원 연결 확인")
    found: dict[str, str] = {}
    for port in ports:
        r = scan(port)
        if r is None:
            continue
        print(f"{port} -> {os.path.realpath(port)}")
        if not r["motors"]:
            print("   응답한 모터 없음(전원·배선 확인)")
            continue
        for i, m in r["motors"].items():
            print(f"   id{i}: {m['volt']:.1f}V torque={m['torque']} pos={m['pos']}")
        print(f"   → 모터 {len(r['motors'])}/6개 응답, 평균 {r['avg']:.1f}V → {r['role']}")
        if r["role"] in found:
            print(f"   [주의] {r['role']}로 판정된 포트가 둘 이상")
        found.setdefault(r["role"], port)

    print()
    for role in ("follower", "leader"):
        print(f"SO101_{role.upper()}_PORT={found.get(role, '(못 찾음)')}")
    if args.write:
        if not {"follower", "leader"} <= found.keys():
            raise SystemExit("follower·leader를 모두 찾지 못해 기록하지 않았습니다.")
        conf = REPO / "config" / "so101.env"
        text = conf.read_text()
        for role in ("follower", "leader"):
            text = re.sub(rf"^SO101_{role.upper()}_PORT=.*$", f"SO101_{role.upper()}_PORT={found[role]}", text, flags=re.M)
        conf.write_text(text)
        print(f"기록함: {conf}")


if __name__ == "__main__":
    main()
