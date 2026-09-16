# wb-credits

[![tests](https://github.com/Snow7-G/wb-credits/actions/workflows/tests.yml/badge.svg)](https://github.com/Snow7-G/wb-credits/actions/workflows/tests.yml)

[中文](README.md) | **English**

Per-session credit usage for WorkBuddy.

WorkBuddy's built-in usage page shows a running total and a flat list of requests. It can't answer "how much did *this conversation* cost?" This plugin reads the local database, adds up the credits of every request in a session, and fills that gap.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/card-dark.svg">
  <img alt="wb-credits card: 142.85 credits over 39 rounds, 98% cache hit rate, with a token breakdown and the last three rounds" src="docs/card-light.svg">
</picture>

The card follows your theme, so the image above switches with your system's light/dark setting. Everything is local and read-only — no network calls, nothing uploaded.

**Tested on** WorkBuddy desktop 5.3.14 (macOS). The data comes from the client's internal storage, not a public API, so other versions aren't guaranteed to work. If it can't read the data it says so explicitly rather than returning a wrong number.

The install path has been verified end-to-end in a **clean, isolated environment**: add the marketplace from GitHub → install the plugin → run the script from where it landed. The commit it pulled matched the latest one in the repository.

> The card text is in Chinese, because WorkBuddy is a Chinese product and so is its user base. The plugin itself has no other locale.

## Install

**WorkBuddy desktop** — no clone, no command line:

1. Open **Skills** (it lives under the Experts area)
2. Switch to the **Plugins** tab (套件)
3. Click the **＋** button to the right of the marketplace tabs — its tooltip reads "Add Marketplace" (添加市场)
4. Enter `Snow7-G/wb-credits` as the marketplace source and submit
5. Find the `wb-credits` card and click the **＋** on its right to install

Then **start a new conversation**.

> Plugins are not under a menu called "Plugin Management" — they live under **Skills → Plugins**. That's easy to miss, which is why it's spelled out here.

The source field accepts four forms:

| Form | When to use |
|---|---|
| `owner/repo` | A GitHub repository — the usual case |
| `git@github.com:owner/repo.git` | SSH |
| `./path/to/marketplace` | A local directory; edits take effect immediately, handy for development |
| `https://.../marketplace.zip` | A ZIP archive |

The desktop app does not implement the `/plugin` slash commands — typing them into the chat box just sends them as a normal message. Same for `/reload-plugins`. Plugin config is only read at session startup, so you must open a new conversation or restart for it to load.

**CodeBuddy Code (CLI)**:

```
/plugin marketplace add Snow7-G/wb-credits
/plugin install wb-credits@snow7g
/reload-plugins
```

For local development, point the first line at your working copy instead: `./path/to/wb-credits`

**How to tell it loaded**: ask the agent to invoke the `wb-credits` skill. If you get `Can not find skill`, it isn't loaded — open a new conversation and try again.

## Usage

```
/credits                   Card for the current conversation
/credits --all             Ranked list of all sessions
/credits -k KEYWORD        Filter by session title
/credits --detail          Add per-request detail
/credits --estimate        Add an estimated credit split across token types
/credits --no-tokens       Skip the token breakdown; database only
```

You can also just ask in plain language — "where did my credits go this month?" — and the skill triggers on its own.

Some flag combinations fail on purpose, because they can't do anything sensible: `--all --format card` (there's no card template for a ranking), `--no-tokens --estimate` (the estimate needs token data), and a negative `--limit` (in a slice that means "trim from the end", silently returning fewer rows). `--limit 0` means no limit. **Quietly handing back something other than what you asked for is worse than saying no.**

The script also runs on its own, outside the chat:

```bash
python3 plugins/wb-credits/skills/wb-credits/scripts/wb_credits.py --format text
python3 plugins/wb-credits/skills/wb-credits/scripts/wb_credits.py --all --format json
```

## What the four numbers mean

**Total credits** is the headline — it's the first thing you see. The round count and context size below it are supporting detail.

**Cache hit rate** gets its own line, because it explains the price. A high hit rate means the money went into *re-sending the context every round*, not into output. If you want to spend less, shrink the context; don't tell the model to talk less.

**The last three rounds** give you a sense of pace, and project it forward one step. The "10 more rounds" estimate is anchored to the last three *settled* rounds, not the overall average — per-round cost is driven by two things (whether the prefix cache hit, and how heavy the round was), and the overall average gets dragged up by early heavy rounds. When nothing has settled yet it reads "waiting for the first round to settle" rather than showing a zero.

**The footer only appears when there's something to say.** Two cases: an unusually expensive round (with the reason, e.g. `14:29 single round 5.18 (idle 2h19m)`), or incomplete data (`usage detail is corrupt, total may be understated`). If it can't determine a cause, it stays quiet.

## Token breakdown

Three rows in the lower left of the card, with the hit rate on the line under the headline number:

```
积分｜39 轮｜缓存命中 98%｜上下文 25 万

未缓存输入        226 万
缓存命中         8840 万
输出               31 万
```

The data comes from the trace files under `~/.workbuddy/traces/`. Two things to know:

**There is no "reasoning" token count.** The client's traces don't record any reasoning-related field. It isn't available — not a permissions issue, the data doesn't exist.

**It's on by default, and it's cheap.** The `sessionId` and `modelInfo` fields both sit within the first 400 bytes of each file, so only the file header needs reading: about **42 ms** for 571 files / 462 MB, versus 1 second when parsing each file in full. If the header doesn't contain the fields it falls back to reading the whole file, so nothing is silently dropped. Add `--no-tokens` to skip it entirely and get down to about 30 ms.

Adding `--estimate` appends one more row, splitting the total across the three token types:

```
按比例估算        27 / 105 / 11 积分
```

**That row is an estimate, not a measurement.** It distributes the total using an assumed price ratio of 1 : 0.1 : 3 for uncached input, cached input, and output. The ratio varies by model and hasn't been calibrated. Don't treat it as exact.

## Where the data comes from

`~/.workbuddy/workbuddy.db` on your machine, two tables:

| Table | Contents |
|---|---|
| `sessions` | Session metadata: title, working directory, model, timestamps |
| `session_usage` | Usage per session; `credit_json` is `{"requestId": credits}` |

Summing every value in a session's `credit_json` gives that session's total credits. The client owns and maintains these tables; the plugin only reads them.

It opens the database read-only (`file:...?mode=ro`) so a running client isn't disturbed. It makes no network calls.

## Design tradeoffs

**Marginal cost uses the last three rounds, not the overall average.** The overall average gets dragged up by early heavy rounds and produces an estimate that's too high.

**It won't tell you what a conversation will ultimately cost.** That depends on how many more rounds you have in you. It can't be computed. Printing a number would be a lie.

**Anomalies are only reported when a cause can be named.** Right now one case is recognized: cache rebuild after an idle gap. If no cause can be determined, it says nothing rather than inventing a reason.

**A wrong number is worse than an error.** When the table structure doesn't match, it fails loudly. When a timestamp is missing, it stays blank. It never guesses a value to fill the space. Users don't double-check numbers — they believe them.

## Known limitations

- Depends on the client's internal data structures, not a public API. If an upgrade changes the schema, the plugin fails with a clear error.
- The exact meaning of the `used` field (current value or peak) isn't documented anywhere. The output labels it "context usage" and doesn't infer further. One measured detail worth adding: it **drops** when the context gets compacted (observed 390k → 110k), so it's a snapshot, not a running total of everything processed.
- Audit logs are written in two layers, so some requests have no precise timestamp. Those are left blank.
- Requires WorkBuddy to have been run at least once and to have produced AI requests.

## Development

```bash
python3 tests/test_wb_credits.py
```

228 checks covering the data layer, metrics, rendering, CLI, argument edge cases, and error paths. The count is verified by the suite itself — get it wrong and CI turns red.

**The tests are self-contained**: they run entirely on synthetic fixtures and never read or write your real data, so results are identical on any machine. CI runs them on Python 3.9 and 3.13 — the lower and upper bounds that were actually verified locally (3.14 passes too).

The two card images in this README are generated by `docs/make_card.py` (standard library only). Re-run it after changing the card template:

```bash
python3 docs/make_card.py
```

## License

MIT
