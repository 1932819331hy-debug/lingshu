# -*- coding: utf-8 -*-
"""#420 守卫：只含扩展词的子句不得在开放词表生效前被整句丢弃。

缺陷面：`compile_description` 原先先调 `hex_text.parse_description`，后者只保留
窄词表（红/绿/蓝、圆/三角/条纹、复合方位）命中的子句；只含扩展词
（黄/橙/紫/粉/棕/黑/白/灰、方/矩形/星/心/六边/菱）的子句在进入 `OPEN_COLORS` /
`OPEN_SHAPES` 前就被丢弃，生成物体静默消失。
"""
from lingshu.nn.hex_gen import compile_description


def test_extended_only_clause_is_not_dropped():
    d = compile_description("黄色星形")
    assert len(d["parts"]) == 1
    assert d["parts"][0]["shape"] == "star"
    assert d["parts"][0]["color"] == "yellow"


def test_mixed_narrow_and_extended_clauses():
    d = compile_description("左上有红圆形；右下有紫六边形")
    assert len(d["parts"]) == 2
    assert (d["parts"][0]["shape"], d["parts"][0]["color"], d["parts"][0]["zone"]) == (
        "circle", "red", "r0")
    assert (d["parts"][1]["shape"], d["parts"][1]["color"], d["parts"][1]["zone"]) == (
        "hexagon", "purple", "r8")


def test_extended_shape_words():
    cases = [("方形", "square"), ("矩形", "rectangle"), ("星形", "star"),
             ("心形", "heart"), ("六边形", "hexagon"), ("菱形", "diamond")]
    for word, en in cases:
        d = compile_description("黄色" + word)
        assert len(d["parts"]) == 1, word
        assert d["parts"][0]["shape"] == en, (word, d["parts"][0])


def test_extended_color_words():
    cases = [("橙色方形", "orange"), ("紫色星形", "purple"),
             ("粉色心形", "pink"), ("棕色六边形", "brown"),
             ("黑色菱形", "black"), ("白色方形", "white"),
             ("灰色矩形", "gray")]
    for text, color in cases:
        d = compile_description(text)
        assert len(d["parts"]) == 1, text
        assert d["parts"][0]["color"] == color, (text, d["parts"][0])
