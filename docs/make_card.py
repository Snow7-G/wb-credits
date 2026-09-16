#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 README 用的卡片图（SVG）。

卡片本体是 HTML + CSS 变量，颜色跟随用户主题；README 里的图做不到跟随，
所以出浅色与深色两版，由 README 用 <picture> 按 prefers-color-scheme 切换。

改了卡片模板的话，同步改这里的 CONFIG 再重跑：

    python3 docs/make_card.py            # 重新生成
    python3 docs/make_card.py --check     # 只校验图与脚本是否同步，不写文件

只依赖 Python 标准库，不装任何东西。
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

WIDTH = 720
HEIGHT = 232
PAD_LEFT = 30
PAD_TOP = 26
DIVIDER_X = 436
RIGHT_X = 462
VALUE_RIGHT_X = 410

FONT = ("-apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', "
        "'Hiragino Sans GB', 'Microsoft YaHei', 'Noto Sans CJK SC', "
        "Helvetica, Arial, sans-serif")

# 卡片内容。取一份有代表性的快照，不是实时数据。
CONFIG = {
    "label": "本对话",
    "total": "142.85",
    "meta": "积分｜39 轮｜缓存命中 98%｜上下文 25 万",
    "token_rows": [
        ("未缓存输入", "226 万"),
        ("缓存命中", "8840 万"),
        ("输出", "31 万"),
    ],
    "right_label": "最近三轮",
    "recent": "6.60｜7.47｜0.55",
    "forecast": "再聊 10 轮｜约 53 积分",
}

THEMES = {
    "light": {
        "bg": "#f6f8fa",
        "border": "#d8dee4",
        "primary": "#1f2328",
        "secondary": "#59636e",
        "tertiary": "#818b98",
    },
    "dark": {
        "bg": "#1b1f24",
        "border": "#2d333b",
        "primary": "#e6edf3",
        "secondary": "#9198a1",
        "tertiary": "#6e7681",
    },
}


def build(theme):
    c = THEMES[theme]
    lines = []
    add = lines.append

    add('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
        'viewBox="0 0 %d %d" role="img">' % (WIDTH, HEIGHT, WIDTH, HEIGHT))
    add('  <title>wb-credits 卡片</title>')
    add('  <desc>本对话累计积分、缓存命中率、token 拆分与最近三轮节奏</desc>')
    add('  <rect x="0.5" y="0.5" width="%d" height="%d" rx="12" fill="%s" '
        'stroke="%s"/>' % (WIDTH - 1, HEIGHT - 1, c["bg"], c["border"]))
    add('  <line x1="%d" y1="%d" x2="%d" y2="%d" stroke="%s"/>'
        % (DIVIDER_X, PAD_TOP + 4, DIVIDER_X, HEIGHT - PAD_TOP - 4, c["border"]))

    def text(x, y, body, size, color, anchor="start", weight="400", extra=""):
        return ('  <text x="%s" y="%s" font-family="%s" font-size="%s" '
                'font-weight="%s" fill="%s" text-anchor="%s"%s>%s</text>'
                % (x, y, FONT, size, weight, color, anchor, extra, body))

    # 左栏
    add(text(PAD_LEFT, 52, CONFIG["label"], 12, c["secondary"]))
    add(text(PAD_LEFT, 94, CONFIG["total"], 30, c["primary"], weight="500"))
    add(text(PAD_LEFT, 122, CONFIG["meta"], 12, c["tertiary"]))

    row_y = 154
    for label, value in CONFIG["token_rows"]:
        add(text(PAD_LEFT, row_y, label, 12, c["tertiary"]))
        add(text(VALUE_RIGHT_X, row_y, value, 12, c["secondary"], anchor="end"))
        row_y += 20

    # 右栏
    add(text(RIGHT_X, 52, CONFIG["right_label"], 12, c["secondary"]))
    add(text(RIGHT_X, 90, CONFIG["recent"], 15, c["primary"]))
    add(text(RIGHT_X, 118, CONFIG["forecast"], 12, c["tertiary"]))

    add('</svg>')
    return "\n".join(lines) + "\n"


def check():
    """校验磁盘上的图与当前脚本一致，不写任何文件。"""
    stale = []
    for theme in THEMES:
        path = os.path.join(HERE, "card-%s.svg" % theme)
        want = build(theme)
        try:
            with open(path, encoding="utf-8") as handle:
                have = handle.read()
        except OSError:
            stale.append("%s（缺失）" % path)
            continue
        if have != want:
            stale.append(path)
    if stale:
        print("与生成脚本不一致：%s" % "、".join(stale))
        print("跑一次 python3 docs/make_card.py 重新生成。")
        return 1
    print("两张卡片图与生成脚本一致")
    return 0


def main(argv):
    if "--check" in argv:
        return check()
    for theme in THEMES:
        path = os.path.join(HERE, "card-%s.svg" % theme)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(build(theme))
        print("写出 %s" % path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
