# -*- coding: utf-8 -*-
"""计算层：把原始用量整理成展示所需指标。

不直接读写数据库，也不负责排版。输入输出都是普通 dict。
"""

from __future__ import annotations

import datetime

# 边际成本取最近几轮的已结算值
RECENT_WINDOW = 3

# 峰值超过中位数这么多倍，才认为存在异常
ANOMALY_MEDIAN_RATIO = 4.0

# 间隔超过这个时长，才把变贵归因到闲置
IDLE_THRESHOLD_MS = 30 * 60 * 1000

FORECAST_ROUNDS = 10


def _round(value):
    """浮点累加会留下 0.30000000000000004 这类尾数，输出前收敛。"""
    return round(value, 4)


def summarize(credits, used=0, size=0, timestamps=None, current_request_id=""):
    """单会话汇总。

    credits: {"请求ID": 积分}，顺序沿用 credit_json 的原始键序（接近写入顺序）。
    current_request_id: 当前这一轮，尚未定稿，不计入边际成本。
    """
    timestamps = timestamps or {}
    items = list(credits.items())
    values = [value for _, value in items]

    settled = [value for rid, value in items if rid != current_request_id]
    recent = settled[-RECENT_WINDOW:]
    marginal = sum(recent) / len(recent) if recent else 0.0

    in_flight = bool(current_request_id) and current_request_id in credits

    return {
        "total": _round(sum(values)),
        "rounds": len(items),
        "used": used,
        "size": size,
        "recent": [_round(value) for value in values[-RECENT_WINDOW:]],
        "marginal": _round(marginal),
        "forecast": {
            "rounds": FORECAST_ROUNDS,
            "credits": _round(marginal * FORECAST_ROUNDS),
        },
        "in_flight": in_flight,
        "in_flight_value": _round(credits.get(current_request_id, 0.0)) if in_flight else 0.0,
        "anomaly": find_anomaly(items, timestamps),
    }


def find_anomaly(items, timestamps):
    """挑一次异常贵的请求，返回一行提示。

    只在能给出原因时才提示，判断不出来就返回 None。
    目前只识别一种原因：长时间闲置后缓存重建。
    """
    if len(items) < 3 or not timestamps:
        return None

    values = sorted(value for _, value in items)
    median = values[len(values) // 2]
    if median <= 0:
        return None

    peak_id, peak_value = max(items, key=lambda kv: kv[1])
    if peak_value < median * ANOMALY_MEDIAN_RATIO:
        return None
    if peak_id not in timestamps:
        return None

    timed = sorted(
        ((rid, value) for rid, value in items if rid in timestamps),
        key=lambda kv: timestamps[kv[0]],
    )
    order = [rid for rid, _ in timed]
    position = order.index(peak_id)
    if position == 0:
        return "%s 单轮 %.2f" % (fmt_clock(timestamps[peak_id]), peak_value)

    gap = timestamps[peak_id] - timestamps[order[position - 1]]
    if gap < IDLE_THRESHOLD_MS:
        return None
    return "%s 单轮 %.2f（闲置 %s）" % (
        fmt_clock(timestamps[peak_id]),
        peak_value,
        fmt_gap(gap),
    )


def rank(usages, sessions):
    """全部会话按积分降序。"""
    rows = []
    for usage in usages:
        meta = sessions.get(usage["session_id"]) or {}
        rows.append(
            {
                "session_id": usage["session_id"],
                "title": meta.get("title") or "(未命名会话)",
                "model": meta.get("model") or "",
                "used": usage["used"],
                "rounds": len(usage["credits"]),
                "total": _round(sum(usage["credits"].values())),
                "updated_at": usage["updated_at"],
            }
        )
    rows.sort(key=lambda row: row["total"], reverse=True)
    return rows


def detail_lines(credits):
    """逐次请求明细，保持原始顺序。"""
    return [
        {"round": index, "credits": value}
        for index, (_, value) in enumerate(credits.items(), 1)
    ]


def fmt_clock(ms):
    return datetime.datetime.fromtimestamp(ms / 1000.0).strftime("%H:%M")


def fmt_gap(ms):
    minutes = int(round(ms / 60000.0))
    hours, mins = divmod(minutes, 60)
    if hours:
        return "%dh%dm" % (hours, mins)
    return "%dm" % mins


def fmt_context(used):
    """上下文规模的粗略表示。上万后不带小数，避免给出虚假精度。"""
    if used >= 10000:
        return "%.0f 万" % (used / 10000.0)
    return "%d" % used
