#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""coggraph 守卫 · 节点 id 唯一化（issue #358）与导出顺序/分日与机器无关（issue #360）。

两件各钉「缺陷不再存在」，不是「代码能跑」：

A 组（#358 重复 id）
  A1 两个文件写同一 frontmatter `id` ⇒ graph.json 的节点 id 必须两两唯一，
     且 counts.nodes == 去重后 id 数（旧码：counts.nodes=2 / 去重 id 数=1，自相矛盾）。
  A2 冲突必须留痕：meta.duplicate_ids 记下「撞车的原 id、保留原名给哪个文件、
     后到者改成了什么」，stderr 出告警（旧码：无任何告警，静默双节点）。
  A3 改名后的 id 逐内容定值（= `<id>#<该文件内容 sha 前 12 位>`）⇒ 重跑稳定、不撞既有 id。
  A4 查看器口径不再互相覆盖：`{n["id"]: n for n in nodes}` 必须留下两条不同笔记
     （旧码 viewer_serve.tmpl:23 的 BY_ID 后者覆盖前者，点开「笔记乙」拿到「笔记甲」正文）。
  A5 下游 derive_edges 的 by_id 同样两条（不再是「一条不可达」）。

B 组（#360 目录序）
  B1 同一真源、仅**目录返回顺序**不同（用 os.walk 包装器注入反向序，模拟 ext4 目录项序），
     导出的节点顺序必须一致（旧码顺序即文件系统序）。
  B2 由此 derive_edges 的 R5 同名标题归属（title_owner 取 ids[0]）不得改变：
     两轮推导边的 (s, t) 集合必须逐字相同（旧码：c1→aaa1 vs c1→bbb1）。
     **本组夹具不变量**：先断言两轮都真的产出了 R5 边（=1），再断言目标相同 ——
     否则「两边都为空」会让 B2 恒真。
  B3 端到端不可复现的判据是 source_sha：两轮 source_sha 相同而节点顺序必须也相同。

C 组（#360 本地时区）
  C1 同一份 graph.json 在 TZ=UTC 与 TZ=Pacific/Kiritimati 下跑 derive_edges，
     除运行元信息 generated_at 外，产物必须逐字相同（旧码：localtime 分日 ⇒
     R4 日枢纽与边随机器时区改变）。**夹具不变量**：先断言夹具确实跨了 UTC 日界
     （两个 TZ 的 localtime 日期不同），否则 C1 是恒真。

跨批覆盖重叠自查：A4 与 A5 复用 viewer/derive 的口径，但断言落在「本件改名机制产出
的 id 集合」上，不依赖任何前批守卫的结论；B/C 组断言的是「导出/推导产物」，与前批
（#45 R4 count、#411 推导边去重、#290 族）各自的断言面不重叠。

运行：
  · pytest：python -X utf8 -m pytest tests/test_coggraph_export_id_order_guard.py -q --no-header
  · 脚本式：python -X utf8 tests/test_coggraph_export_id_order_guard.py
全部夹具写在 tempfile，绝不读写真源。
"""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

HERE = Path(__file__).resolve().parents[1]
EXPORT = HERE / "tools" / "coggraph" / "export_coggraph.py"
DERIVE = HERE / "tools" / "coggraph" / "derive_edges.py"
COGG = str(HERE / "tools" / "coggraph")


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def _doc(nid, title, extra=""):
    return "---\nid: %s\nlayer: knowledge\n%s---\n# 功能名：%s\n" % (nid, extra, title)


def _run(cmd, env=None):
    return subprocess.run([sys.executable, "-X", "utf8", *cmd], capture_output=True,
                          text=True, encoding="utf-8", timeout=120,
                          env=dict(os.environ, PYTHONUTF8="1", **(env or {})))


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    def _export(self, src, out, extra=(), env=None):
        p = _run([str(EXPORT), "--root", str(src), "--out", str(out), "--write", *extra], env=env)
        self.assertEqual(p.returncode, 0, "导出退出码 %d\nstderr:\n%s" % (p.returncode, p.stderr))
        return p, json.loads((Path(out) / "graph.json").read_text(encoding="utf-8"))

    def _derive(self, graph, out, bodies=None, extra=(), env=None):
        cmd = [str(DERIVE), "--graph", str(graph), "--out", str(out), "--write"]
        if bodies is not None:
            cmd += ["--bodies-root", str(bodies)]
        p = _run(cmd + list(extra), env=env)
        self.assertEqual(p.returncode, 0, "推导退出码 %d\nstderr:\n%s" % (p.returncode, p.stderr))
        return json.loads((Path(out) / "derived_edges.json").read_text(encoding="utf-8"))


class DuplicateNodeIdTests(_Base):
    """A 组：issue #358 —— 同 id 双节点。"""

    def _fixture(self):
        src = self.base / "src"
        # 两个不同目录、不同标题，但 frontmatter id 撞车
        _write(str(src / "knowledge" / "aaa" / "note.md"), _doc("note-1", "笔记甲"))
        _write(str(src / "knowledge" / "bbb" / "note.md"), _doc("note-1", "笔记乙"))
        return src

    def test_a1_a2_a3_duplicate_ids_are_unique_and_recorded(self):
        src = self._fixture()
        out = self.base / "out"
        out.mkdir()
        proc, g = self._export(src, out)

        ids = [n["id"] for n in g["nodes"]]
        # A1：唯一 + counts 自洽
        self.assertEqual(len(ids), 2, "夹具应有两条节点")
        self.assertEqual(len(set(ids)), len(ids),
                         "节点 id 必须两两唯一（旧码：同 id 双节点，counts.nodes 与去重 id 数自相矛盾）")
        self.assertEqual(g["meta"]["counts"]["nodes"], len(set(ids)),
                         "counts.nodes 必须等于去重后的 id 数")

        # A2：冲突留痕
        dup = g["meta"].get("duplicate_ids")
        self.assertIsInstance(dup, list, "meta.duplicate_ids 必须存在（旧码无任何登记）")
        self.assertEqual(len(dup), 1, "恰好登记一条冲突：%r" % (dup,))
        rec = dup[0]
        self.assertEqual(rec["id"], "note-1")
        self.assertTrue(rec["kept"].endswith("aaa/note.md"),
                        "先到者保留原名（sorted 遍历序 ⇒ aaa 在前）：%r" % rec)
        self.assertIn("note-1", proc.stderr, "重复 id 必须出告警（stderr）")
        self.assertTrue(("重复" in proc.stderr), "告警文案须含「重复」：%r" % proc.stderr)

        # A3：改名逐内容定值
        renamed = [i for i in ids if i != "note-1"]
        self.assertEqual(len(renamed), 1, ids)
        bbb_sha = hashlib.sha256(
            (self.base / "src" / "knowledge" / "bbb" / "note.md").read_bytes()).hexdigest()[:12]
        self.assertEqual(renamed[0], "note-1#%s" % bbb_sha,
                         "后到者改名为 <id>#<该文件内容 sha 前 12 位>（逐内容定值、不撞既有 id）")
        # 重跑稳定（不随遍历/哈希种子变）
        out2 = self.base / "out2"
        out2.mkdir()
        _, g2 = self._export(src, out2)
        self.assertEqual([n["id"] for n in g2["nodes"]], ids, "同一真源重跑必须得到同一 id 序列")

    def test_a4_a5_viewer_and_derive_no_longer_collide(self):
        src = self._fixture()
        out = self.base / "out"
        out.mkdir()
        _, g = self._export(src, out)

        # A4：查看器口径（viewer_serve.tmpl:23 的同款字典推导）
        by_id = {n["id"]: n for n in g["nodes"]}
        self.assertEqual(len(by_id), 2,
                         "查看器 BY_ID 必须留下两条不同笔记（旧码后者覆盖前者 ⇒ 只剩 1 条）")
        self.assertEqual(sorted(n["title"] for n in by_id.values()), ["笔记乙", "笔记甲"])

        # A5：下游推导器 by_id 同样两条
        d = self._derive(out / "graph.json", self.base / "dout")
        self.assertEqual(len({n["id"] for n in g["nodes"]}), 2)
        self.assertEqual(d["meta"]["counts"]["nodes"], 2)


class DirOrderDeterminismTests(_Base):
    """B 组：issue #360 —— 目录遍历顺序不得渗进产物。"""

    TITLE = "认知图导出器使用说明"

    def _fixture(self):
        src = self.base / "src"
        _write(str(src / "knowledge" / "aaa" / "aaa1.md"), _doc("aaa1", self.TITLE))
        _write(str(src / "knowledge" / "bbb" / "bbb1.md"), _doc("bbb1", self.TITLE))
        _write(str(src / "knowledge" / "ccc" / "c1.md"),
               _doc("c1", "引用者节点", extra="") + "\n正文提到 %s 。\n" % self.TITLE)
        return src

    def _export_with_dir_order(self, src, out, reverse):
        """在进程内跑导出；reverse=True 时用包装器把每个目录的子目录列表反转，
        模拟「文件系统返回的目录序」与字母序相反（ext4 目录项序）。"""
        sys.path.insert(0, COGG)
        try:
            import importlib
            E = importlib.import_module("export_coggraph")
            E = importlib.reload(E)
            orig_walk = E.os.walk

            def patched(root, *a, **kw):
                for dirpath, dirnames, filenames in orig_walk(root, *a, **kw):
                    if reverse:
                        dirnames.sort(reverse=True)
                    yield dirpath, dirnames, filenames

            E.os.walk = patched
            os.makedirs(str(out), exist_ok=True)
            rc = E.main(["--root", str(src), "--out", str(out), "--write"])
            E.os.walk = orig_walk
        finally:
            sys.path.remove(COGG)
        self.assertEqual(rc, 0, "导出退出码 %d" % rc)
        return json.loads((Path(out) / "graph.json").read_text(encoding="utf-8"))

    def test_b1_b2_b3_node_order_and_r5_target_stable(self):
        src = self._fixture()
        gs, ds = {}, {}
        for tag, rev in (("fwd", False), ("rev", True)):
            out = self.base / ("out_" + tag)
            g = self._export_with_dir_order(src, out, rev)
            gs[tag] = g
            dout = self.base / ("dout_" + tag)
            d = self._derive(out / "graph.json", dout, bodies=src)
            ds[tag] = d

        # B1：节点顺序一致
        order_fwd = [n["id"] for n in gs["fwd"]["nodes"]]
        order_rev = [n["id"] for n in gs["rev"]["nodes"]]
        self.assertEqual(order_fwd, order_rev,
                         "同一真源、仅目录返回顺序不同 ⇒ 节点顺序必须一致（旧码顺序=文件系统序）")
        self.assertEqual(order_fwd, ["aaa1", "bbb1", "c1"],
                         "按目录/文件名字典序：%r" % order_fwd)

        # B2：R5 归属不得随顺序改变；先钉「真的产出了 R5 边」再比目标。
        # 注：每个节点正文里的 `# 功能名：<标题>` 行本身就是一次提及，故同名标题的
        # 两条节点会互相产生一条 body_crossref（这是 R5 既有行为，非本件范围）。
        r5_fwd = ds["fwd"]["meta"]["rules"]["R5_body_crossref"]
        r5_rev = ds["rev"]["meta"]["rules"]["R5_body_crossref"]
        self.assertGreater(r5_fwd, 0, "夹具不变量：正向序下必须真的产出 R5 边（否则本组恒真）")
        self.assertEqual(r5_fwd, r5_rev, "两轮 R5 边数必须一致")
        # 只比 R5 边（R1 桶枢纽边也随节点顺序出现，非本件判据面）
        pairs_fwd = {(e["s"], e["t"]) for e in ds["fwd"]["edges"] if e["rule"] == "R5_body_crossref"}
        pairs_rev = {(e["s"], e["t"]) for e in ds["rev"]["edges"] if e["rule"] == "R5_body_crossref"}
        self.assertEqual(pairs_fwd, pairs_rev,
                         "R5 边 (s,t) 集合必须逐字相同（旧码：指向 aaa1 还是 bbb1 取决于目录序）")
        self.assertEqual(pairs_fwd, {("bbb1", "aaa1"), ("c1", "aaa1")},
                         "同名标题的规范归属必须是先见者 aaa1：%r" % (sorted(pairs_fwd),))

        # B3：source_sha 相同 ⇒ 顺序也必须相同（sha 本身按 id 排序，两轮必然相同）
        self.assertEqual(gs["fwd"]["meta"]["source_sha"], gs["rev"]["meta"]["source_sha"],
                         "两轮 source_sha 相同（这正是旧缺陷的暴露点：指纹同、产物不同）")


class LocalTimezoneDeterminismTests(_Base):
    """C 组：issue #360 —— R4 分日不得随机器时区改变。"""

    # 两个 TZ 下 localtime 落在不同日历日、gmtime 落在同一日（跨日界）。
    # TZ 用 POSIX 规格串（"XXX-14" = UTC+14、"YYY11" = UTC-11）：本机实测（Windows
    # Python 3.12）`TZ=Pacific/Kiritimati` 这类 IANA 名字**不生效**（退回 UTC+1 附近），
    # 而 POSIX 串生效（`TZ=XXX-14` → localtime 03:26 次日）。夹具用后者才有判别力。
    TS = 1762000000   # UTC 2025-11-01 12:26:40
    TZ_WEST = "YYY11"       # UTC-11 → localtime 2025-11-01
    TZ_EAST = "XXX-14"      # UTC+14 → localtime 2025-11-02

    def _fixture_graph(self):
        out = self.base / "fixture"
        out.mkdir()
        nodes = [{"id": "n%d" % i, "title": "节点 %d" % i, "layer": "knowledge",
                  "bucket": "kb", "tags": [], "importance": 0, "created": self.TS + i,
                  "ref_root": "", "ref_dir": "", "path": "n%d.md" % i} for i in range(3)]
        g = out / "graph.json"
        with io.open(str(g), "w", encoding="utf-8", newline="\n") as f:
            json.dump({"meta": {"source_sha": "tz-fixture"}, "nodes": nodes, "edges": []}, f,
                      ensure_ascii=False)
        return g

    def test_c1_day_hub_is_timezone_independent(self):
        # 夹具不变量（两条，缺一则本件恒真）：
        #  ① 两个 TZ 的**本地**日历日确实不同；
        #  ② TZ 串在本机 Python 上**真的生效**（否则两轮子进程都跑机器本地时区，
        #     比对退化为「自己等于自己」）。故用子进程实测 localtime 再下断言。
        probe = ("import time;print(time.strftime('%Y-%m-%d %H:%M',"
                 "time.localtime({})))").format(self.TS)
        got = {}
        for tag, tz in (("west", self.TZ_WEST), ("east", self.TZ_EAST)):
            p = _run(["-c", probe], env={"TZ": tz})
            self.assertEqual(p.returncode, 0, p.stderr)
            got[tag] = p.stdout.strip()
        self.assertNotEqual(got["west"], got["east"],
                            "夹具不变量：TZ 串必须真的改变 localtime（否则比对恒真）：%r" % (got,))
        day_west, day_east = got["west"][:10], got["east"][:10]
        self.assertNotEqual(day_west, day_east,
                            "夹具不变量：两个 TZ 的本地日历日必须不同（%s vs %s）" % (day_west, day_east))

        g = self._fixture_graph()
        docs = {}
        for tag, tz in (("west", self.TZ_WEST), ("east", self.TZ_EAST)):
            out = self.base / ("dout_" + tag)
            docs[tag] = self._derive(g, out, extra=("--small-bucket", "0"), env={"TZ": tz})

        for tag, doc in docs.items():
            doc["meta"].pop("generated_at", None)   # 运行元信息，非产物语义
        self.assertEqual(docs["west"], docs["east"],
                         "同一 graph.json 在两个 TZ 下产物必须逐字相同（旧码 localtime 分日 ⇒ 日枢纽不同）")
        hubs = {h["id"] for h in docs["west"]["hubs"] if h["id"].startswith("day:")}
        want = "day:" + time.strftime("%Y-%m-%d", time.gmtime(self.TS))
        self.assertEqual(hubs, {want},
                         "日枢纽必须按 UTC 日历日命名：%r" % (hubs,))


if __name__ == "__main__":
    unittest.main(verbosity=2)
