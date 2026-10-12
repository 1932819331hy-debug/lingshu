# -*- coding: utf-8 -*-
"""结构锚定按题型拆分：点名章节的问句 vs 其余问句，各自的 top-10 证据保留。

用法：[LINGSHU_FTS=1 LINGSHU_FTS_CONTEXT=1] [LINGSHU_ANCHOR=1] python bench_anchor.py A|B
写入时总是打容器标签 chapter:N（写入契约），因此「关」与「开」只差查询侧是否解析锚点。
判据：开锚定后「点名章节」组应上升，「其余」组应逐位不变（无锚点问句不进入新路径）。
另报漏检诊断：漏检证据中，所在章已有块进 top-10 的条数（= 结构锚定可回收的上限来源）。
"""
import sys
from common import build_set, evaluate, engine, emit, os
from lingshu.core.retrieval import parse_anchors


def main():
    cut = sys.argv[1]
    chunks, qs = build_set(8 if cut == "A" else 16)
    e = engine()
    m = {}
    for i, (_, no, t) in enumerate(chunks):
        m[e.add_perception(t, importance=0.5, skip_dedup=True, tags=[f"chapter:{no}"]).id] = i
    cache = {}

    def fn(q):
        if q not in cache:
            cache[q] = [m[n.id] for n, _ in e.store.search_content(q, limit=10)]
        return cache[q]
    anch = [x for x in qs if parse_anchors(x["q"])]
    rest = [x for x in qs if not parse_anchors(x["q"])]
    miss = miss_in_hit_ch = 0
    for x in qs:
        top = fn(x["q"])[:10]
        chs = {chunks[i][0] for i in top}
        for (_, cid, i) in x["ev"]:
            if i not in top:
                miss += 1
                miss_in_hit_ch += cid in chs
    ra, rr = evaluate(chunks, anch, fn), evaluate(chunks, rest, fn)
    emit(dict(suite=f"anchor_{cut}", 点名章节题=len(anch), 点名章节证据=ra["证据数"], 点名章节保留=ra["证据保留"],
              其余题=len(rest), 其余保留=rr["证据保留"], 漏检=miss, 漏检且所在章已命中=miss_in_hit_ch))


main()
