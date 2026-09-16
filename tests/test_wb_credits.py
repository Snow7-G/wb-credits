#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""wb-credits 全量测试。

自包含：全部跑在临时构造的假数据上，不读也不写用户的真实数据。
因此在任何机器上结果都一样，CI 里也能直接跑。

直接运行：
    python3 tests/test_wb_credits.py
"""

from __future__ import annotations

import atexit
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import xml.etree.ElementTree

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "plugins", "wb-credits", "skills", "wb-credits", "scripts")
sys.path.insert(0, SCRIPTS)

import data  # noqa: E402
import metrics  # noqa: E402
import render  # noqa: E402

PY = sys.executable
CLI = os.path.join(SCRIPTS, "wb_credits.py")

PASSED = []
FAILED = []


def check(name, condition, detail=""):
    if condition:
        PASSED.append(name)
        print("  ok    %s" % name)
    else:
        FAILED.append((name, detail))
        print("  FAIL  %s  %s" % (name, detail))


# --------------------------------------------------------------------------
# 构造临时数据
# --------------------------------------------------------------------------

SESSIONS_DDL = """
create table sessions (
    id text primary key, cwd text, user_id text, title text,
    custom_title text, status text, created_at integer, updated_at integer,
    model text
)
"""

USAGE_DDL = """
create table session_usage (
    session_id text primary key, used integer, size integer,
    updated_at integer, credit_json text
)
"""


TMP_DIRS = []


def _cleanup_tmp():
    for path in TMP_DIRS:
        shutil.rmtree(path, ignore_errors=True)


atexit.register(_cleanup_tmp)


def make_tmp():
    path = tempfile.mkdtemp(prefix="wbcredits-")
    TMP_DIRS.append(path)
    return path


def make_store_dir(sessions=None, usages=None, sessions_ddl=SESSIONS_DDL,
                   usage_ddl=USAGE_DDL, name="workbuddy.db"):
    tmp = make_tmp()
    con = sqlite3.connect(os.path.join(tmp, name))
    if sessions_ddl:
        con.execute(sessions_ddl)
    if usage_ddl:
        con.execute(usage_ddl)
    for row in sessions or []:
        con.execute("insert into sessions values (?,?,?,?,?,?,?,?,?)", row)
    for row in usages or []:
        con.execute("insert into session_usage values (?,?,?,?,?)", row)
    con.commit()
    con.close()
    return tmp


# --------------------------------------------------------------------------
# 共享固定装置
#
# 整套测试必须能在没有开发机真实数据的环境里跑起来（CI、别人的电脑）。
# 因此造一份内容确定的假数据，默认所有命令行走它。真实数据一次都不碰。
# --------------------------------------------------------------------------

FIXTURE_SESSION = "fixture-session-0001"

_FIXTURE_SESSIONS = [
    ("fixture-session-0001", "/tmp/fixture-alpha", "u", "固定装置会话甲", None,
     "Done", 1700000000000, 1700000003000, "glm-5.3-flash"),
    ("fixture-session-0002", "/tmp/fixture-beta", "u", "固定装置会话乙", None,
     "Done", 1700000001000, 1700000004000, "glm-5.3-flash"),
    ("fixture-session-0003", "/tmp/fixture-gamma", "u", "含关键词的会话", None,
     "Done", 1700000002000, 1700000005000, "glm-5.3-flash"),
]

_FIXTURE_USAGES = [
    ("fixture-session-0001", 12000, 1000000, 1700000003000,
     '{"r1": 1.5, "r2": 2.25, "r3": 0.75, "r4": 12.0}'),
    ("fixture-session-0002", 8000, 1000000, 1700000004000,
     '{"r5": 4.0, "r6": 0.5}'),
    ("fixture-session-0003", 600, 1000000, 1700000005000, '{"r7": 8.0}'),
]

# r3 到 r4 之间留一段长空闲，让异常识别有东西可抓
_FIXTURE_STAMPS = [
    ("r1", 1700000000000),
    ("r2", 1700000000000 + 60 * 1000),
    ("r3", 1700000000000 + 120 * 1000),
    ("r4", 1700000000000 + 120 * 1000 + 95 * 60 * 1000),
]

_FIXTURE_TOKENS = {"input": 400000, "cached": 360000, "output": 9000}

_fixture = None


def fixture_dir():
    """造固定装置，只建一次，返回其配置目录。"""
    global _fixture
    if _fixture is not None:
        return _fixture

    root = make_tmp()
    con = sqlite3.connect(os.path.join(root, "workbuddy.db"))
    con.execute(SESSIONS_DDL)
    con.execute(USAGE_DDL)
    for row in _FIXTURE_SESSIONS:
        con.execute("insert into sessions values (?,?,?,?,?,?,?,?,?)", row)
    for row in _FIXTURE_USAGES:
        con.execute("insert into session_usage values (?,?,?,?,?)", row)
    con.commit()
    con.close()

    log_dir = os.path.join(root, "audit-log")
    os.makedirs(log_dir)
    with open(os.path.join(log_dir, "2026-01-01.jsonl"), "w", encoding="utf-8") as handle:
        for rid, ts in _FIXTURE_STAMPS:
            handle.write(json.dumps({"requestId": rid, "timestamp": ts}) + "\n")

    trace_dir = os.path.join(root, "traces", "1")
    os.makedirs(trace_dir)
    with open(os.path.join(trace_dir, "trace_fixture.json"), "w", encoding="utf-8") as handle:
        json.dump({"trace": {
            "sessionId": FIXTURE_SESSION,
            "modelInfo": {
                "totalInputTokens": _FIXTURE_TOKENS["input"],
                "totalCachedTokens": _FIXTURE_TOKENS["cached"],
                "totalOutputTokens": _FIXTURE_TOKENS["output"],
            },
        }}, handle)

    _fixture = root
    return root


def run_cli(args, config_dir=None, drop_env=(), session=FIXTURE_SESSION):
    """跑一次命令行。

    默认指向固定装置，而不是开发机的真实数据——否则这套测试换个环境就红。
    """
    env = dict(os.environ)
    env["WORKBUDDY_CONFIG_DIR"] = config_dir or fixture_dir()
    env.pop("CODEBUDDY_CONFIG_DIR", None)
    # 清掉开发机上的会话变量，再按需注入，避免继承真实会话
    env.pop("CODEBUDDY_SESSION_ID", None)
    env.pop("CODEBUDDY_CONVERSATION_REQUEST_ID", None)
    if session:
        env["CODEBUDDY_SESSION_ID"] = session
    for key in drop_env:
        env.pop(key, None)
    return subprocess.run(
        [PY, CLI] + list(args), capture_output=True, text=True, env=env
    )


# --------------------------------------------------------------------------
# 1. 静态检查
# --------------------------------------------------------------------------

def test_static():
    print("\n[1] 静态检查")

    import py_compile

    ok = True
    for name in ("data.py", "metrics.py", "render.py", "wb_credits.py"):
        try:
            py_compile.compile(os.path.join(SCRIPTS, name), doraise=True)
        except py_compile.PyCompileError as exc:
            ok = False
            check("语法 %s" % name, False, str(exc))
    if ok:
        check("四个脚本语法均可编译", True)

    for rel in (".codebuddy-plugin/marketplace.json",
                "plugins/wb-credits/.codebuddy-plugin/plugin.json"):
        path = os.path.join(ROOT, rel)
        try:
            with open(path, encoding="utf-8") as handle:
                json.load(handle)
            check("JSON 合法 %s" % rel, True)
        except Exception as exc:  # noqa: BLE001
            check("JSON 合法 %s" % rel, False, str(exc))

    manifest_path = os.path.join(ROOT, "plugins/wb-credits/.codebuddy-plugin/plugin.json")
    with open(manifest_path, encoding="utf-8") as handle:
        manifest = json.load(handle)
    name = manifest.get("name", "")
    valid = name and name[0].isalpha() and name == name.lower() and " " not in name
    check("插件名为合法 kebab-case", bool(valid), repr(name))

    market_path = os.path.join(ROOT, ".codebuddy-plugin/marketplace.json")
    with open(market_path, encoding="utf-8") as handle:
        market = json.load(handle)
    check("市场清单含 name/owner/plugins",
          all(k in market for k in ("name", "owner", "plugins")))
    entry = market["plugins"][0]
    check("市场条目含 name/source/description",
          all(k in entry for k in ("name", "source", "description")))
    check("插件 source 为相对路径且以 ./ 开头",
          isinstance(entry["source"], str) and entry["source"].startswith("./"),
          repr(entry.get("source")))
    target = os.path.normpath(os.path.join(ROOT, entry["source"]))
    check("source 指向的目录真实存在", os.path.isdir(target), target)
    check("source 目录含 plugin.json",
          os.path.isfile(os.path.join(target, ".codebuddy-plugin", "plugin.json")))

    for rel in ("commands/credits.md", "skills/wb-credits/SKILL.md"):
        path = os.path.join(target, rel)
        check("组件存在 %s" % rel, os.path.isfile(path), path)

    # README 里引用的图必须真的存在，且是合法 SVG。
    # 图挂掉在网页上只是显示不出来，不会报错——正是最容易被忽略的那种坏。
    readmes = ["README.md", "README.en.md"]
    all_refs = []
    for name in readmes:
        path = os.path.join(ROOT, name)
        check("%s 存在" % name, os.path.isfile(path), path)
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as handle:
            body = handle.read()
        refs = re.findall(r'srcset="([^"]+)"|src="([^"]+)"', body)
        refs = [a or b for a, b in refs]
        refs = [r for r in refs if not r.startswith("http")]
        check("%s 引用了卡片图" % name, len(refs) >= 2, str(refs))
        for rel in refs:
            # 别用 target 这个名字，下面还要指插件目录
            image_path = os.path.join(ROOT, rel)
            exists = os.path.isfile(image_path)
            check("%s 引用的图存在 %s" % (name, rel), exists, image_path)
            if not exists:
                continue
            try:
                xml.etree.ElementTree.parse(image_path)
                check("图是合法 SVG %s" % rel, True)
            except xml.etree.ElementTree.ParseError as exc:
                check("图是合法 SVG %s" % rel, False, str(exc))
        all_refs.append(refs)

    # 两份 README 必须互链，语言切换不能断
    def linked(name, other):
        if not os.path.isfile(os.path.join(ROOT, name)):
            return False
        with open(os.path.join(ROOT, name), encoding="utf-8") as handle:
            return other in handle.read()

    check("中文版链到英文版", linked("README.md", "README.en.md"))
    check("英文版链到中文版", linked("README.en.md", "README.md"))

    # 两份 README 用同一组图，避免一版改了另一版没跟上
    if len(all_refs) == 2:
        check("两份 README 引用同一组图", set(all_refs[0]) == set(all_refs[1]),
              "%s vs %s" % (all_refs[0], all_refs[1]))

    # 两版图的尺寸必须一致，否则切换主题时卡片会跳一下
    sizes = []
    for rel in all_refs[0] if all_refs else []:
        path = os.path.join(ROOT, rel)
        if os.path.isfile(path):
            root = xml.etree.ElementTree.parse(path).getroot()
            sizes.append((root.get("width"), root.get("height")))
    check("浅色与深色图尺寸一致", len(set(sizes)) <= 1, str(sizes))

    # 图必须与生成脚本同步。用 --check 校验，不写文件——
    # 测试不该改动工作区。
    gen = os.path.join(ROOT, "docs", "make_card.py")
    check("卡片图生成脚本存在", os.path.isfile(gen), gen)
    if os.path.isfile(gen):
        result = subprocess.run([PY, gen, "--check"],
                                capture_output=True, text=True, cwd=ROOT)
        check("卡片图与生成脚本一致", result.returncode == 0,
              (result.stdout + result.stderr).strip()[:160])

    with open(os.path.join(target, "commands/credits.md"), encoding="utf-8") as handle:
        body = handle.read()
    check("命令文件含 frontmatter", body.startswith("---"))
    check("命令文件含 description", "description:" in body.split("---")[1])
    check("命令文件含渲染硬要求", "必须调用可视化渲染工具" in body)

    with open(os.path.join(target, "skills/wb-credits/SKILL.md"), encoding="utf-8") as handle:
        skill = handle.read()
    check("技能文件含 frontmatter", skill.startswith("---"))
    check("技能文件含 name/description",
          "name:" in skill.split("---")[1] and "description:" in skill.split("---")[1])

    check("脚本目录无第三方依赖",
          "import requests" not in open(os.path.join(SCRIPTS, "data.py"), encoding="utf-8").read())


# --------------------------------------------------------------------------
# 2. 数据层：正常路径
# --------------------------------------------------------------------------

def test_data_normal():
    print("\n[2] 数据层 · 正常路径")

    store = data.Store(config_dir=fixture_dir())
    try:
        sessions = store.sessions()
        check("能读到会话表", isinstance(sessions, dict) and len(sessions) > 0,
              "共 %d 个" % len(sessions))

        usages = store.all_usage()
        check("能读到用量表", len(usages) > 0, "共 %d 条" % len(usages))

        sample = max(usages, key=lambda u: sum(u["credits"].values()))
        check("积分字典非空", len(sample["credits"]) > 0)
        check("积分值均为浮点",
              all(isinstance(v, float) for v in sample["credits"].values()))

        stamps = store.timestamps()
        check("能读到审计日志时间戳", len(stamps) > 0, "共 %d 条" % len(stamps))

        meta = sessions.get(sample["session_id"])
        check("用量能关联到会话元信息", meta is not None)
    finally:
        store.close()

    check("只读连接未产生写文件",
          not os.path.exists(os.path.join(store.config_dir, "workbuddy.db-journal")))


# --------------------------------------------------------------------------
# 3. 数据层：边界与错误
# --------------------------------------------------------------------------

def test_data_errors():
    print("\n[3] 数据层 · 边界与错误")

    empty = make_tmp()
    try:
        data.Store(config_dir=empty)
        check("空目录应抛 DataError", False, "未抛异常")
    except data.DataError as exc:
        check("空目录抛 DataError", True)
        check("错误信息为人话且含路径提示",
              "找不到数据库文件" in str(exc), str(exc)[:60])
    except Exception as exc:  # noqa: BLE001
        check("空目录抛 DataError", False, "抛了 %s" % type(exc).__name__)

    only_sessions = make_store_dir(usage_ddl=None)
    try:
        data.Store(config_dir=only_sessions).sessions()
        check("缺表应抛 DataError", False, "未抛异常")
    except data.DataError as exc:
        check("缺表抛 DataError", True)
        check("缺表错误点名了 session_usage", "session_usage" in str(exc), str(exc)[:80])

    bad_ddl = """
    create table sessions (
        id text primary key, cwd text, user_id text, title text,
        custom_title text, status text, created_at integer, updated_at integer
    )
    """
    missing_col = make_store_dir(sessions_ddl=bad_ddl)
    try:
        data.Store(config_dir=missing_col).sessions()
        check("缺字段应抛 DataError", False, "未抛异常")
    except data.DataError as exc:
        check("缺字段抛 DataError", True)
        check("缺字段错误点名了 model", "model" in str(exc), str(exc)[:80])

    broken = make_store_dir(
        sessions=[("s1", "/tmp", "u", "坏数据会话", None, "Done", 0, 0, "m")],
        usages=[("s1", 100, 1000, 0, "{not json")],
    )
    try:
        store = data.Store(config_dir=broken)
        usage = store.usage("s1")
        check("credit_json 损坏时不抛异常", usage is not None)
        check("损坏数据被标记 partial", usage["partial"] is True)
        check("损坏数据降级为空积分", usage["credits"] == {})
        store.close()
    except Exception as exc:  # noqa: BLE001
        check("credit_json 损坏时不抛异常", False, "%s" % exc)

    wrong_type = make_store_dir(
        sessions=[("s1", "/tmp", "u", "类型异常", None, "Done", 0, 0, "m")],
        usages=[("s1", 100, 1000, 0, '{"a": "not-a-number", "b": 2.5}')],
    )
    store = data.Store(config_dir=wrong_type)
    usage = store.usage("s1")
    check("非数字积分被丢弃", "a" not in usage["credits"])
    check("合法积分被保留", usage["credits"].get("b") == 2.5)
    check("类型异常标记 partial", usage["partial"] is True)
    store.close()

    empty_usage = make_store_dir(
        sessions=[("s1", "/tmp", "u", "空用量", None, "Done", 0, 0, "m")],
        usages=[("s1", 0, 1000, 0, None)],
    )
    store = data.Store(config_dir=empty_usage)
    usage = store.usage("s1")
    check("credit_json 为空时返回空字典", usage["credits"] == {})
    check("空用量不算 partial", usage["partial"] is False)
    store.close()

    store = data.Store(config_dir=empty_usage)
    check("查询不存在的会话返回 None", store.usage("nope") is None)
    store.close()


# --------------------------------------------------------------------------
# 4. 计算层
# --------------------------------------------------------------------------

def test_metrics():
    print("\n[4] 计算层")

    credits = {"r1": 3.0, "r2": 2.0, "r3": 1.0}
    summary = metrics.summarize(credits, used=12345, size=1000000)
    check("累计积分正确", abs(summary["total"] - 6.0) < 1e-9, str(summary["total"]))
    check("轮次正确", summary["rounds"] == 3)
    check("最近三轮取全部", summary["recent"] == [3.0, 2.0, 1.0])
    check("边际成本为均值", abs(summary["marginal"] - 2.0) < 1e-9)
    check("预估值 = 边际 × 10", abs(summary["forecast"]["credits"] - 20.0) < 1e-9)
    check("上下文格式化为万", metrics.fmt_context(12345) == "1 万")

    big = {"r%d" % i: 1.0 for i in range(10)}
    summary = metrics.summarize(big)
    check("最近三轮只取尾部", summary["recent"] == [1.0, 1.0, 1.0])
    check("十轮合计正确", abs(summary["total"] - 10.0) < 1e-9)

    in_flight = {"r1": 5.0, "r2": 4.0, "r3": 3.0, "r4": 0.5}
    summary = metrics.summarize(in_flight, current_request_id="r4")
    check("在进行中的轮次不计入边际成本",
          abs(summary["marginal"] - 4.0) < 1e-9, str(summary["marginal"]))
    check("标记了 in_flight", summary["in_flight"] is True)
    check("在进行中的轮次仍计入累计",
          abs(summary["total"] - 12.5) < 1e-9, str(summary["total"]))

    summary = metrics.summarize({})
    check("空数据不崩", summary["total"] == 0.0 and summary["rounds"] == 0)
    check("空数据边际成本为 None", summary["marginal"] is None)
    check("空数据预估为 None", summary["forecast"]["credits"] is None)

    only_in_flight = metrics.summarize({"r1": 0.95}, current_request_id="r1")
    check("仅当前轮时边际成本为 None", only_in_flight["marginal"] is None)
    check("仅当前轮时预估为 None", only_in_flight["forecast"]["credits"] is None)
    check("仅当前轮时预估基数为 0", only_in_flight["forecast"]["basis"] == 0)

    mixed = metrics.summarize(
        {"r1": 1.0, "r2": 2.0, "r3": 3.0, "r4": 0.5}, current_request_id="r4"
    )
    check("预估基数为已结算轮数", mixed["forecast"]["basis"] == 3, str(mixed["forecast"]))
    check("空数据异常提示为空", summary["anomaly"] is None)

    check("时间格式化为 HH:MM", metrics.fmt_clock(1789540180559).count(":") == 1)
    check("间隔格式化含 h",
          metrics.fmt_gap(2 * 3600 * 1000 + 19 * 60 * 1000) == "2h19m")
    check("一小时以内显示分钟", metrics.fmt_gap(45 * 60 * 1000) == "45m")
    check("小于一万不带万", metrics.fmt_context(9999) == "9999")


def test_anomaly():
    print("\n[5] 异常识别")

    base = 1789540000000
    gap = 2 * 3600 * 1000 + 19 * 60 * 1000
    items = [("a", 0.5), ("b", 0.6), ("c", 0.7), ("d", 0.8), ("e", 5.18)]
    stamps = {
        "a": base,
        "b": base + 1000,
        "c": base + 2000,
        "d": base + 3000,
        "e": base + 3000 + gap,
    }
    hint = metrics.find_anomaly(items, stamps)
    check("能识别闲置后变贵的轮次", hint is not None and "闲置" in hint, str(hint))
    check("提示含具体数值", hint is not None and "5.18" in hint, str(hint))
    check("提示含小时分钟", hint is not None and "2h19m" in hint, str(hint))

    dense = dict(stamps)
    dense["e"] = base + 4000
    check("间隔不足 30 分钟不提示",
          metrics.find_anomaly(items, dense) is None)

    flat = [("a", 1.0), ("b", 1.0), ("c", 1.0)]
    flat_stamps = {"a": base, "b": base + 1000, "c": base + 1000 + gap}
    check("无突增时不提示", metrics.find_anomaly(flat, flat_stamps) is None)

    check("少于三轮不提示",
          metrics.find_anomaly([("a", 1.0), ("b", 9.0)], {"a": base, "b": base + gap}) is None)
    check("无时间戳不提示", metrics.find_anomaly(items, {}) is None)

    zero = [("a", 0.0), ("b", 0.0), ("c", 5.0)]
    zero_stamps = {"a": base, "b": base + 1, "c": base + 1 + gap}
    check("中位数为 0 时不误报",
          metrics.find_anomaly(zero, zero_stamps) is None)

    no_stamp_items = [("a", 0.5), ("b", 0.6), ("c", 5.0)]
    check("峰值缺时间戳时不提示",
          metrics.find_anomaly(no_stamp_items, {"a": base, "b": base + 1}) is None)


# --------------------------------------------------------------------------
# 6. 呈现层
# --------------------------------------------------------------------------

def test_render():
    print("\n[6] 呈现层")

    summary = metrics.summarize(
        {"r1": 1.0, "r2": 2.0, "r3": 3.0}, used=260000, size=1000000
    )
    card = render.card(summary)

    check("卡片含累计值", "6.00" in card)
    check("卡片含分隔符 ｜", "｜" in card)
    check("卡片不使用 · 作为分隔", "·" not in card)
    check("卡片走主题变量", "--color-background-secondary" in card
          and "--color-text-primary" in card)
    check("卡片无硬编码色值", "#" not in card)
    check("卡片无渐变与阴影",
          "gradient" not in card and "box-shadow" not in card)
    check("卡片用 grid 两栏", "grid-template-columns" in card)
    check("卡片含 minmax 防溢出", "minmax(0,1fr)" in card)
    check("卡片字号均不小于 11px",
          all(int(s.replace("px", "")) >= 11
              for s in _extract_font_sizes(card)),
          str(_extract_font_sizes(card)))

    plain = render.text(summary)
    check("纯文本含累计值", "6.00" in plain)
    check("纯文本含最近三轮", "1.00" in plain and "3.00" in plain)
    check("纯文本两栏对齐（首行含右侧标签）", "最近三轮" in plain.splitlines()[0])
    check("纯文本不使用 · 作为分隔", "·" not in plain)

    empty = render.text(metrics.summarize({}))
    check("空数据纯文本不崩", "0.00" in empty)

    thin = metrics.summarize({"r1": 0.95}, used=40000, current_request_id="r1")
    thin_card = render.card(thin)
    check("数据不足时卡片不出现「约 0 积分」", "约 0 积分" not in thin_card)
    check("数据不足时卡片说明待结算", "待首轮结算" in thin_card)
    check("数据不足时卡片仍显示累计值", "0.95" in thin_card)
    check("数据不足时不报 in_flight 数值错乱", "0.95 未定稿" in thin_card)

    thin_text = render.text(thin)
    check("数据不足时纯文本不出现「约 0 积分」", "约 0 积分" not in thin_text)
    check("数据不足时纯文本说明待结算", "待首轮结算" in thin_text)

    check("排行空列表给人话", "没有找到" in render.rank_text([]))
    check("明细空列表给人话", "没有用量记录" in render.detail_text([]))

    rows = [{"title": "中文标题测试", "rounds": 3, "used": 12000, "total": 9.5}]
    text = render.rank_text(rows)
    check("排行文本可渲染", "中文标题测试" in text and "9.50" in text)

    wide = render.rank_text([{"title": "超长" * 30, "rounds": 1, "used": 100, "total": 1.0}])
    check("超长标题不崩", isinstance(wide, str) and len(wide) > 0)


def _extract_font_sizes(html):
    sizes = []
    cursor = 0
    while True:
        found = html.find("font-size:", cursor)
        if found == -1:
            return sizes
        start = found + len("font-size:")
        end = html.find(";", start)
        sizes.append(html[start:end].strip())
        cursor = end


# --------------------------------------------------------------------------
# 7. 命令行
# --------------------------------------------------------------------------

def test_cli():
    print("\n[7] 命令行")

    real = subprocess.run([PY, CLI, "--version"], capture_output=True, text=True)
    check("--version 可用", real.returncode == 0 and "wb-credits" in real.stdout)

    real = subprocess.run([PY, CLI, "--help"], capture_output=True, text=True)
    check("--help 可用", real.returncode == 0 and "--all" in real.stdout)

    result = run_cli(["--format", "text"])
    check("默认本对话 text 输出成功", result.returncode == 0, result.stderr[:120])
    check("本对话输出含标签", "本对话" in result.stdout)
    check("本对话输出含最近三轮", "最近三轮" in result.stdout)

    result = run_cli(["--format", "card"])
    check("card 格式输出 HTML", result.returncode == 0 and "<div" in result.stdout)
    check("card 格式不含其他内容", "ok" not in result.stdout)

    result = run_cli([])
    check("默认 json 格式可解析", result.returncode == 0)
    try:
        payload = json.loads(result.stdout)
        check("json 含 ok/kind/summary/card",
              all(k in payload for k in ("ok", "kind", "summary", "card")))
        check("json 的 kind 为 session", payload.get("kind") == "session")
        check("json 的 card 为字符串", isinstance(payload.get("card"), str))
    except ValueError as exc:
        check("默认 json 格式可解析", False, str(exc))

    result = run_cli(["--all", "--format", "json", "--limit", "3"])
    check("--all 输出成功", result.returncode == 0, result.stderr[:120])
    payload = json.loads(result.stdout)
    check("排行条目数受限", len(payload["rows"]) <= 3, str(len(payload["rows"])))
    check("排行按积分降序",
          all(payload["rows"][i]["total"] >= payload["rows"][i + 1]["total"]
              for i in range(len(payload["rows"]) - 1)))

    # 固定装置有 3 个会话。取 2 条才会出现「共 N 个」那行，顺带把截断提示也覆盖上。
    result = run_cli(["--all", "--format", "text", "--limit", "2"])
    check("排行文本输出成功", result.returncode == 0 and "共" in result.stdout,
          result.stdout[:80])
    check("排行被截断时说明总数", "共 3 个会话" in result.stdout, result.stdout[:80])

    result = run_cli(["--all", "-k", "不存在的关键词zzz", "--format", "json"])
    payload = json.loads(result.stdout)
    check("关键词无匹配时返回空列表", payload["rows"] == [])
    check("无匹配时文本给人话",
          "没有找到" in run_cli(["--all", "-k", "zzz不存在", "--format", "text"]).stdout)

    result = run_cli(["--detail", "--format", "json"])
    payload = json.loads(result.stdout)
    check("--detail 返回明细", "detail" in payload and len(payload["detail"]) > 0)

    result = run_cli(["--detail", "--format", "text"])
    check("--detail 文本含明细", "#1" in result.stdout)


def test_cli_errors():
    print("\n[8] 命令行 · 错误处理")

    result = run_cli(["--session", "deadbeef-0000", "--format", "text"])
    check("不存在的会话退出码为 1", result.returncode == 1)
    check("不存在的会话给人话", "还没有用量记录" in result.stderr, result.stderr[:80])
    check("错误不走 stdout", result.stdout.strip() == "")

    result = run_cli(["--session", "deadbeef-0000"])
    check("错误 json 格式可解析", result.returncode == 1)
    payload = json.loads(result.stdout)
    check("错误 json 含 ok=false", payload.get("ok") is False)
    check("错误 json 含 error 字段", bool(payload.get("error")))

    result = run_cli([], drop_env=("CODEBUDDY_SESSION_ID",))
    check("无会话 ID 时退出码为 1", result.returncode == 1)
    payload = json.loads(result.stdout)
    check("无会话 ID 时提示用 --session",
          "session" in payload.get("error", ""), payload.get("error", "")[:80])

    empty = make_tmp()
    result = run_cli([], config_dir=empty)
    check("数据目录无效时退出码为 1", result.returncode == 1)
    payload = json.loads(result.stdout)
    message = payload.get("error", "")
    check("数据目录无效时给人话且不含堆栈",
          "workbuddy.db" in message and "Traceback" not in message, message[:80])
    check("显式指定的无效目录不回退到默认目录",
          "WORKBUDDY_CONFIG_DIR" in message, message[:80])

    result = run_cli([], drop_env=("CODEBUDDY_SESSION_ID", "CODEBUDDY_CONVERSATION_REQUEST_ID"))
    check("缺少全部环境变量时仍能给出人话提示",
          result.returncode == 1 and "error" in result.stdout)


# --------------------------------------------------------------------------
# 9. 端到端一致性
# --------------------------------------------------------------------------

def test_cross_check():
    print("\n[9] 端到端一致性")

    store = data.Store(config_dir=fixture_dir())
    session_id = FIXTURE_SESSION

    usage = store.usage(session_id)
    expected = round(sum(usage["credits"].values()), 2)
    store.close()

    result = run_cli(["--session", session_id, "--format", "json"])
    payload = json.loads(result.stdout)
    actual = round(payload["summary"]["total"], 2)
    check("CLI 累计值与直接查库一致", abs(actual - expected) < 0.011,
          "CLI %.2f vs 直查 %.2f" % (actual, expected))
    check("CLI 轮次数与直查一致",
          payload["summary"]["rounds"] == len(usage["credits"]))

    text_out = run_cli(["--session", session_id, "--format", "text"]).stdout
    card_out = run_cli(["--session", session_id, "--format", "card"]).stdout
    check("text 与 json 数值一致", ("%.2f" % expected) in text_out)
    check("card 与 json 数值一致", ("%.2f" % expected) in card_out)


# --------------------------------------------------------------------------
# 12. token 拆分
# --------------------------------------------------------------------------

def test_tokens():
    print("\n[12] token 拆分")

    check("小数值原样显示", metrics.fmt_tokens(9999) == "9999")
    check("上万显示为万", metrics.fmt_tokens(12345) == "1 万", metrics.fmt_tokens(12345))
    check("过亿显示为亿", metrics.fmt_tokens(123456789) == "1.2 亿", metrics.fmt_tokens(123456789))

    raw = {"input": 1000, "cached": 900, "uncached": 100, "output": 50, "traces": 3}
    ts = metrics.token_summary(raw)
    check("token_summary 保留各项", ts["input"] == 1000 and ts["output"] == 50)
    check("缓存比例计算正确", abs(ts["cached_ratio"] - 0.9) < 1e-9)
    check("空输入返回 None", metrics.token_summary(None) is None)
    check("input 为 0 时比例给 None 而非 0",
          metrics.token_summary({"input": 0})["cached_ratio"] is None)

    check("比例格式化为百分比", metrics.fmt_ratio(0.974) == "97%")
    check("零比例显示 0%", metrics.fmt_ratio(0.0) == "0%")
    check("全命中显示 100%", metrics.fmt_ratio(1.0) == "100%")
    check("比例为 None 时给破折号", metrics.fmt_ratio(None) == "—")

    est = metrics.estimate_credits(ts, 100.0)
    check("估算三项之和等于总积分", abs(sum(est.values()) - 100.0) < 0.01, str(est))
    check("估算三项均为正", all(v > 0 for v in est.values()))
    check("全零 token 时估算为 None",
          metrics.estimate_credits({"uncached": 0, "cached": 0, "output": 0}, 10.0) is None)

    tmp = make_store_dir(
        sessions=[("s1", "/tmp", "u", "token 测试", None, "Done", 0, 0, "m")],
        usages=[("s1", 0, 1000, 0, '{"a": 1.0}')],
    )
    tdir = os.path.join(tmp, "traces", "12345")
    os.makedirs(tdir, exist_ok=True)

    def write_trace(name, body):
        with open(os.path.join(tdir, name), "w", encoding="utf-8") as handle:
            json.dump({"trace": body}, handle)

    def model_info(sid, inp, cached, out):
        return {"sessionId": sid, "modelInfo": {
            "totalInputTokens": inp, "totalCachedTokens": cached, "totalOutputTokens": out}}

    write_trace("trace_1.json", model_info("s1", 1000, 900, 50))
    write_trace("trace_2.json", model_info("s1", 500, 400, 20))
    write_trace("trace_other.json", model_info("s2", 9999, 0, 9999))
    write_trace("trace_noinfo.json", {"sessionId": "s1"})
    with open(os.path.join(tdir, "trace_broken.json"), "w", encoding="utf-8") as handle:
        handle.write("{ 不是 JSON")
    with open(os.path.join(tdir, "trace_junk.json"), "w", encoding="utf-8") as handle:
        handle.write("[1, 2, 3]")

    store = data.Store(config_dir=tmp)
    agg = store.trace_tokens()
    check("按会话聚合", "s1" in agg and "s2" in agg)
    check("input 求和正确", agg["s1"]["input"] == 1500, str(agg.get("s1")))
    check("cached 求和正确", agg["s1"]["cached"] == 1300)
    check("uncached 为差值", agg["s1"]["uncached"] == 200)
    check("output 求和正确", agg["s1"]["output"] == 70)
    check("只统计含 modelInfo 的 trace", agg["s1"]["traces"] == 2, str(agg["s1"]["traces"]))
    store.close()

    tmp2 = make_store_dir(
        sessions=[("s1", "/tmp", "u", "x", None, "Done", 0, 0, "m")],
        usages=[("s1", 0, 1000, 0, '{"a": 1.0}')],
    )
    d2 = os.path.join(tmp2, "traces", "1")
    os.makedirs(d2, exist_ok=True)
    with open(os.path.join(d2, "trace_x.json"), "w", encoding="utf-8") as handle:
        json.dump({"trace": {"sessionId": "s1", "modelInfo": {
            "totalInputTokens": 100, "totalCachedTokens": 999,
            "totalOutputTokens": 5}}}, handle)
    store = data.Store(config_dir=tmp2)
    agg = store.trace_tokens()
    check("cached 超过 input 时被截断", agg["s1"]["cached"] == 100, str(agg.get("s1")))
    check("截断后 uncached 不为负", agg["s1"]["uncached"] == 0)
    store.close()

    # 头部读取只对「字段位置稳定」成立。以下三种情况必须仍然正确。
    tmp4 = make_store_dir(
        sessions=[("s1", "/tmp", "u", "x", None, "Done", 0, 0, "m")],
        usages=[("s1", 0, 1000, 0, '{"a": 1.0}')],
    )
    d4 = os.path.join(tmp4, "traces", "1")
    os.makedirs(d4, exist_ok=True)

    # (a) 字段被挤到 64KB 之后：必须靠读全文兜住，不能静默漏掉
    deep = {"trace": {"metadata": {"blob": "x" * 80000},
                      "sessionId": "s1",
                      "modelInfo": {"totalInputTokens": 700,
                                    "totalCachedTokens": 600,
                                    "totalOutputTokens": 10}}}
    with open(os.path.join(d4, "trace_deep.json"), "w", encoding="utf-8") as handle:
        json.dump(deep, handle)

    # (b) token 字段只出现在 spans 里：不能算进 modelInfo 的账
    span_only = {"trace": {"sessionId": "s1", "modelInfo": {"models": ["m"]},
                           "spans": [{"name": "llm_call",
                                      "totalInputTokens": 999999,
                                      "totalCachedTokens": 999999,
                                      "totalOutputTokens": 999999}]}}
    with open(os.path.join(d4, "trace_span.json"), "w", encoding="utf-8") as handle:
        json.dump(span_only, handle)

    # (c) 字段顺序调换：靠正则逐字段取，不应依赖先后
    reordered = {"trace": {"modelInfo": {"totalOutputTokens": 7,
                                         "totalCachedTokens": 20,
                                         "totalInputTokens": 100},
                           "sessionId": "s1"}}
    with open(os.path.join(d4, "trace_reorder.json"), "w", encoding="utf-8") as handle:
        json.dump(reordered, handle)

    store = data.Store(config_dir=tmp4)
    agg = store.trace_tokens()
    store.close()
    got = agg.get("s1", {})
    check("字段越过头部时仍能读到", got.get("input") == 800, str(got))
    check("只统计有 token 字段的 trace", got.get("traces") == 2, str(got.get("traces")))
    check("spans 里的同名字段不被计入", got.get("input") != 1000799, str(got))
    check("字段顺序调换不影响结果", got.get("output") == 17, str(got))
    check("modelInfo 无 token 字段的 trace 被跳过",
          got.get("input") != 0 and "models" not in str(got))

    # modelInfo 对象恰好跨过头部边界：只读头部会拿到半截 JSON。
    # 这不是理论问题——实测构造出来时会整条 trace 被静默丢掉。
    tmp5 = make_store_dir(
        sessions=[("s1", "/tmp", "u", "x", None, "Done", 0, 0, "m")],
        usages=[("s1", 0, 1000, 0, '{"a": 1.0}')],
    )
    d5 = os.path.join(tmp5, "traces", "1")
    os.makedirs(d5, exist_ok=True)
    head_bytes = data.TRACE_HEAD_BYTES
    pad = head_bytes - 86          # 让 modelInfo 的 { 落在头部内、} 落在头部外
    body = ('{"trace":{"sessionId":"s1","metadata":{"pad":"' + "x" * pad + '"},'
            '"modelInfo":{"totalInputTokens":900,"totalCachedTokens":800,'
            '"totalOutputTokens":7}}}')
    start = body.find('"modelInfo"')
    end = body.rfind("}")
    with open(os.path.join(d5, "trace_straddle.json"), "w", encoding="utf-8") as handle:
        handle.write(body)

    store = data.Store(config_dir=tmp5)
    agg = store.trace_tokens()
    store.close()
    got5 = agg.get("s1", {})
    check("构造样例确实跨过了头部边界",
          start < head_bytes < end, "起点 %d 终点 %d 头部 %d" % (start, end, head_bytes))
    check("对象跨头部边界时不丢 trace", got5.get("input") == 900, str(got5))
    check("跨边界时数值完整", got5.get("cached") == 800 and got5.get("output") == 7, str(got5))

    tmp3 = make_store_dir(
        sessions=[("s1", "/tmp", "u", "x", None, "Done", 0, 0, "m")],
        usages=[("s1", 0, 1000, 0, '{"a": 1.0}')],
    )
    store = data.Store(config_dir=tmp3)
    check("无 traces 目录返回空字典", store.trace_tokens() == {})
    store.close()

    base = metrics.summarize({"a": 1.0, "b": 2.0, "c": 3.0}, used=1000)
    check("无 token 时卡片不含 token 块", "未缓存输入" not in render.card(base))
    check("无 token 时纯文本不含 token 行", "未缓存输入" not in render.text(base))

    base["tokens"] = metrics.token_summary(raw)
    card = render.card(base)
    check("卡片含三项标签",
          "未缓存输入" in card and "缓存命中" in card and "输出" in card)
    check("卡片不含「思考」字样", "思考" not in card)
    check("纯文本含 token 行", "未缓存输入" in render.text(base))

    base["tokens"]["estimate"] = metrics.estimate_credits(base["tokens"], 6.0)
    check("带估算时卡片出现估算行", "按比例估算" in render.card(base))
    check("带估算时纯文本出现估算行", "按比例估算" in render.text(base))


def test_cli_tokens():
    print("\n[13] 命令行 · token 开关")

    payload = json.loads(run_cli(["--format", "json"]).stdout)
    check("默认返回 tokens 字段", "tokens" in payload["summary"])
    check("默认的 tokens 含缓存比例",
          (payload["summary"].get("tokens") or {}).get("cached_ratio") is not None)

    off = json.loads(run_cli(["--format", "json", "--no-tokens"]).stdout)
    check("--no-tokens 时不返回 tokens 字段", "tokens" not in off["summary"])

    result = run_cli(["--format", "json", "--tokens"])
    check("--tokens 执行成功", result.returncode == 0, result.stderr[:100])
    payload = json.loads(result.stdout)
    check("--tokens 后含 tokens 字段", "tokens" in payload["summary"])
    tokens = payload["summary"].get("tokens")
    if tokens:
        check("token 含三项", all(k in tokens for k in ("uncached", "cached", "output")))
        check("token 值为非负整数",
              all(isinstance(tokens[k], int) and tokens[k] >= 0
                  for k in ("uncached", "cached", "output")), str(tokens))

    payload = json.loads(run_cli(["--format", "json", "--tokens", "--estimate"]).stdout)
    est = payload["summary"].get("tokens", {}).get("estimate")
    check("--estimate 产出估算", est is not None)
    if est:
        total = payload["summary"]["total"]
        check("估算之和接近总积分",
              abs(sum(est.values()) - total) < max(0.5, total * 0.01),
              "%s vs %.2f" % (est, total))

    check("text 模式含 token 行",
          "未缓存输入" in run_cli(["--format", "text", "--tokens"]).stdout)
    check("card 模式含 token 块",
          "未缓存输入" in run_cli(["--format", "card", "--tokens"]).stdout)

    payload = json.loads(run_cli(["--format", "json", "--estimate"]).stdout)
    check("--estimate 单独用时也产出估算",
          "estimate" in (payload["summary"].get("tokens") or {}))

    result = run_cli(["--all", "--format", "json", "--tokens"])
    check("--all 与 --tokens 同用不报错", result.returncode == 0, result.stderr[:100])

    # 缓存命中率要出现在主数字下方那一行，而不是埋在 token 明细里
    card = run_cli(["--format", "card"]).stdout
    meta = run_cli(["--format", "text"]).stdout
    check("卡片顶部含缓存命中率", "缓存命中 " in card and "%" in card)
    check("纯文本顶部含缓存命中率", "缓存命中 " in meta and "%" in meta)
    check("卡片顶部行在 token 明细之前",
          card.find("上下文") < card.find("未缓存输入"), "顶部行未前置")
    check("关掉 token 后顶部行不再出现比例",
          "缓存命中 " not in run_cli(["--format", "card", "--no-tokens"]).stdout)

    # 明细损坏时总数偏低。不说出来，用户会把偏低的数当成完整的。
    broken = make_store_dir(
        sessions=[("s1", "/tmp", "u", "损坏明细", None, "Done", 0, 0, "m")],
        usages=[("s1", 0, 1000, 0, '{"a": 1.0, "b": ')],
    )
    result = run_cli(["--session", "s1", "--format", "json"], config_dir=broken)
    payload = json.loads(result.stdout)
    check("损坏明细被标记 partial", payload["partial"] is True, str(payload["partial"]))
    check("partial 传到 summary", payload["summary"].get("partial") is True)

    card = run_cli(["--session", "s1", "--format", "card"], config_dir=broken).stdout
    check("卡片标出数据不完整", "损坏" in card, card[:120])
    text_out = run_cli(["--session", "s1", "--format", "text"], config_dir=broken).stdout
    check("纯文本标出数据不完整", "损坏" in text_out, text_out[:120])

    # 正常数据不该出现这行
    ok_card = run_cli(["--format", "card"]).stdout
    check("数据正常时不出现不完整提示", "损坏" not in ok_card)


# --------------------------------------------------------------------------
# 10. 健壮性
# --------------------------------------------------------------------------

def test_robustness():
    print("\n[10] 健壮性")

    summary = metrics.summarize({"a": 0.1, "b": 0.2})
    check("浮点求和无长尾数", summary["total"] == 0.3, str(summary["total"]))

    tmp = make_store_dir(
        sessions=[("s1", "/tmp", "u", "时间戳异常", None, "Done", 0, 0, "m")],
        usages=[("s1", 0, 1000, 0, '{"a": 1.0}')],
    )
    log_dir = os.path.join(tmp, "audit-log")
    os.makedirs(log_dir, exist_ok=True)
    with open(os.path.join(log_dir, "2026-01-01.jsonl"), "w", encoding="utf-8") as handle:
        handle.write('{"requestId": "a", "timestamp": "1789540180559"}\n')
        handle.write('{"requestId": "c", "timestamp": true}\n')
        handle.write('{"requestId": "d", "timestamp": 0}\n')
        handle.write('{"requestId": "e", "timestamp": 1789540180559}\n')
        handle.write("这一行不是 JSON\n")
        handle.write('{"没有requestId": 1}\n')
        handle.write("\n")

    store = data.Store(config_dir=tmp)
    stamps = store.timestamps()
    check("字符串时间戳被忽略", "a" not in stamps)
    check("布尔时间戳被忽略", "c" not in stamps)
    check("零时间戳被忽略", "d" not in stamps)
    check("合法时间戳被保留", stamps.get("e") == 1789540180559)
    check("损坏行与缺字段行被跳过", len(stamps) == 1, str(stamps))
    store.close()

    os.makedirs(os.path.join(log_dir, "spool"), exist_ok=True)
    store = data.Store(config_dir=tmp)
    check("空 spool 目录不报错", isinstance(store.timestamps(), dict))
    store.close()

    with open(os.path.join(log_dir, "spool", "s1.jsonl"), "w", encoding="utf-8") as handle:
        handle.write('{"requestId": "f", "timestamp": 1789540199999}\n')
    store = data.Store(config_dir=tmp)
    stamps = store.timestamps()
    check("spool 中的记录被读到", stamps.get("f") == 1789540199999)
    store.close()

    odd = metrics.summarize({"a": float("nan"), "b": 1.0, "c": 1.0})
    check("NaN 不导致崩溃", isinstance(odd["total"], float))


# --------------------------------------------------------------------------
# 11. 参数边界
# --------------------------------------------------------------------------

def test_cli_limits():
    print("\n[11] 参数边界")

    payload = json.loads(run_cli(["--all", "--format", "json", "--limit", "1"]).stdout)
    check("--limit 1 只返回一条", len(payload["rows"]) == 1, str(len(payload["rows"])))

    result = run_cli(["--all", "--format", "json", "--limit", "0"])
    payload = json.loads(result.stdout)
    check("--limit 0 表示不限，返回全部",
          result.returncode == 0 and len(payload["rows"]) == payload["count"],
          "%d vs %d" % (len(payload["rows"]), payload["count"]))

    # 负数在切片里是「从尾部截断」，会静默少给几行。必须直接拒掉。
    result = run_cli(["--all", "--format", "json", "--limit", "-1"])
    check("负数 limit 被拒", result.returncode != 0, "竟然成功了")
    check("负数 limit 有说明",
          "负数" in (result.stdout + result.stderr), (result.stdout + result.stderr)[:80])

    result = run_cli(["--all", "--format", "json", "--limit", "9999"])
    payload = json.loads(result.stdout)
    check("超大 limit 不崩",
          result.returncode == 0 and payload["count"] >= len(payload["rows"]))

    result = run_cli(["--no-such-flag"])
    check("未知参数非零退出", result.returncode != 0)

    # 请求了卡片却悄悄给 JSON，等于答案不是要的那个
    result = run_cli(["--all", "--format", "card"])
    check("排行 + card 明确报错而非静默给 JSON",
          result.returncode != 0 and "卡片" in result.stdout, result.stdout[:80])

    result = run_cli(["--format", "json", "--no-tokens", "--estimate"])
    check("--no-tokens 与 --estimate 冲突时报错",
          result.returncode != 0 and "estimate" in result.stdout, result.stdout[:100])


# --------------------------------------------------------------------------

def verify_readme_counts():
    """两份 README 里写的测试项数必须与实际一致。

    放在最后跑，因为要先知道总数。加了几条检查却忘了改 README，
    是那种没人会发现的漂移——数字看着挺具体，其实是旧的。
    """
    readmes = ["README.md", "README.en.md"]
    # 本函数自己也要产出 len(readmes) 条检查，先把它算进去
    total = len(PASSED) + len(FAILED) + len(readmes)

    for name in readmes:
        path = os.path.join(ROOT, name)
        if not os.path.isfile(path):
            check("%s 存在（校验项数用）" % name, False, path)
            continue
        with open(path, encoding="utf-8") as handle:
            body = handle.read()
        match = re.search(r"(\d{2,4})\s*(?:项检查|checks)", body)
        stated = int(match.group(1)) if match else None
        check("%s 写的项数与实际一致（%d）" % (name, total),
              stated == total, "写的是 %s" % stated)


def main():
    print("=" * 60)
    print("wb-credits 全量测试")
    print("=" * 60)

    for test in (test_static, test_data_normal, test_data_errors, test_metrics,
                 test_anomaly, test_render, test_cli, test_cli_errors,
                 test_cross_check, test_robustness, test_cli_limits,
                 test_tokens, test_cli_tokens):
        try:
            test()
        except Exception as exc:  # noqa: BLE001
            import traceback
            FAILED.append((test.__name__, "测试函数自身抛异常"))
            print("  FAIL  %s 抛异常：%s" % (test.__name__, exc))
            traceback.print_exc()

    verify_readme_counts()

    print("\n" + "=" * 60)
    print("通过 %d 项，失败 %d 项" % (len(PASSED), len(FAILED)))
    if FAILED:
        print("\n失败明细：")
        for name, detail in FAILED:
            print("  - %s  %s" % (name, detail))
    print("=" * 60)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
