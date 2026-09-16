# -*- coding: utf-8 -*-
"""数据层：只读访问 WorkBuddy 本地数据。

设计约束：
1. 全程只读，客户端可能正在写。
2. 对结构变化容错，宁可给出人话提示，也不静默算错。
3. 对外只抛 DataError，message 可直接展示给用户。
"""

from __future__ import annotations

import glob
import json
import os
import sqlite3
import time

DEFAULT_CONFIG_DIRS = (
    os.path.expanduser("~/.workbuddy"),
    os.path.expanduser("~/.codebuddy"),
)

# 表 -> 必须存在的字段。缺失即判定版本不兼容。
REQUIRED_SCHEMA = {
    "sessions": ("id", "title", "custom_title", "model", "updated_at", "cwd"),
    "session_usage": ("session_id", "used", "credit_json", "updated_at"),
}

SCHEMA_HINT = "可能是 WorkBuddy 升级改了数据结构，请到项目主页反馈，附上客户端版本号。"


class DataError(Exception):
    """数据不可用。message 是给人看的说明，不含堆栈。"""


def find_config_dir():
    """定位配置目录。

    环境变量显式指定时不回退：路径无效就直接报错。
    静默回退到别的目录会用错数据，比直接报错糟糕。
    """
    for var in ("WORKBUDDY_CONFIG_DIR", "CODEBUDDY_CONFIG_DIR"):
        path = os.environ.get(var)
        if path:
            if os.path.isfile(os.path.join(path, "workbuddy.db")):
                return path
            raise DataError(
                "环境变量 %s 指向的目录里没有 workbuddy.db：%s\n"
                "如果这是误设，请取消该环境变量。" % (var, path)
            )
    for path in DEFAULT_CONFIG_DIRS:
        if os.path.isfile(os.path.join(path, "workbuddy.db")):
            return path
    raise DataError(
        "找不到 WorkBuddy 数据目录。\n已尝试：%s\n请确认客户端已安装，并至少运行过一次。"
        % "、".join(DEFAULT_CONFIG_DIRS)
    )


class Store(object):
    """数据访问入口。"""

    def __init__(self, config_dir=None):
        self.config_dir = config_dir or find_config_dir()
        self.db_path = os.path.join(self.config_dir, "workbuddy.db")
        if not os.path.isfile(self.db_path):
            raise DataError("找不到数据库文件：%s" % self.db_path)
        self._con = None

    # -- 连接 ------------------------------------------------------------

    @property
    def con(self):
        if self._con is None:
            self._con = self._connect()
        return self._con

    def _connect(self):
        if not os.path.isfile(self.db_path):
            raise DataError("找不到数据库文件：%s" % self.db_path)
        uri = "file:%s?mode=ro" % self.db_path
        try:
            con = sqlite3.connect(uri, uri=True)
            con.execute("select 1 from sqlite_master limit 1")
        except sqlite3.Error as exc:
            raise DataError("打开数据库失败：%s" % exc)
        self._check_schema(con)
        return con

    def _check_schema(self, con):
        try:
            tables = {
                row[0]
                for row in con.execute(
                    "select name from sqlite_master where type = 'table'"
                )
            }
        except sqlite3.Error as exc:
            raise DataError("读取数据表失败：%s" % exc)

        missing_tables = set(REQUIRED_SCHEMA) - tables
        if missing_tables:
            raise DataError(
                "数据结构不匹配，缺少表：%s\n%s"
                % ("、".join(sorted(missing_tables)), SCHEMA_HINT)
            )

        for table, columns in REQUIRED_SCHEMA.items():
            try:
                present = {row[1] for row in con.execute("pragma table_info(%s)" % table)}
            except sqlite3.Error as exc:
                raise DataError("读取表 %s 结构失败：%s" % (table, exc))
            missing = set(columns) - present
            if missing:
                raise DataError(
                    "数据结构不匹配，表 %s 缺少字段：%s\n%s"
                    % (table, "、".join(sorted(missing)), SCHEMA_HINT)
                )

    def close(self):
        if self._con is not None:
            self._con.close()
            self._con = None

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()
        return False

    # -- 会话 ------------------------------------------------------------

    def sessions(self):
        """会话元信息，key 为 session_id。读取失败的字段降级为空。"""
        sql = """
            select id,
                   coalesce(nullif(custom_title, ''), title, ''),
                   cwd, model, created_at, updated_at
            from sessions
        """
        out = {}
        try:
            rows = self.con.execute(sql).fetchall()
        except sqlite3.Error as exc:
            raise DataError("读取会话列表失败：%s" % exc)
        for row in rows:
            out[row[0]] = {
                "id": row[0],
                "title": row[1] or "",
                "cwd": row[2] or "",
                "model": row[3] or "",
                "created_at": row[4] or 0,
                "updated_at": row[5] or 0,
            }
        return out

    # -- 用量 ------------------------------------------------------------

    def usage(self, session_id):
        """单个会话的用量。会话不存在或没有记录时返回 None。"""
        sql = """
            select credit_json, used, size, updated_at
            from session_usage where session_id = ?
        """
        try:
            row = self.con.execute(sql, (session_id,)).fetchone()
        except sqlite3.Error as exc:
            raise DataError("读取会话用量失败：%s" % exc)
        if row is None:
            return None
        parsed = self._parse_usage(row)
        parsed["session_id"] = session_id
        return parsed

    def all_usage(self):
        """所有会话的用量。"""
        sql = "select session_id, credit_json, used, size, updated_at from session_usage"
        out = []
        try:
            rows = self.con.execute(sql).fetchall()
        except sqlite3.Error as exc:
            raise DataError("读取用量汇总失败：%s" % exc)
        for row in rows:
            parsed = self._parse_usage(row[1:])
            parsed["session_id"] = row[0]
            out.append(parsed)
        return out

    @staticmethod
    def _parse_usage(row):
        """credit_json 可能损坏。解析失败时降级为空，并标记 partial。"""
        raw, used, size, updated = row
        credits = {}
        partial = False
        if raw:
            try:
                parsed = json.loads(raw)
            except ValueError:
                parsed = None
                partial = True
            if isinstance(parsed, dict):
                for key, value in parsed.items():
                    try:
                        credits[key] = float(value)
                    except (TypeError, ValueError):
                        partial = True
            elif parsed is not None:
                partial = True
        return {
            "credits": credits,
            "used": used or 0,
            "size": size or 0,
            "updated_at": updated or 0,
            "partial": partial,
        }

    # -- 时间戳 ----------------------------------------------------------

    def timestamps(self):
        """requestId -> 毫秒时间戳。

        审计日志分两层写入：已归档的日文件，以及尚未合并的 spool。
        只读其中一处会漏掉最新记录，必须两处都读。
        """
        out = {}
        base = os.path.join(self.config_dir, "audit-log")
        patterns = (
            os.path.join(base, "*.jsonl"),
            os.path.join(base, "spool", "*.jsonl"),
        )
        for pattern in patterns:
            for path in sorted(glob.glob(pattern)):
                self._scan_timestamps(path, out)
        return out

    @staticmethod
    def _scan_timestamps(path, out):
        try:
            handle = open(path, encoding="utf-8", errors="ignore")
        except OSError:
            return
        with handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(obj, dict):
                    continue
                rid = obj.get("requestId")
                ts = obj.get("timestamp")
                if not rid or rid in out:
                    continue
                if isinstance(ts, bool) or not isinstance(ts, (int, float)):
                    continue
                if ts > 0:
                    out[rid] = ts


    # -- token 明细（traces）-------------------------------------------------

    def trace_tokens(self, max_age_days=None):
        """从 traces/ 聚合 token 用量，按 sessionId 分组。

        数据源是 `<配置目录>/traces/<pid>/trace_*.json`。文件名不含会话 ID，
        必须逐个读进来才能按会话聚合。实测全量扫描约 1 秒（562 个文件 / 454 MB），
        且随使用时间线性增长，所以默认不读，由上层按需触发。

        max_age_days 非空时只扫最近这些天改动过的文件。
        """
        base = os.path.join(self.config_dir, "traces")
        out = {}
        if not os.path.isdir(base):
            return out

        cutoff = None
        if max_age_days is not None:
            cutoff = time.time() - max_age_days * 86400

        for path in glob.glob(os.path.join(base, "*", "trace_*.json")):
            if cutoff is not None:
                try:
                    if os.path.getmtime(path) < cutoff:
                        continue
                except OSError:
                    continue
            self._scan_trace(path, out)
        return out

    @staticmethod
    def _scan_trace(path, out):
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return

        trace = data.get("trace")
        if not isinstance(trace, dict):
            return
        info = trace.get("modelInfo")
        if not isinstance(info, dict):
            return
        session_id = trace.get("sessionId")
        if not session_id:
            return

        total_in = _as_int(info.get("totalInputTokens"))
        cached = _as_int(info.get("totalCachedTokens"))
        if cached > total_in:
            cached = total_in
        total_out = _as_int(info.get("totalOutputTokens"))

        entry = out.setdefault(
            session_id,
            {"input": 0, "cached": 0, "uncached": 0, "output": 0, "traces": 0},
        )
        entry["input"] += total_in
        entry["cached"] += cached
        entry["uncached"] += total_in - cached
        entry["output"] += total_out
        entry["traces"] += 1


def _as_int(value):
    """token 字段容错：非数值当 0。布尔值要排除，bool 是 int 的子类。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return int(value)


def current_session_id():
    """当前会话 ID。取不到时返回空串。"""
    return os.environ.get("CODEBUDDY_SESSION_ID", "").strip()


def current_request_id():
    """当前这一轮的请求 ID。取不到时返回空串。"""
    return os.environ.get("CODEBUDDY_CONVERSATION_REQUEST_ID", "").strip()
