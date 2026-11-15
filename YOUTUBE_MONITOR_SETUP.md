# YouTube 频道更新监控 · 部署与配置参考

> 配套文件：`youtube_channel_monitor.py`、`youtube.yml`
> 沿用标普500项目的同一仓库 `xukai93/biaopu500infopushweixin`，**不新建项目**。

---

## 一、原理（30 秒版）

```
cron-job.org（每 2 小时）
   └─ POST → GitHub workflow_dispatch 接口
        └─ GitHub Actions 跑 youtube_channel_monitor.py
             ├─ 读 YouTube 官方 RSS（每频道最近约 15 条）
             ├─ 与上次记录的 videoId 比对 → 找出新视频
             └─ 有新视频 → 汇总成一条消息 → Server酱 → 微信
```

**为什么用 RSS 而不是 yt-dlp / YouTube Data API**
- 官方 RSS 地址 `feeds/videos.xml?channel_id=UC...`，**无需 API Key、无需配额、无需登录**；
- 不解析易变的网页结构，不会被 YouTube 前端改版打断；
- 唯一成本：需要先知道 `channel_id`（脚本会自动解析并缓存，见第三节）。

**为什么需要状态文件**：不记住"上次见过哪些视频"，每次运行都会把同一批视频重复推送。状态存在 `youtube_state.json`，由 GitHub Actions 的 cache 跨次运行保留。

---

## 二、部署文件（3 步）

| 本地文件 | 放到仓库哪里 | 说明 |
| :--- | :--- | :--- |
| `youtube_channel_monitor.py` | 仓库根目录 | 主脚本 |
| `youtube.yml` | `.github/workflows/youtube.yml` | 新建 workflow |
| — | `requirements.txt` **不用改** | 只依赖已有的 `requests==2.34.2` |

再把这行加进仓库 `.gitignore`（状态文件由云端 cache 管理，不必进仓库）：
```
youtube_state.json
```

**Secret 不用新建**：直接复用现有的 `SERVERCHAN_SENDKEY`（`Settings → Secrets and variables → Actions`）。脚本从环境变量读取，代码里没有任何明文密钥。

commit + push 到 main 即生效。

---

## 三、⚠️ 首次部署必做：云端连通性自检

本机（国内网络）到 YouTube 是 502，**本地无法验证真实抓取**；GitHub 境外 runner 通常可以访问，但频道页可能触发反爬。所以第一步一定在云端做自检：

1. GitHub → **Actions** → 左侧选 **YouTube 频道更新监控**
2. 点右侧 **Run workflow** → `mode` 选 **`resolve-only`** → 绿色按钮运行
3. 点进这次 run → **Run monitor** 这一步看日志

日志会打印每个频道是 `[OK]` 还是 `[FAIL]`，并在末尾生成这样一段：

```
FEED_URL_OVERRIDES = {
    "@JiangFengTimes": "https://www.youtube.com/feeds/videos.xml?channel_id=UCxxxx",
    ...
}
```

**建议把这段贴回脚本顶部的 `FEED_URL_OVERRIDES`**（替换掉空的 `{}`）再 push。这样做之后，脚本每次运行只请求 RSS 端点、完全不碰频道页，是最稳的形态。以后加新频道，再跑一次 `resolve-only` 补一行即可。

> 若某个频道是 `[FAIL]`：浏览器打开该频道页 → `Ctrl/Cmd + U` 查看源代码 → 搜索 `channelId` 或 `rss+xml` → 把 `UC...` 那一串填进 overrides 对应位置。
> 若 **全部频道都 FAIL**，说明 GitHub 的 IP 段被 YouTube 挡了，需要换网络出口（自建 VPS 跑 cron，或给 Actions 配代理），RSS 端点本身通常不会受限。

---

## 四、先看效果，再开定时

自检通过后，按这个顺序手动跑两次（都在 Actions 的 Run workflow 里选 mode）：

| 次序 | mode | 预期结果 |
| :--- | :--- | :--- |
| 1 | `normal` | **首次运行只建基线**，微信收到一条「📺 频道监控已启动 · 11 个频道」，不会把 11 个频道的历史视频一次性刷屏 |
| 2 | `test-push` | 抓真实数据，强制把每个频道最新 1 条当新视频推一条 → **在手机上看真实排版**；此模式不改状态 |

确认排版 OK 再开定时任务。之后正常模式**没有新视频就不推送**，不打扰。

其他可用的 mode：
- `init` — 新增频道 / 状态错乱后重建基线（不推历史视频）
- `dry-run` — 全流程跑一遍只打印，不推送、不写状态

---

## 五、cron-job.org 加第二个定时任务

标普500 那个任务保持不动，**新建一个任务**指向本 workflow。

- **Title**：`YouTube channel monitor`
- **URL**（注意结尾是 `youtube.yml`，不是 `daily.yml`）：
  ```
  https://api.github.com/repos/xukai93/biaopu500infopushweixin/actions/workflows/youtube.yml/dispatches
  ```
- **Request method**：`POST`
- **Headers**：逐条点 **Add header**，每条出来两格，按下表填

  | # | 第一格（Header name） | 第二格（Header value） |
  | :-- | :--- | :--- |
  | 1 | `Accept` | `application/vnd.github+json` |
  | 2 | `Authorization` | `Bearer ` + 一个空格 + 你的 PAT，形如 `Bearer github_pat_xxxx` |
  | 3 | `X-GitHub-Api-Version` | `2022-11-28` |
  | 4 | `Content-Type` | `application/json` |

  > 第 2 条最容易错：`Bearer` 后面必须有**一个空格**，且 `github_pat_xxxx` 要整个替换成真 token（不带尖括号）。**PAT 可以直接复用标普500那个**——fine-grained PAT 授权的是整个仓库的 Actions 读写权限，对两个 workflow 都有效。

- **Request body**：
  ```json
  {"ref":"main"}
  ```
  不带 `inputs`，GitHub 会用 workflow 里的默认值 `normal`，正是定时任务要的。
- **Schedule**：
  - Timezone：`Asia/Shanghai`
  - Expression：`7 */2 * * *` → 每 2 小时的第 7 分钟（00:07、02:07 … 22:07，共 12 次/天）。错开整点是习惯，不是必须。
  - 想省事可改 `0 8,13,19,23 * * *`（一天 4 次）；YouTube RSS 本身有约 15–30 分钟延迟，不建议低于 1 小时间隔。
- **Save** → 点 **Run now** 测一次，期望 `204 No Content`，GitHub Actions 出现新 run。

> **PAT 到期会同时影响两个定时任务**，届时重新生成 token 后，两个任务的 Header 都要更新。

---

## 六、状态持久化：原理与边界

`youtube.yml` 里 `actions/cache@v4`：
- `key: yt-state-${{ github.run_id }}` → 每次运行存一份新 cache；
- `restore-keys: yt-state-` → 下次运行取最近那份。

**cache 有可能被 GitHub 清理**（7 天未访问 / 总量超 10GB）。脚本做了第二层保护：只认 `LOOKBACK_HOURS = 48` 小时内发布的视频。所以最坏情况也只是**把最近两天的视频重推一次**，绝不会把几个月前的老视频批量推出去。

如果希望 100% 可靠，可把状态写回仓库（改用 GitHub Contents API + `contents: write` 权限），代价是每次运行产生一个 commit。当前 12 次/天，会觉得吵，**默认用 cache 就够了**。

---

## 七、常用参数（脚本配置区）

| 参数 | 默认 | 作用 |
| :--- | :--- | :--- |
| `CHANNELS` | 11 个 @handle | 增删频道改这里；中文 handle 直接写中文 |
| `FEED_URL_OVERRIDES` | `{}` | 手动指定 RSS，跳过频道页解析（**推荐填**） |
| `LOOKBACK_HOURS` | `48` | 只把最近 N 小时的视频视为"新"，状态丢失时的兜底 |
| `MAX_VIDEOS_PER_CHANNEL` | `5` | 单条消息里每频道最多列几条，防刷屏 |
| `NOTIFY_WHEN_EMPTY` | `False` | 改成 `True` 会在无更新时也推一条（默认关，免打扰） |
| `NOTIFY_ON_INIT` | `True` | 首次建基线时推一条"监控已启动" |
| `PUSH_CHANNEL` | `serverchan` | 可切 `wecom_app` / `wecom_webhook`，模板已按渠道语法分离 |

---

## 八、排障速查

| 现象 | 原因 / 处理 |
| :--- | :--- |
| 首次运行推了上百条 | 不会发生：首次自动建基线，只推"监控已启动"。若真出现，检查 cache 是否把旧 state 丢了 |
| 每天都推同样的视频 | state 没保住 → 看 Actions 日志里 `Restore state` 是否 `Cache hit`；同时确认 `LOOKBACK_HOURS` 生效 |
| 某频道一直 `[FAIL]` | 频道页反爬或 handle 写错 → `--resolve-only` 看报错 → 手动填 `FEED_URL_OVERRIDES` |
| 全部频道 FAIL | GitHub IP 被 YouTube 挡 → 换出口（VPS/代理），RSS 端点通常不受限 |
| 微信没收到 | 先确认 Actions run 成功、日志有 `✔ 推送成功`；再看 Server酱推送日志；最后检查 Secret 是否填了**轮换后的新 Key** |
| 定时不触发 | 与标普500同因：确认 cron-job.org 任务 URL 结尾是 `youtube.yml`、PAT 没过期、workflow 名为 `youtube.yml` 且已在 main 分支 |
| 时间戳差 8 小时 | workflow 的 `env: TZ: Asia/Shanghai` 被删了，补回来 |

---

## 九、安全提醒

- 本脚本**不含任何明文密钥**，全部走 `os.environ`；`SERVERCHAN_SENDKEY` 复用仓库级 Secret。
- `FEED_URL_OVERRIDES` 里只有公开的 channel_id，不是敏感信息，可以进公开仓库。
- 与标普500项目共用一个 public 仓库 → 记得 60 天保活；两个 workflow 共用同一份 Secrets 和 `requirements.txt`。
- ⚠️ **遗留 P0**：标普500脚本早期把旧 SendKey 明文推进过 public 仓库的历史 commit，历史记录永久可见。**必须确认 GitHub Secret 里填的是轮换后的新 Key**，否则前面所有脱敏改造等于白做。

---

## 十、可选：README 增补一段

在仓库 README 加一节即可：

```markdown
## YouTube 频道更新监控

监控一组 YouTube 频道，有新视频时汇总推送到微信。

- 数据源：YouTube 官方 RSS，无需 API Key
- 触发：cron-job.org 定时调用本仓库 workflow_dispatch 接口
- 配置：在 `youtube_channel_monitor.py` 的 `CHANNELS` 增删频道
- 密钥：复用仓库 Secret `SERVERCHAN_SENDKEY`
- 手动测试：Actions → YouTube 频道更新监控 → Run workflow，可选 mode
```
