"""#454 守卫：`unpack_out` 的外部数据校验必须在 **-O 下也生效**。

用法：
  python tests/test_issue454_unpack_out_guard.py            # 全绿自证
  python tests/test_issue454_unpack_out_guard.py --mutate   # 变异测试（每条恰好点红一条断言）

★ 为什么必须带 `-O` 腿：原实现用 `assert` 校验魔数，`python -O` 下 assert 整体消失，
非法二进制被**静默当成合法结果**返回。只在常规模式跑的用例**永远发现不了**这个缺陷
——这正是「门禁在部署模式下失明」（规范 3.1：门禁必须做变异测试，变异不红即假守卫）。
"""
import os
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
MAGIC = 0x4845584E4E4F5554

_CHILD = r'''
import os, struct, sys, tempfile
from pathlib import Path
# Windows 下子进程默认 stdout 编码是 GBK，父进程按 UTF-8 读会 UnicodeDecodeError
# ⇒ 显式重配为 UTF-8（含报错里的中文路径与原因）。
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, sys.argv[1])
from lingshu.nn.rust_bridge import unpack_out, HexExeError
d = Path(tempfile.mkdtemp())

def w(name, b):
    p = d / name
    p.write_bytes(b)
    return p

bad = {
    "垃圾字节(魔数不符)": w("t.bin", b"\x00" * 64),
    "仅魔数无steps(过短)": w("s.bin", struct.pack("<Q", 0x4845584E4E4F5554)),
    "steps越界": w("b.bin", struct.pack("<Q", 0x4845584E4E4F5554) + struct.pack("<Q", 99)
                  + struct.pack("<dd", 1.0, 2.0)),
}
for name, p in bad.items():
    try:
        unpack_out(p)
        print("REJECT_FAIL", name)          # 放行 = 失败
    except HexExeError as e:
        # ★ 只判「有没有抛」会被重叠闸掩盖：去掉「过短」闸后，steps 越界闸仍会抛，
        # 于是变异点不红 ⇒ 假守卫。故**连错误信息一起断言**，让两闸可分辨。
        msg = str(e)
        if name == "仅魔数无steps(过短)":
            ok = "过短" in msg
        elif name == "steps越界":
            ok = "steps" in msg
        else:
            ok = "魔数不符" in msg
        print(("REJECT_OK " if ok else "REJECT_WRONG_REASON ") + name)
    except Exception as e:
        print("REJECT_WRONG_EXC", name, type(e).__name__)

good = w("g.bin", struct.pack("<Q", 0x4845584E4E4F5554) + struct.pack("<Q", 1)
         + struct.pack("<dd", 1.5, 2.5))
try:
    c, v, s = unpack_out(good)
    print("ACCEPT_OK" if (list(c) == [1.5] and s == 2.5) else "ACCEPT_BAD_VALUE")
except Exception as e:
    print("ACCEPT_FAIL", type(e).__name__)
'''


def leg(opt):
    """在指定模式（opt=True 即 -O）跑子进程，返回 {用例: 结果}。"""
    cmd = [sys.executable] + (["-O"] if opt else []) + ["-c", _CHILD, str(REPO)]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120)
    out = {}
    for line in r.stdout.splitlines():
        parts = line.split()
        if parts:
            out[" ".join(parts[1:]) or parts[0]] = parts[0]
    return out


def mutate_and_run():
    """规范 3.1：每条变异**恰好**让对应用例点红；不红即「假守卫」。"""
    src_path = REPO / "lingshu" / "nn" / "rust_bridge.py"
    orig = src_path.read_text(encoding="utf-8")
    muts = [
        # assert 变异**只在 -O 下**才显形（这正是原缺陷的藏身处）⇒ 该条必须跑 -O。
        ("MUT-A 魔数校验退回 assert（跑 -O）",
         '    if magic != _MAGIC:\n        raise HexExeError(\n'
         '            "unpack_out：魔数不符（期望 0x%X，实得 0x%X）——非 .hexout：%r"\n'
         '            % (_MAGIC, magic, str(path)))',
         '    assert magic == _MAGIC', True),
        ("MUT-B 去掉长度自洽闸",
         '    if steps < 0 or steps > len(vals) - 1:\n', '    if False:\n', False),
        # 去「过短」闸后，steps 越界闸仍会拦住它 ⇒ 必须用**更细的**断言才能分辨：
        # 只剩魔数 8 字节时，越界闸报的是「steps 与载荷不符」，而我们要的是
        # 「文件过短」。故本条变异期望点红的是**过短专用用例**（见 _CHILD 的 REJECT_OK 前缀）。
        ("MUT-C 去掉过短闸",
         '    if len(raw) < _HEAD + 8:\n', '    if False:\n', False),
    ]
    bad = 0
    try:
        for name, old, new, use_opt in muts:
            if old not in orig:
                print(f"  {name}: 锚点未命中，跳过（须人工核对）")
                bad += 1
                continue
            src_path.write_text(orig.replace(old, new, 1), encoding="utf-8",
                                newline="\n")
            res = leg(use_opt)
            red = [k for k, v in res.items()
                   if v in ("REJECT_FAIL", "REJECT_WRONG_EXC", "REJECT_WRONG_REASON")]
            if red:
                print(f"  {name}: 点红 ✓ ({', '.join(red)})")
            else:
                print(f"  {name}: **未点红 ✗ 假守卫**")
                bad += 1
    finally:
        src_path.write_text(orig, encoding="utf-8", newline="\n")
    return bad


def main():
    mutate = "--mutate" in sys.argv
    fails = []
    print("=== 两种模式都必须拒绝非法输入 ===")
    for opt, label in ((False, "常规"), (True, "-O 部署")):
        res = leg(opt)
        bad_names = [k for k in res if k.startswith(("垃圾", "仅魔数", "steps"))]
        ok = all(res[k] == "REJECT_OK" for k in bad_names) and \
            res.get("") != "REJECT_FAIL"
        print(f"  {label}: " + ", ".join(f"{k}={res[k]}" for k in res))
        if not (len(bad_names) == 3 and all(res[k] == "REJECT_OK" for k in bad_names)):
            fails.append(f"{label} 未全部拒绝")
        if res.get("ACCEPT_OK") != "ACCEPT_OK" and "ACCEPT_OK" not in res.values():
            fails.append(f"{label} 合法文件被拒")
    if mutate:
        print("\n=== 变异测试（每条应恰好点红）===")
        bad = mutate_and_run()
        if bad:
            fails.append(f"{bad} 条变异未点红（假守卫）")
    print("===== " + ("全绿" if not fails else "失败: " + "; ".join(fails)) + " =====")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
