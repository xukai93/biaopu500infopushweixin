# 标普500 每日行情 · 微信推送

每天北京时间 07:00 自动获取标普500指数（S&P 500 / ^SPX）**前一交易日**的行情，生成一条排版好的消息推送到微信。基于 GitHub Actions 定时运行，无需常驻服务器，本机关机也能收到。

> 美股在北京时间凌晨收盘，07:00 取到的正是「前一天」的收盘行情。

## 消息包含

- 收盘价、涨跌点数与幅度、开盘 / 最高 / 最低 / 前收
- 日内区间位置条：收盘价落在当日高低区间的什么位置
- 近 5 个交易日迷你走势（Unicode 方块）+ 区间涨跌幅
- 可选的每日定投持仓盈亏粗估
- 涨红跌绿（中国习惯）

消息示例（纯文本渠道）：

```text
📉 标普500 -0.35% · 7,592.40

📊 标普500 · 每日行情
2026年11月12日（周四） · 美股收盘
────────────────────────
　收盘价　 7,592.40
　🟢 ▼ -26.98 点（-0.35%）
　小幅下跌　·　前收 7,619.38
────────────────────────
　开盘价　　　7,550.84
　最高价　　　7,652.76
　最低价　　　7,550.84

　日内位置　█████░░░░░░ 41%
　近 5 日　▁▆▅█▇ +2.92%
```

## 文件结构

| 路径 | 说明 |
| :--- | :--- |
| `sp500_daily_report.py` | 主脚本：取数、生成消息、推送 |
| `requirements.txt` | Python 依赖 |
| `.github/workflows/daily.yml` | GitHub Actions 定时配置 |

## 部署

### 1. 添加 Secret

**Settings → Secrets and variables → Actions → New repository secret**

| Name | 说明 |
| :--- | :--- |
| `SERVERCHAN_SENDKEY` | 使用默认的 Server酱渠道时必填 |

密钥只通过环境变量注入运行中的容器，**不要写进代码、日志或 README**。Secret 建好后不可再次查看，日志中会自动掩码为 `***`。

### 2. 选择推送渠道

编辑 `sp500_daily_report.py` 顶部的 `PUSH_CHANNEL`：

| 值 | 渠道 | 消息落在哪 | 需要的凭据 |
| :--- | :--- | :--- | :--- |
| `serverchan` | Server酱（默认） | 微信服务号消息 | `SERVERCHAN_SENDKEY` |
| `wecom_app` | 企业微信自建应用 | 微信「我的企业」/ 企业微信 | CorpID、Secret、AgentId |
| `wecom_webhook` | 企业微信群机器人 | 群聊 | Webhook Key |

> 微信官方没有开放「给个人微信号主动发消息」的接口。Server酱本质是托管了一个公众号；企业微信两个渠道走的是腾讯官方 `qyapi.weixin.qq.com`。切换渠道时，请同样把对应凭据改为从环境变量读取并配置 Secret，切勿硬编码。

三种渠道各有独立模板：Server酱用标准 Markdown（支持表格），企业微信 markdown 不支持表格，微信插件只支持纯文本。不要混用。

### 3. 定时任务

`.github/workflows/daily.yml` 默认配置：

```yaml
on:
  schedule:
    - cron: '0 23 * * *'   # UTC 23:00 = 北京时间次日 07:00
  workflow_dispatch:
```

- GitHub Actions 的 cron 默认按 **UTC**，北京时间需减 8 小时。
- 建议在 workflow 中设置 `env: TZ: Asia/Shanghai`，否则脚本内的 `datetime.now()` / `date.today()` 按 UTC 计算，日期与时间戳会差一天。
- 整点是平台高负载时段，可能延迟或丢弃排队作业；把分钟挪开（如 `3 23 * * *`）更稳。
- 公开仓库 **60 天无任何 commit 会自动禁用定时任务**，偶尔提交一次即可保活。
- 只想在交易日运行，可把 cron 改成 `... * * 1-5`（注意 UTC 与北京日期差一天）。

改好后到 **Actions → 选中 workflow → Run workflow** 手动触发一次，日志出现 `✔ 推送成功！` 即配置完成。

## 本地运行

```bash
pip install -r requirements.txt

export SERVERCHAN_SENDKEY="你的 SendKey"     # Windows: set / $env:
python sp500_daily_report.py --dry-run       # 只预览，不推送
python sp500_daily_report.py                 # 实际推送
python sp500_daily_report.py --preview-all   # 预览三种渠道的模板
```

要求 Python ≥ 3.10。

## 主要配置

均在 `sp500_daily_report.py` 顶部：

| 变量 | 说明 |
| :--- | :--- |
| `PUSH_CHANNEL` | 推送渠道，默认 `serverchan` |
| `HIST_DAYS` | 迷你走势取几个交易日，默认 `5` |
| `INVEST_ENABLED` | 是否显示定投估算区块 |
| `INVEST_PER_WORKDAY` | 每工作日定投金额 |
| `INVEST_BASE` / `INVEST_BASE_DATE` | 定投基准金额与基准日，用于按工作日推算累计投入 |

## 数据源

| 用途 | 来源 |
| :--- | :--- |
| 实时行情 | 腾讯财经 `qt.gtimg.cn/q=usINX` |
| 近 5 日日 K | 腾讯财经 `web.ifzq.gtimg.cn/appstock/app/usfqkline/get` |
| 备用 | 雅虎财经 chart API |

均为免费公开接口，无需 API Key。历史数据获取失败时会自动省略走势图，不影响主消息推送。

## 注意事项

- **定投盈亏仅为粗估**：按「累计投入 × 指数涨跌幅」计算。QDII 基金净值确认滞后 1~2 个交易日且含人民币汇率影响，**不等于账户里的实际盈亏**，请以理财通显示为准。
- 本项目仅用于个人行情提醒，不构成任何投资建议。
