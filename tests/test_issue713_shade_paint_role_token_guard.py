# -*- coding: utf-8 -*-
"""#713 守卫：`role_for_material` 的短关键词**词元**匹配（不得被子串误命中）。

============================================================================ 缺陷
`ROLE_KEYWORDS` 里有 `led`/`alu`/`abs`/`oak`/`lamp`/`iron` 这类 3–4 字母词，旧实现
`if k in s` 做**裸子串**匹配 ⇒ 普通材质名被误抢：
  `M_filled_bracket`(fil·**led**) → `emissive`（unlit=True）/ `M_modelled`(mode·**led**) /
  `M_clamp`(c·**lamp**) / `M_value`(v·**alu**e) → `metal` / `mesh_evaluated`(ev·**alu**ated) /
  `M_environment_map`(env·**iron**ment) / `M_cloak_panel`(cl·**oak**) …
`emissive` 的 `unlit=True` 让 `shade_studio` 第一行直接 `return EMISSIVE_GAIN`
（无光照、无边缘光、无视角项）⇒ 成图上是「发光贴片」，是最刺眼的错误渲染。
`ROLE_KEYWORDS` 的注释只声明了**顺序**敏感（`painted_metal` 先于 `metal`），
没意识到**子串**同样会误抢。

============================================================================ 判据来源
  · issue #713（作者 Yinfeng818，2026-10-16）的复现读数与「加词边界」修复建议；
  · 实现侧口径（`lingshu/world/shade_paint.py`）：`_norm_name`（小写 + camelCase 驼峰
    处插入空格）+ `_kw_hit`（ASCII 关键词左右不得再是 ASCII 字母；CJK 关键词按子串）；
  · 为什么这样判：材质名是**标识符**不是自然语言句子，词元边界只认 ASCII 字母，
    数字/下划线/空格都算分隔符（`M_alu2`/`alu_wheel`/`WoodOak` 必须继续命中）；
    而 `WoodOak` 这类驼峰粘连必须切分——**漏词比误命中更难发现**（真名被降级成
    `painted_metal` 只会让画面变灰，不会报错）。

============================================================================ 断言组
  A 正对照（量具有效）：修前/修后都能测到「独立词元命中」这一已知正确形态。
  B 误命中面：27 个普通材质名/工程名必须全部回退 `DEFAULT_ROLE`，且 `unlit=False`。
  C 不漏词：每个关键词各造 `_kw_part` 与 `_kwPart` 两种形态，全部仍按该关键词命中；
    camelCase 粘连真名（`WoodOak`/`M_paintedMetal`/`M_ledStrip`/`M_LampShade`）保留。
  D 边界口径逐条钉死：数字/下划线是分隔符，字母不是；CJK 关键词按子串。
  E 原本正确命中集（含中文名、None、空串）逐字不变。

============================================================================ 回退即红
把 `role_for_material` 的匹配改回 `if k in s`（或把 `_kw_hit` 换成裸 `in`）⇒
B 组 27 条全部变红并点名具体名字（`test_b1_*` 的 `bad` 字典会列出每个
`名字 -> 实得 role`）；把 `_norm_name` 的驼峰切分去掉 ⇒ C 组
`test_c3_camelcase_粘连真名不得被漏掉` 变红并点名 `WoodOak`。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from lingshu.world import shade_paint as SP  # noqa: E402

# ── 误命中面：普通材质名 / 工程名（关键词被包在更长字母串内部）─────────────
FP_NAMES = (
    "M_filled_bracket", "M_modelled", "M_clamp", "M_value", "mesh_evaluated",
    "M_environment_map", "M_absorber_pad", "M_cloak_panel", "M_bracket_filled",
    "M_unvalued_slot", "M_soak_pit", "M_cloaking_field",
    "M_ironclad_hull", "M_ironside", "M_silverback", "M_goldfish_bowl",
    "M_windowless", "M_glasshouse", "M_plasticine", "M_woodchuck",
    "M_stonewall", "M_skinflint", "M_fabricate", "M_resinous",
    "M_neonatal", "M_emitted", "M_glowing_review",
)
# ── 原本正确命中集：关键词是独立词元（含中文名 / camelCase / 退化输入）──────
CTRL_CASES = {
    "M_steel": "metal", "M_brass": "metal", "M_iron_bar": "metal",
    "M_aluminium_hull": "metal", "M_aluminum_plate": "metal",
    "M_alu_wheel": "metal", "M_alu2": "metal", "alu_wheel": "metal",
    "M_copper_pipe": "metal", "M_chrome_ring": "metal",
    "M_bronze_bell": "metal", "M_gold_leaf": "metal", "M_silver_ring": "metal",
    "M_painted_metal": "painted_metal", "M_paintedMetal": "painted_metal",
    "M_paint_shop": "painted_metal", "M_coating_thin": "painted_metal",
    "M_lacquer_box": "painted_metal",
    "M_glow": "emissive", "M_glow_lamp": "emissive", "M_lamp_shade": "emissive",
    "M_led_strip": "emissive", "M_ledStrip": "emissive", "M_neon_sign": "emissive",
    "M_LampShade": "emissive", "M_emissive_band": "emissive",
    "mat_rubber_grip": "rubber", "rubber_grip": "rubber", "M_tyre": "rubber",
    "Glass_Lens": "glass", "M_glass_pane": "glass", "M_crystal_ball": "glass",
    "M_abs_housing": "plastic", "M_plastic_shell": "plastic",
    "WoodOak": "wood", "M_oak_plank": "wood", "M_timber_beam": "wood",
    "concrete_floor": "stone", "M_granite_top": "stone", "M_marble_slab": "stone",
    "cloth_banner": "fabric", "M_canvas_tent": "fabric",
    "skin_arm": "skin", "M_leather_seat": "skin",
    "M_dark": "painted_metal", "UNKNOWN_MAT": "painted_metal",
    "M_涂装_01": "painted_metal", "M_灯罩": "emissive", "M_发光条": "emissive",
    "M_金属外壳": "metal", "M_黄铜件": "metal", "M_木箱": "wood",
    "M_石基": "stone", "M_胶垫": "rubber", "M_布幔": "fabric",
    "M_玻璃窗": "glass", "M_塑料件": "plastic", "M_皮革": "painted_metal",
    "": "painted_metal", None: "painted_metal",
}


def _kw_hits(name):
    """按 `ROLE_KEYWORDS` 顺序返回 (role, kw)；无命中返回 (None, None)。"""
    s = SP._norm_name(name)
    for role, keys in SP.ROLE_KEYWORDS:
        for k in keys:
            if SP._kw_hit(k, s):
                return role, k
    return None, None


def test_a1_正对照_量具有效_独立词元命中可测():
    """正对照①：探针能测到「关键词自成词元」的已知正确形态（不是恒绿也不是恒红）。"""
    assert len(SP.ROLE_KEYWORDS) == 10, "关键词表被改动（10 个角色）"
    assert SP.role_for_material("M_glow_lamp") == "emissive"
    assert SP.role_for_material("M_steel") == "metal"
    assert SP.role_for_material("M_alu_wheel") == "metal"
    assert SP.role_for_material("M_led_strip") == "emissive"
    # 反面对照②：同一量具必须能测到「不同输入给不同输出」（否则是恒绿）
    assert SP.role_for_material("M_steel") != SP.role_for_material("M_glow_lamp")
    assert SP.role_for_material("M_steel") != SP.role_for_material("Glass_Lens")


def test_b1_普通材质名不得被短关键词子串抢走():
    """#713 主断言：27 个普通材质名/工程名必须回退 DEFAULT_ROLE。"""
    assert len(FP_NAMES) >= 20, "误命中名字集非空硬断言"
    bad = {n: (SP.role_for_material(n), SP.DEFAULT_ROLE) for n in FP_NAMES
           if SP.role_for_material(n) != SP.DEFAULT_ROLE}
    assert not bad, "普通材质名被短关键词子串抢走（#713 缺陷形态）：%s" % bad


def test_b2_普通材质名不得被判成自发光_unlit():
    """`unlit=True` 会让 `shade_studio` 第一行直接返回 EMISSIVE_GAIN ⇒ 发光贴片。"""
    assert len(FP_NAMES) >= 20, "误命中名字集非空硬断言"
    unlit = {n: SP.role_for_material(n) for n in FP_NAMES
             if SP.role_palette(SP.role_for_material(n))["unlit"]}
    assert not unlit, "普通材质名落入 unlit=True（整面同亮/无光照）：%s" % unlit
    # 正对照：`emissive` 确实是 unlit，`painted_metal` 确实不是（量具有判别力）
    assert SP.role_palette("emissive")["unlit"] is True
    assert SP.role_palette(SP.DEFAULT_ROLE)["unlit"] is False


def test_b3_误命中面的端到端读数_studio强度必须不是EMISSIVE_GAIN():
    """走真实调用路径：误判成 emissive 会让强度恒为 EMISSIVE_GAIN（无光照/无视角）。"""
    v = (0.0, 0.0, -1.0)
    normals = ((0.0, 1.0, 0.0), (0.0, -1.0, 0.0), (-1.0, 0.0, 0.0))
    for n in FP_NAMES:
        role = SP.role_for_material(n)
        vals = [SP.shade_studio(nm, v, role) for nm in normals]
        assert len(set(round(x, 9) for x in vals)) > 1, \
            "%s -> role=%s：三个不同朝向的面强度相同 ⇒ 该角色无光照" % (n, role)
        assert not all(abs(x - SP.EMISSIVE_GAIN) < 1e-12 for x in vals), \
            "%s -> role=%s：强度恒为 EMISSIVE_GAIN（发光贴片）" % (n, role)


def test_c1_每个关键词两种形态都不得漏词():
    """不漏词（比误命中更隐蔽的回归）：每个关键词各造 `_kw_part` 与 `_kwPart`。"""
    cover = []
    for _role, keys in SP.ROLE_KEYWORDS:
        for k in keys:
            cover.append("M_%s_part" % k if k.isascii() else "M_%s件" % k)
            if k.isascii():
                cover.append("M_%sPart" % k)
    assert len(cover) >= 60, "关键词覆盖集过小（%d）" % len(cover)
    lost = {}
    for n in cover:
        role, kw = _kw_hits(n)
        if role is None:
            lost[n] = "无关键词命中"
    assert not lost, "词元形态的关键词名被漏掉（修法过紧）：%s" % lost


def test_c2_每个关键词都必须能被自己的形态命中且角色正确():
    """逐关键词钉死：`M_<kw>_part` 必须判成该关键词所属的 role。"""
    wrong = {}
    for role, keys in SP.ROLE_KEYWORDS:
        for k in keys:
            for n in ("M_%s_part" % k if k.isascii() else "M_%s件" % k,
                      ("M_%sPart" % k) if k.isascii() else None):
                if n is None:
                    continue
                got = SP.role_for_material(n)
                if got != role:
                    wrong[n] = (got, role)
    assert not wrong, "关键词未判成自己的角色：%s" % wrong


def test_c3_camelcase_粘连真名不得被漏掉():
    """驼峰粘连（无分隔符）的独立词元必须切分——只加 `\\b` 会把这 4 个漏掉。"""
    cases = {"WoodOak": "wood", "M_paintedMetal": "painted_metal",
             "M_ledStrip": "emissive", "M_LampShade": "emissive"}
    bad = {n: (SP.role_for_material(n), want) for n, want in cases.items()
           if SP.role_for_material(n) != want}
    assert not bad, "camelCase 粘连真名被漏词（缺驼峰切分）：%s" % bad


def test_c4_缩写前缀粘连真名不得被漏掉():
    """「全大写缩写 + TitleCase 词」的粘连（`MGlow`/`SMGlow`/`ABCGlow`）必须切分。

    只做「小写→大写」切分（不做「大写→大写+小写」切分）会把这整类真名漏掉：
    实测 `MGlow`/`SMGlow`/`ABCGlow`/`ABCLed` 全部从 `emissive` 退化成 `DEFAULT_ROLE`。
    """
    cases = {"MGlow": "emissive", "SMGlow": "emissive", "ABCGlow": "emissive",
             "ABGlow": "emissive", "ABCLed": "emissive", "M_LED": "emissive",
             "abcLED": "emissive", "GlassLens": "glass", "WoodOak": "wood",
             "SMAlu": "metal", "ABCSteel": "metal"}
    bad = {n: (SP.role_for_material(n), want) for n, want in cases.items()
           if SP.role_for_material(n) != want}
    assert not bad, "缩写前缀粘连真名被漏词：%s" % bad


def test_d1_词元边界只认ASCII字母_数字与下划线是分隔符():
    """边界口径：`M_alu2` / `alu_wheel` 照旧命中；`M_aluminum`（含在更长词里）不命中。"""
    assert SP.role_for_material("M_alu2") == "metal"
    assert SP.role_for_material("alu_wheel") == "metal"
    assert SP.role_for_material("M_aluminium") == "metal"
    # 反面对照：同一关键词被更长字母串包住 ⇒ 不命中
    assert SP.role_for_material("M_valued") == SP.DEFAULT_ROLE
    assert SP.role_for_material("M_filled") == SP.DEFAULT_ROLE


def test_d2_中文关键词保持子串():
    """中文没有词边界，且 `灯`/`漆`/`石` 本身即语义单元 ⇒ 子串命中是期望行为。"""
    assert SP.role_for_material("灯") == "emissive"
    assert SP.role_for_material("M_灯罩") == "emissive"
    assert SP.role_for_material("漆") == "painted_metal"
    assert SP.role_for_material("M_石基") == "stone"
    assert SP.role_for_material("发光") == "emissive"


def test_d4_缩写前缀不抢真词_AB开头的名字按真词判定():
    """`ABSkin`/`ABStone`：改前 `abs`（plastic）抢在真词之前；切掉缩写后按真词判。

    这是**改好**不是误伤（关键词表 `plastic` 先于 `skin`/`stone`）。
    """
    assert SP.role_for_material("ABSkin") == "skin"
    assert SP.role_for_material("ABStone") == "stone"
    # 正对照：真名 `ABS_housing` 仍走 plastic（abs 是独立词元）
    assert SP.role_for_material("ABS_housing") == "plastic"
    assert SP.role_for_material("M_abs_housing") == "plastic"


def test_d3_中文与ASCII混合名两类关键词都要能命中():
    assert SP.role_for_material("M_灯罩_aluminium") == "emissive"   # 顺序：emissive 先
    assert SP.role_for_material("M_木箱_steel") == "metal"          # metal 先于 wood
    assert SP.role_for_material("M_玻璃_aluminium") == "metal"


def test_e1_原本正确命中集逐字不变():
    """回归护栏：53 条正常名（含中文/None/空串）改前改后必须一致。"""
    assert len(CTRL_CASES) >= 40, "对照组非空硬断言（%d）" % len(CTRL_CASES)
    bad = {k: (want, SP.role_for_material(k)) for k, want in CTRL_CASES.items()
           if SP.role_for_material(k) != want}
    assert not bad, "原本正确命中的材质名判定被改动：%s" % bad


def test_e2_优先级_painted_metal必须先于metal():
    assert SP.role_for_material("M_painted_metal") == "painted_metal"
    assert SP.role_for_material("painted") == "painted_metal"
    assert SP.role_for_material("M_paint_steel") == "painted_metal"


def test_e3_退化输入回退DEFAULT_ROLE():
    for name in (None, "", 0, "   ", "___", "M_", "x"):
        assert SP.role_for_material(name) == SP.DEFAULT_ROLE, repr(name)


def test_f1_修法不引入恒绿_负形态必须被区分():
    """判别力自证：同一关键词的「词元形态」命中、「包在更长词里」不命中。"""
    pairs = [("M_alu_wheel", "M_value"), ("M_led_strip", "M_filled_bracket"),
             ("M_lamp_shade", "M_clamp"), ("M_iron_bar", "M_environment_map"),
             ("M_oak_plank", "M_cloak_panel"), ("M_abs_housing", "M_absorber_pad")]
    for good, bad in pairs:
        assert SP.role_for_material(good) != SP.DEFAULT_ROLE, \
            "%s（词元形态）应命中" % good
        assert SP.role_for_material(bad) == SP.DEFAULT_ROLE, \
            "%s（包在更长词里）不应命中" % bad


def test_f2_selftest仍为PASS():
    r = SP.selftest()
    fails = {k: v for k, v in r.items() if isinstance(v, dict) and not v.get("ok")}
    assert not fails, fails
    assert r["verdict"] == "PASS"
