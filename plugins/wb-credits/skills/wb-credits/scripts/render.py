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
      <div style="font-size:12px;color:var(--color-text-tertiary);margin-top:10px;">{meta_line}</div>
{token_block}    </div>
    <div style="padding-left:30px;border-left:0.5px solid var(--color-border-tertiary);">
      <div style="font-size:12px;color:var(--color-text-secondary);">最近三轮</div>
      <div style="font-size:15px;color:var(--color-text-primary);margin-top:14px;">{recent}</div>
      <div style="font-size:12px;color:var(--color-text-tertiary);margin-top:12px;">{forecast_label}</div>
    </div>
  </div>
{footer}</div>"""

FOOTER_TEMPLATE = """  <div style="font-size:12px;color:var(--color-text-tertiary);margin-top:24px;padding-top:18px;border-top:0.5px solid var(--color-border-tertiary);line-height:1.9;">{notes}</div>
"""

# 明细解析失败时总数是偏低的。不标出来，用户会把这个数当成完整的。
PARTIAL_NOTE = "用量明细有损坏，总数可能偏低"


def card(summary):
    """本对话卡片。HTML 片段，供渲染工具使用。"""
    forecast = summary["forecast"]
    label = _forecast_label(forecast)
    if summary.get("in_flight"):
        label += "（本轮 %.2f 未定稿）" % summary["in_flight_value"]

    notes = _footer_notes(summary)
    footer = FOOTER_TEMPLATE.format(notes="<br>".join(notes)) if notes else ""

    return CARD_TEMPLATE.format(
        total="%.2f" % summary["total"],
        meta_line=_meta_line(summary),
        recent=_join(summary["recent"]),
        forecast_label=label,
        token_block=_token_block(summary.get("tokens")),
        footer=footer,
    )


TOKEN_ROW = '        <div style="display:flex;justify-content:space-between;gap:12px;"><span>{label}</span><span style="color:var(--color-text-secondary);">{value}</span></div>\n'

TOKEN_BLOCK = """      <div style="font-size:12px;color:var(--color-text-tertiary);margin-top:14px;line-height:1.95;">
{rows}      </div>
"""


def _token_block(tokens):
    """左下方的 token 拆分。未提供时返回空串，卡片退回原样。"""
    if not tokens:
        return ""
    rows = [
        TOKEN_ROW.format(label="未缓存输入", value=metrics.fmt_tokens(tokens["uncached"])),
        TOKEN_ROW.format(label="缓存命中", value=metrics.fmt_tokens(tokens["cached"])),
        TOKEN_ROW.format(label="输出", value=metrics.fmt_tokens(tokens["output"])),
    ]

    estimate = tokens.get("estimate")
    if estimate:
        rows.append(
            TOKEN_ROW.format(
                label="按比例估算",
                value="%.0f / %.0f / %.0f 积分" % (
                    estimate["uncached"], estimate["cached"], estimate["output"]
                ),
            )
        )
    return TOKEN_BLOCK.format(rows="".join(rows))


def _footer_notes(summary):
    """底栏提示。没有可说的就返回空列表，不留空行。"""
    notes = []
    if summary.get("partial"):
        notes.append(PARTIAL_NOTE)
    if summary.get("anomaly"):
        notes.append(summary["anomaly"])
    return notes


def _meta_line(summary):
    """主数字下方那一行。

    缓存命中率放在这里，不放 token 明细里——它是解释「为什么这么便宜」的那个
    数字，值得出现在第一屏。拿不到 token 数据时整段省略，不留空标签。
    """
    parts = ["积分", "%d 轮" % summary["rounds"]]
    tokens = summary.get("tokens")
    if tokens and tokens.get("cached_ratio") is not None:
        parts.append("缓存命中 %s" % metrics.fmt_ratio(tokens["cached_ratio"]))
    parts.append("上下文 %s" % metrics.fmt_context(summary["used"]))
    return "｜".join(parts)


def _forecast_label(forecast):
    """预估文案。数据不足时说明原因，不给 0 这类错数字。"""
    if forecast.get("credits") is None:
        return "再聊 %d 轮｜待首轮结算" % forecast["rounds"]
    return "再聊 %d 轮｜约 %.0f 积分" % (forecast["rounds"], forecast["credits"])


def text(summary):
    """纯文本降级版。渲染工具不可用时使用，字段与卡片一致。"""
    left = [
        "本对话",
        "%.2f" % summary["total"],
        _meta_line(summary),
    ]
    forecast = summary["forecast"]
    right = [
        "最近三轮",
        _join(summary["recent"]),
        _forecast_label(forecast),
    ]

    column = 42
    lines = [_pad(left[i], column) + right[i] for i in range(3)]

    tokens = summary.get("tokens")
    if tokens:
        lines.append("")
        lines.append(
            "未缓存输入 %s ｜ 缓存命中 %s ｜ 输出 %s"
            % (
                metrics.fmt_tokens(tokens["uncached"]),
                metrics.fmt_tokens(tokens["cached"]),
                metrics.fmt_tokens(tokens["output"]),
            )
        )
        estimate = tokens.get("estimate")
        if estimate:
            lines.append(
                "按比例估算 %.0f / %.0f / %.0f 积分"
                % (estimate["uncached"], estimate["cached"], estimate["output"])
            )

    notes = _footer_notes(summary)
    if notes:
        lines.append("")
        lines.extend(notes)
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
