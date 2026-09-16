---
name: wb-credits
description: 查询 WorkBuddy 本机各会话的积分消耗。可看当前对话的累计消耗与边际成本、全部会话排行、逐次请求明细。适用于用户说"这个对话花了多少积分""我积分都花哪了""哪个任务最费积分""查看用量明细""按会话统计消耗"等场景。数据取自本机数据库，全程只读、不联网、不上传。
---

# 查询 WorkBuddy 会话积分消耗

## 为什么需要它

WorkBuddy 官网的「套餐与用量」只有总量和逐条 Request 明细，没有"某个对话一共花了多少"这个视图。这个技能直接读本机数据，把某个会话下所有请求的积分加总，补上这块空白。

## 取数

脚本在 `scripts/wb_credits.py`，只依赖 Python 标准库。

```bash
python3 scripts/wb_credits.py --format json          # 本对话，默认
python3 scripts/wb_credits.py --all --format text    # 全部会话排行
python3 scripts/wb_credits.py -k 关键词               # 按标题过滤，配合 --all
python3 scripts/wb_credits.py --detail               # 附加逐次明细
python3 scripts/wb_credits.py --session <会话ID>      # 指定会话
python3 scripts/wb_credits.py --no-tokens            # 不查 token，只读数据库，更快
python3 scripts/wb_credits.py --estimate             # 附加三类 token 的积分估算
```

三种输出格式：`json`（含数据与卡片 HTML）、`text`（等宽纯文本）、`card`（仅 HTML）。

若插件根目录环境变量不可用，用 Glob 搜索 `wb_credits.py` 定位后以绝对路径执行。

## token 拆分

数据源是 `<配置目录>/traces/<pid>/trace_*.json`，其中的 `modelInfo` 有 `totalInputTokens` / `totalCachedTokens` / `totalOutputTokens`。

**两个必须知道的事实：**

**没有「思考」token。** trace 的全部字段里不存在 reason / think / thought 相关项。用户若要求"思考用了多少"，如实说明拿不到，不要编。

**默认开启，代价可忽略。** 只需要 sessionId 与 modelInfo，两者都固定在文件开头 400 字节内，因此只读头部就够——实测 571 个文件 / 462 MB 是 **约 42 毫秒**（早期逐文件全量解析要 1 秒）。头部拿不到字段时会读全文再判一次，不会静默漏数据。

拆分展示三项：

| 项 | 含义 |
|---|---|
| 未缓存输入 | `input - cached`，按全价计费的部分 |
| 缓存命中 | `cached`，按折扣价计费 |
| 输出 | `output` |

**缓存命中率单独放在主数字下面那一行**，不埋在明细里。它解释的是"为什么花了这么多"——命中率高说明钱主要花在重复发送的上下文上，而不是输出。这是整张卡片里最有解释力的一项。

**`--estimate` 的结果是估算，不是实测。** 它按假设的单价比例（未缓存输入 1、缓存 0.1、输出 3）把总积分分摊到三类。比例是行业经验值，随模型变化，未做校准。对外说明时必须标注"估算"，不能说成实测值。

## 呈现

JSON 里的 `card` 字段是卡片 HTML。**必须调用可视化渲染工具渲染成卡片**，不要贴 HTML 源码给用户。

渲染工具不可用时退回 `--format text`，直接输出等宽文本。

渲染完成后用一两句话说明数字含义。`summary.anomaly` 有值时才解释原因，没有就不要再补充。

`summary.forecast.credits` 为 `null` 时表示已结算轮次不足（`summary.forecast.basis` 为 0），此时**不要编造预估值**，如实说明首轮尚未结算即可。卡片模板会自动显示"待首轮结算"。

## 数据源

`~/.workbuddy/workbuddy.db`，SQLite，WAL 模式。

| 表 | 关键字段 |
|---|---|
| `sessions` | `id` / `title` / `custom_title` / `cwd` / `model` / `updated_at` |
| `session_usage` | `session_id` / `used` / `size` / `credit_json` |

`credit_json` 是 `{"请求ID": 积分}` 的逐次明细，求和即该会话总积分。

## 实现上必须知道的六件事

**1. 一律只读打开**（`file:...?mode=ro`）。客户端可能正在写。

**2. 会话定位靠环境变量**，不要用启发式推断。`CODEBUDDY_SESSION_ID` 是当前会话，`CODEBUDDY_CONVERSATION_REQUEST_ID` 是当前这一轮。

**3. 审计日志分两层写入。** 新记录先落 `audit-log/spool/*.jsonl`，之后才合并进 `audit-log/YYYY-MM-DD.jsonl`。只读其中一处会漏掉最新记录的请求时间戳，必须两处都读。

**4. 积分是流式累加的**，不是请求结束后一次性写入。正在进行的那一轮只能看到部分值，因此要标注"未定稿"。同一个会话隔几十秒查两次，数字会变，这是正常的。

**5. `used` / `size` 是上下文 token 占用**，不是积分。`used` 的确切含义（当前值还是峰值）没有权威说明，对外只能说"上下文占用"，不要做进一步推断。另外它是**会回落的**——上下文压缩后实测从 39 万掉到 11 万，所以它只是当前快照，不能读成"这个对话累计处理了多少"；要表达累计量级应该用 token 明细里的输入总量。

**6. 单轮成本不单调递减。** 受两个变量支配：前缀缓存是否命中、这一轮任务有多重。长时间闲置后首轮会明显变贵——实测空闲两小时十九分后那轮是前一轮的 9 倍。所以：

- 边际成本用**最近 N 轮已结算**的均值，不用全程均值
- "再聊 N 轮"必须带前提，措辞挂靠"最近三轮"
- 不给"这个对话最终会花多少"这类点估计，那是算不出来的

## 抗摔

表不存在、字段缺失、`credit_json` 损坏时，逐项兜底，给出人话提示，不抛堆栈。结构性不匹配明确报"当前版本不支持"，不静默算错。

**算错比报错糟糕。** 用户不会核对数字，他会直接信。时间戳拿不到时留空，不要猜一个填上。

## 隐私

只读本机文件，不发起任何网络请求。
