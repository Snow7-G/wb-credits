# wb-credits

按**会话**查看 WorkBuddy 的积分消耗。

WorkBuddy 官网的「套餐与用量」只能看到总量和逐条请求明细，没有"这个对话一共花了多少"这个视图。这个插件读本机数据，把每个会话下所有请求的积分加总，补上这块空白。

## 输出长这样

```
本对话                            最近三轮
31.58                             0.32｜1.82｜4.64
积分｜20 轮｜上下文 26 万         再聊 10 轮｜约 10 积分

14:29 单轮 5.18（闲置 2h19m）
```

左栏是既成事实，右栏给你节奏感并往前推一步，底栏在有异常时说明原因。

## 安装

```
/plugin marketplace add Snow7-G/wb-credits
/plugin install wb-credits@snow7g
/reload-plugins
```

本地开发时把第一行换成本地路径：

```
/plugin marketplace add /path/to/wb-credits
```

## 用法

```
/credits                    本对话消耗卡片
/credits --all              全部会话排行
/credits -k 关键词           按会话标题过滤
/credits --detail           附加逐次请求明细
```

也可以直接用自然语言问，比如"我这个月积分都花哪了"，技能会自动触发。

不通过对话时，脚本可以单独跑：

```bash
python3 plugins/wb-credits/skills/wb-credits/scripts/wb_credits.py --format text
python3 plugins/wb-credits/skills/wb-credits/scripts/wb_credits.py --all --format json
```

只依赖 Python 标准库，无需安装依赖。

## 数据从哪来

本机 `~/.workbuddy/workbuddy.db`，两张表：

| 表 | 用途 |
|---|---|
| `sessions` | 会话元信息：标题、工作目录、模型、时间 |
| `session_usage` | 会话用量，其中 `credit_json` = `{"请求ID": 积分}` |

把某个会话 `credit_json` 里所有值加起来，就是该会话的总积分。这张表由客户端自己维护，插件只读不写。

以只读方式打开（`file:...?mode=ro`），不会干扰正在运行的客户端。不联网，不上传任何数据。

## 几个设计上的取舍

**边际成本用最近三轮，不用全程均值。** 单轮成本受两个变量支配：前缀缓存是否命中、这一轮任务有多重。全程均值会被早期的重型任务拉高，给出偏高的预估。

**不给"这个对话最终会花多少"。** 那句话取决于你还要聊多少轮，算不出来。写出来就是骗人。

**异常只在能给出原因时才提示。** 目前只识别一种：长时间闲置后缓存重建。判断不出原因就不吭声，不编理由。

**数字错比报错糟糕。** 表结构不匹配时明确报错，时间戳拿不到时留空，都不会猜一个填上。

## 已知限制

- 依赖客户端内部数据结构，不是公开 API。客户端升级改了表结构，插件会失效并明确报错。
- `used` 字段的确切含义（当前值还是峰值）没有权威说明，输出中只标为"上下文占用"，不做进一步推断。
- 审计日志分两层写入，部分请求拿不到精确时间戳。缺失时留空。
- 需要 WorkBuddy 至少运行过并产生过 AI 请求。

## License

MIT
