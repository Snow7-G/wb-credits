# -*- coding: utf-8 -*-
"""呈现层：把指标渲染成卡片，或降级为纯文本。

颜色一律走主题变量，不硬编码色值。卡片 HTML 由上层交给渲染工具输出。
"""

from __future__ import annotations

import unicodedata

import metrics

CARD_TEMPLATE = """<div style="background:var(--color-background-secondary);border-radius:12px;padding:26px 30px;font-family:var(--font-sans);max-width:620px;">
  <div style="display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);">
    <div style="padding-right:30px;">
      <div style="font-size:12px;color:var(--color-text-secondary);">本对话</div>
      <div style="font-size:30px;font-weight:500;color:var(--color-text-primary);line-height:1.1;margin-top:12px;">{total}</div>
      <div style="font-size:12px;color:var(--color-text-tertiary);margin-top:10px;">积分｜{rounds} 轮｜上下文 {context}</div>
    </div>
    <div style="padding-left:30px;border-left:0.5px solid var(--color-border-tertiary);">
      <div style="font-size:12px;color:var(--color-text-secondary);">最近三轮</div>
      <div style="font-size:15px;color:var(--color-text-primary);margin-top:14px;">{recent}</div>
      <div style="font-size:12px;color:var(--color-text-tertiary);margin-top:12px;">{forecast_label}</div>
    </div>
  </div>
{footer}</div>"""

FOOTER_TEMPLATE = """  <div style="font-size:12px;color:var(--color-text-tertiary);margin-top:24px;padding-top:18px;border-top:0.5px solid var(--color-border-tertiary);">{anomaly}</div>
"""


def card(summary):
    """本对话卡片。HTML 片段，供渲染工具使用。"""
    forecast = summary["forecast"]
    label = "再聊 %d 轮｜约 %.0f 积分" % (forecast["rounds"], forecast["credits"])
    if summary.get("in_flight"):
        label += "（本轮 %.2f 未定稿）" % summary["in_flight_value"]

    footer = ""
    if summary.get("anomaly"):
        footer = FOOTER_TEMPLATE.format(anomaly=summary["anomaly"])

    return CARD_TEMPLATE.format(
        total="%.2f" % summary["total"],
        rounds=summary["rounds"],
        context=metrics.fmt_context(summary["used"]),
        recent=_join(summary["recent"]),
        forecast_label=label,
        footer=footer,
    )


def text(summary):
    """纯文本降级版。渲染工具不可用时使用，字段与卡片一致。"""
    left = [
        "本对话",
        "%.2f" % summary["total"],
        "积分｜%d 轮｜上下文 %s" % (summary["rounds"], metrics.fmt_context(summary["used"])),
    ]
    forecast = summary["forecast"]
    right = [
        "最近三轮",
        _join(summary["recent"]),
        "再聊 %d 轮｜约 %.0f 积分" % (forecast["rounds"], forecast["credits"]),
    ]

    column = 34
    lines = [_pad(left[i], column) + right[i] for i in range(3)]
    if summary.get("anomaly"):
        lines.append("")
        lines.append(summary["anomaly"])
    return "\n".join(lines)


def rank_text(rows, limit=15):
    """全部会话排行，纯文本。标题列按显示宽度对齐，兼容中英文混排。"""
    if not rows:
        return "没有找到带用量记录的会话。"
    lines = []
    for index, row in enumerate(rows[:limit], 1):
        cost = "%2d. %9.2f" % (index, row["total"])
        meta = "%d 轮 / %s" % (row["rounds"], metrics.fmt_context(row["used"]))
        lines.append("%s  %s  %s" % (cost, _pad(meta, 22), row["title"]))
    if len(rows) > limit:
        lines.append("")
        lines.append("共 %d 个会话，已显示前 %d 个。" % (len(rows), limit))
    return "\n".join(lines)


def detail_text(lines, limit=20):
    """逐次明细，纯文本。"""
    if not lines:
        return "该会话没有用量记录。"
    out = []
    for item in lines[:limit]:
        out.append("#%-3d %.2f" % (item["round"], item["credits"]))
    if len(lines) > limit:
        out.append("… 另有 %d 次请求" % (len(lines) - limit))
    return "\n".join(out)


def _join(values):
    if not values:
        return "—"
    return "｜".join("%.2f" % value for value in values)


def _width(text):
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def _pad(text, target):
    return text + " " * max(1, target - _width(text))
