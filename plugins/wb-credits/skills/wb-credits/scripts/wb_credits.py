#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""wb-credits 命令行入口。

只负责取数和格式化，不做呈现决策。默认输出 JSON，
其中 card 字段是可直接交给渲染工具的 HTML 片段。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import data
import metrics
import render

VERSION = "0.1.0"

# Windows 控制台默认不是 UTF-8，中文会乱码甚至抛 UnicodeEncodeError。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

NO_SESSION_HINT = (
    "拿不到当前会话 ID。\n"
    "请通过 /credits 命令调用，或用 --session 指定会话 ID。"
)


def build_parser():
    parser = argparse.ArgumentParser(
        prog="wb-credits",
        description="按会话查看 WorkBuddy 积分消耗。",
    )
    parser.add_argument("--all", action="store_true", help="列出全部会话排行")
    parser.add_argument("-k", "--kw", help="按会话标题过滤，配合 --all 使用")
    parser.add_argument("--session", help="指定会话 ID，默认取当前会话")
    parser.add_argument("--detail", action="store_true", help="展开逐次请求明细")
    parser.add_argument("--limit", type=int, default=15, help="排行显示条数")
    parser.add_argument(
        "--format",
        choices=("json", "text", "card"),
        default="json",
        help="输出格式。json 含数据与卡片 HTML，text 为等宽纯文本，card 仅输出 HTML",
    )
    parser.add_argument("--version", action="version", version="wb-credits " + VERSION)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except data.DataError as exc:
        emit_error(str(exc), args.format)
        return 1


def run(args):
    with data.Store() as store:
        sessions = store.sessions()
        if args.all:
            return run_rank(args, store, sessions)
        return run_session(args, store, sessions)


def run_session(args, store, sessions):
    session_id = args.session or data.current_session_id()
    if not session_id:
        raise data.DataError(NO_SESSION_HINT)

    usage = store.usage(session_id)
    if usage is None:
        raise data.DataError("这个会话还没有用量记录。")

    summary = metrics.summarize(
        usage["credits"],
        usage["used"],
        usage["size"],
        store.timestamps(),
        data.current_request_id(),
    )
    meta = sessions.get(session_id) or {}
    detail = metrics.detail_lines(usage["credits"])

    payload = {
        "ok": True,
        "kind": "session",
        "session": {
            "id": session_id,
            "title": meta.get("title") or "(未命名会话)",
            "model": meta.get("model") or "",
            "cwd": meta.get("cwd") or "",
        },
        "summary": summary,
        "partial": usage["partial"],
        "card": render.card(summary),
    }
    if args.detail:
        payload["detail"] = detail

    if args.format == "card":
        print(payload["card"])
    elif args.format == "text":
        output = render.text(summary)
        if args.detail:
            output += "\n\n" + render.detail_text(detail)
        print(output)
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def run_rank(args, store, sessions):
    rows = metrics.rank(store.all_usage(), sessions)
    if args.kw:
        key = args.kw.lower()
        rows = [row for row in rows if key in row["title"].lower()]

    payload = {
        "ok": True,
        "kind": "rank",
        "count": len(rows),
        "total": sum(row["total"] for row in rows),
        "rows": rows[: args.limit] if args.limit else rows,
    }

    if args.format == "text":
        print(render.rank_text(rows, args.limit or len(rows)))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def emit_error(message, fmt):
    if fmt == "text":
        sys.stderr.write(message + "\n")
    else:
        print(
            json.dumps({"ok": False, "error": message}, ensure_ascii=False, indent=2)
        )


if __name__ == "__main__":
    sys.exit(main())
