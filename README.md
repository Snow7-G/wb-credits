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

**WorkBuddy 桌面端**：在插件管理页把本仓库目录添加为本地市场，安装 `wb-credits`，然后新开一个对话。

桌面端不实现 `/plugin` 系列斜杠命令——敲在聊天框里会被当作普通消息。同样地，`/reload-plugins` 在桌面端也无效，插件配置只在会话启动时读取，所以要**新开对话或重启**才能加载。

**CodeBuddy Code（CLI）**：

```
/plugin marketplace add Snow7-G/wb-credits
/plugin install wb-credits@snow7g
/reload-plugins
```

本地开发时把第一行换成本地路径：

```
/plugin marketplace add /path/to/wb-credits
```

**怎么确认装上了**：尝试调用技能名 `wb-credits`。返回 `Can not find skill` 就是没加载，新开一个对话再试。

## 用法

```
/credits                    本对话消耗卡片
/credits --all              全部会话排行
/credits -k 关键词           按会话标题过滤
/credits --detail           附加逐次请求明细
/credits --tokens           附加 token 拆分
/credits --tokens --estimate  再加三类 token 的积分估算
```

也可以直接用自然语言问，比如"我这个月积分都花哪了"，技能会自动触发。

不通过对话时，脚本可以单独跑：

```bash
python3 plugins/wb-credits/skills/wb-credits/scripts/wb_credits.py --format text
python3 plugins/wb-credits/skills/wb-credits/scripts/wb_credits.py --all --format json
python3 plugins/wb-credits/skills/wb-credits/scripts/wb_credits.py --tokens --format text
```

只依赖 Python 标准库，无需安装依赖。

## token 拆分

加 `--tokens` 后，卡片左下方会多出三行：

```
未缓存输入        160 万
缓存命中        6212 万
输出              25 万
```

数据来自 `~/.workbuddy/traces/` 里的 trace 文件。两点要注意：

**没有「思考」token。** 客户端的 trace 里不记录 reasoning 类字段，这项拿不到，不是权限问题。

**读取慢。** 文件名不含会话 ID，必须逐个读进来才能按会话聚合。实测 562 个文件 / 454 MB 要 **约 1 秒**，且随使用时间线性增长。所以默认关闭，只有主动加 `--tokens` 才扫。

再加 `--estimate` 会多出一行按假设单价分摊的积分构成：

```
按比例估算        23 / 89 / 11 积分
```

**这一行是估算，不是实测。** 它按未缓存输入 1、缓存 0.1、输出 3 的经验比例把总积分分摊到三类。比例随模型变化且未做校准，别当精确值用。

## 运行测试

```bash
python3 tests/test_wb_credits.py
```

真实数据只读；边界场景用临时构造的数据库，不触碰你的数据。

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
