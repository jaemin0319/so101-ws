#!/usr/bin/env python3
"""유지보수용 lock 도구 (표준 라이브러리만 사용, Python 3.11+).

  constraints <official uv.lock> <out.txt>
      공식 LeRobot uv.lock에서 패키지별 버전을 resolver 입력 *제약*으로 뽑는다.
      - 한 패키지에 버전이 하나만 잠긴 경우만 `name==version`으로 쓴다(여러 버전이면 제외하고 주석으로 남김).
      - torch/torchvision의 로컬 꼬리표(+cu128)는 떼어 같은 공개 버전의 CPU 휠을 resolver가 고르게 한다.
      - nvidia-*/triton 같은 CUDA 전용 패키지는 제약에서 빼는 것이 아니라, CPU 휠이 요구하지 않으면 resolver가
        애초에 고르지 않는다. 제약은 "고르게 된다면 이 버전" 의미일 뿐이다.
      최종 lock(lock/uv.lock)은 이 제약을 받은 uv resolver가 만든다. 이 파일을 lock으로 쓰지 않는다.

  diff <official uv.lock> <our uv.lock> [--md]
      두 lock의 패키지/버전 차이를 출력한다(실제 비교 근거를 REFERENCES.md에 남기기 위함).
"""
from __future__ import annotations

import sys
import tomllib
from collections import defaultdict
from pathlib import Path

LOCAL_TAG_STRIP = {"torch", "torchvision"}


def load_versions(lock_path: Path) -> dict[str, list[str]]:
    data = tomllib.loads(lock_path.read_text())
    versions: dict[str, list[str]] = defaultdict(list)
    for pkg in data.get("package", []):
        name = pkg["name"]
        if "version" in pkg:
            versions[name].append(pkg["version"])
    return versions


def cmd_constraints(official: Path, out: Path) -> None:
    versions = load_versions(official)
    lines = [
        "# 공식 LeRobot uv.lock에서 생성한 resolver 입력 제약(tools/lock_tools.py constraints).",
        "# 최종 lock이 아니다. lock/uv.lock은 uv resolver가 이 제약과 lock/pyproject.toml로 만든다.",
    ]
    skipped = []
    for name in sorted(versions):
        vs = sorted(set(versions[name]))
        if name in LOCAL_TAG_STRIP:
            vs = sorted({v.split("+", 1)[0] for v in vs})
        if name == "lerobot":
            continue
        if len(vs) != 1:
            skipped.append(f"{name} ({', '.join(vs)})")
            continue
        lines.append(f"{name}=={vs[0]}")
    if skipped:
        lines.append("# 공식 lock에 여러 버전이 있어 제약에서 제외: " + "; ".join(skipped))
    out.write_text("\n".join(lines) + "\n")
    print(f"{out}: {len(lines) - 2} constraints, {len(skipped)} skipped")


def cmd_diff(official: Path, ours: Path, md: bool) -> None:
    off = load_versions(official)
    our = load_versions(ours)
    rows = []
    for name in sorted(set(our) - {"so101-env"}):
        o = sorted(set(off.get(name, [])))
        u = sorted(set(our[name]))
        if not o:
            rows.append((name, "-", ", ".join(u), "공식 lock에 없음"))
        elif u != o and not set(u) <= set(o):
            rows.append((name, ", ".join(o), ", ".join(u), "버전 다름"))
    if md:
        print("| 패키지 | 공식 uv.lock | lock/uv.lock | 차이 |")
        print("|---|---|---|---|")
        for r in rows:
            print("| " + " | ".join(r) + " |")
        if not rows:
            print("| (없음) | | | |")
    else:
        for r in rows:
            print("\t".join(r))
    print(f"\n# 우리 lock 패키지 수: {len(set(our) - {'so101-env'})}, 차이 항목: {len(rows)}", file=sys.stderr)


def main(argv: list[str]) -> int:
    if len(argv) >= 3 and argv[0] == "constraints":
        cmd_constraints(Path(argv[1]), Path(argv[2]))
        return 0
    if len(argv) >= 3 and argv[0] == "diff":
        cmd_diff(Path(argv[1]), Path(argv[2]), "--md" in argv[3:])
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
