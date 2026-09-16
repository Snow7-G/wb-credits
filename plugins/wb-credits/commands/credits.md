---
description: 查看本对话或全部会话的积分消耗
argument-hint: [--all] [-k 关键词] [--detail]
---

用户想查看积分消耗，附加参数为 `$ARGUMENTS`。按下面三步执行，不要跳步。

## 第一步：取数

脚本位于本插件根目录的 `skills/wb-credits/scripts/wb_credits.py`。
优先用环境变量定位：`python3 "${CODEBUDDY_PLUGIN_ROOT}/skills/wb-credits/scripts/wb_credits.py"`。

如果该路径不存在（环境变量未生效），用 Glob 在插件根目录下搜索 `wb_credits.py`，取到绝对路径后执行。

参数映射：

- 无参数 → 本对话：`--format json`
- 含 `--all` → 全部会话排行：`--all --format json --limit 15`
- 含 `-k 关键词` → 附加 `-k 关键词`
- 含 `--detail` → 附加 `--detail`

脚本的输出是 JSON。若返回 `{"ok": false}`，把 `error` 字段的内容原样告诉用户，不要自行发挥，也不要重试。

## 第二步：渲染

JSON 里的 `card` 字段是一段 HTML 片段，就是本对话卡片的模板。

**必须调用可视化渲染工具把它渲染成卡片。这是硬要求，不得退化成纯文本，不得把 HTML 源码贴给用户。**

只有在渲染工具确实不可用时，才退回纯文本：执行 `--format text`，把等宽文本作为回复正文。

## 第三步：说明

渲染完成后，用一到两句话说明数字的含义。

- `summary.anomaly` 有值时才展开解释，说明为什么那一次特别贵。
- 没有异常就不要再补充解释。

不要重复卡片里已有的数字，不要罗列字段名，不要介绍脚本是怎么工作的。
