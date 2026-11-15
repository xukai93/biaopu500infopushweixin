# YouTube 频道更新监控 · 部署与配置参考

> 配套文件：`youtube_channel_monitor.py`、`youtube.yml`
> 沿用标普500项目的同一仓库 `xukai93/biaopu500infopushweixin`，**不新建项目**。

---

## 一、原理（30 秒版）

```
cron-job.org（每 2 小时）
   └─ POST → GitHub workflow_dispatch 接口
        └─ GitHub Actions 跑 youtube_channel_monitor.py
             ├─ 读频道最新约 15 条视频（见下方三级数据源）
             ├─ 与上次记录的 videoId 比对 → 找出新视频
             └─ 有新视频 → 汇总成一条消息 → Server酱 → 微信
```

**数据源（三级自动降级，零配置即可用）**

| 优先级 | 数据源 | 何时用 | 说明 |
| :--- | :--- | :--- | :--- |
| 1 | **Data API v3** | 配了 `YOUTUBE_API_KEY` | Google 官方计费接口，有 SLA、返回绝对时间。见第十一节 |
| 2 | **`youtubei/v1 browse`（默认）** | 没配 Key | YouTube 网页端自己加载视频列表用的接口，用网页里**公开的** INNERTUBE_API_KEY，**无需申请任何东西**。实测 11 个频道全部 HTTP 200、各 30 条 |
| 3 | **RSS `feeds/videos.xml`** | 前两者都失败 | 兜底。该端点在部分出口持续 404/500，不作为主路径 |

> ⚠️ 踩过的坑：`feeds/videos.xml` 在 GitHub runner 上从「前 4 个过、后 7 个 404」恶化到**全部 404**，重试无效。逐项核验确认 11 个 channel_id 全对（页面内 `rss+xml`/`canonical`/`externalId` 三处一致），是端点本身的问题。所以本版把默认源换成 browse。
>
> 频道页 `ytInitialData` 只有框架、不含视频列表（视频是 XHR 加载的），但 **browse 正是那个 XHR**，所以能拿到数据。

**11 个 channel_id 与中文频道名均已核实并预置**，脚本不抓频道页，每次运行只有 11 次数据请求；频道名硬编码，不依赖响应结构。

browse 只给相对时间（「23小时前」「6个月前」），脚本换算成近似 UTC 用于 48 小时窗口与排序，消息里仍显示原始文本更直观。

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

## 三、首次部署必做：云端连通性自检

本机（国内网络）到 YouTube 是 502，**本地无法验证真实抓取**；`channel_id` 已核实预置，所以自检的目的只剩一个：**确认 GitHub runner 能拉到数据**。

1. GitHub → **Actions** → 左侧选 **YouTube 频道更新监控**
2. 点右侧 **Run workflow** → `mode` 选 **`resolve-only`** → 绿色按钮运行
3. 点进这次 run → **Run monitor** 这一步看日志

期望输出：

```
数据源：browse 网页接口（零配置，默认）　|　兜底：RSS　|　预置 channel_id：11 个
      └ browse: 15 条
[OK]   @JiangFengTimes      江峰时刻  ·  15 条
[OK]   @Tankman2020         二爷故事  ·  15 条
...
成功 11/11 个频道。
```

`[OK]` 前面的 `└ browse: 15 条` 表示实际走的是哪个数据源。如果看到 `└ RSS:` 说明 browse 没成功、降级了，需要看上面的降级原因。

单频道失败**不会阻断其他频道**，也不会更新该频道的 state，下次运行会自动补检。若**全部频道持续失败**，说明 runner 出口连 browse 也被挡，此时按文末「十一、可选升级」配置 `YOUTUBE_API_KEY` 切到官方 API（加一个 Secret 即可，不用改代码）。

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
| `FEED_URL_OVERRIDES` | 已预置 11 个 | 硬编码 channel_id，跳过频道页解析；新增频道时补一行 |
| `LOOKBACK_HOURS` | `48` | 只把最近 N 小时的视频视为"新"，状态丢失时的兜底 |
| `FETCH_DELAY` | `2.0` | 相邻两个频道之间的间隔（秒），规避限流 |
| `RSS_RETRIES` | `2` | RSS 兜底路径的重试次数（已知该端点常挂，不死等） |
| `YOUTUBE_API_KEY` | 空 | 填了优先走官方 Data API v3；不填走零配置 browse。见第十一节 |
| `CHANNEL_NAMES` | 11 个中文名 | 消息里显示的频道名，不依赖响应结构 |
| `INNERTUBE_API_KEY` | 网页公开 key | browse 接口使用，写死在 YouTube 页面里，**不是私有凭据**，不用改 |
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
| 日志出现 `└ RSS: 15 条` | 正常降级（browse 没成功）。只要 `[OK]` 就不影响使用，想查原因看 `[FAIL]` 里 browse 的报错 |
| RSS 报 404/500 | 已知端点问题，**不是 ID 错**；browse 成功即可，别再调大 RSS 重试次数 |
| 全部 FAIL、错误含 `browse 请求失败` | runner 出口被 YouTube 挡 → 按第十一节配 `YOUTUBE_API_KEY` |
| `browse 返回 0 条视频` | 页面结构变了；脚本已兼容 `lockupViewModel`（新版）与 `videoRenderer`（旧版），仍失败就配 API Key |
| 日志里 `channel_id` 看着不对 | 先别改：11 个 ID 已核实（页面内三处一致）。想复核可浏览器打开频道页 → 查看源代码搜 `rss+xml` |
| 单次运行耗时变长 | 正常：11 频道 × 2 秒间隔 + 偶发降级重试。workflow 超时已放宽到 20 分钟 |
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

- 数据源：YouTube 网页端 browse 接口（零配置），可切官方 Data API v3；11 个 channel_id 已预置
- 触发：cron-job.org 定时调用本仓库 workflow_dispatch 接口
- 配置：在 `youtube_channel_monitor.py` 的 `CHANNELS` / `FEED_URL_OVERRIDES` 增删频道
- 密钥：复用仓库 Secret `SERVERCHAN_SENDKEY`
- 手动测试：Actions → YouTube 频道更新监控 → Run workflow，可选 mode
```

---

## 十一、可选加固：官方 Data API v3（browse 被挡时启用）

默认走零配置的 browse 接口，通常不需要这一节。若自检里 11 个频道**全部 FAIL**、错误指向 browse 被 runner 出口拦截，加一个 Secret 就能切到官方接口，**不用改代码、不用改 workflow**。

**为什么稳**：browse 是网页端内部接口、RSS 端点会限流，而 Data API v3 是 Google 官方计费接口，有 SLA，返回 `publishedAt` 绝对时间。频道 ID 以 `UC` 开头，其「上传」播放列表就是把 `UC` 换成 `UU`，因此每个频道只需 1 次请求。

**申请步骤（约 5 分钟，免费）**
1. 打开 <https://console.cloud.google.com/> → 新建项目（名字随意，如 `yt-monitor`）
2. 左侧 **API 和服务 → 库** → 搜 `YouTube Data API v3` → **启用**
3. **API 和服务 → 凭据 → 创建凭据 → API 密钥** → 复制生成的 Key
4. 建议点该 Key → **API 限制** → 只勾选 `YouTube Data API v3`（丢了也用不到别处）
5. GitHub 仓库 → **Settings → Secrets and variables → Actions → New repository secret**
   - Name：`YOUTUBE_API_KEY`
   - Value：粘贴刚才的 Key
6. 重新跑一次 `mode=resolve-only`，日志首行应变成 `数据源：Data API v3（首选）`，且 11 个频道全 `[OK]`

> 脚本读 `os.environ["YOUTUBE_API_KEY"]`，`youtube.yml` 已注入该 Secret。**没配置时 Secret 展开为空字符串，自动回落到零配置的 browse 接口**，所以这一步完全可选、可随时回退（删掉 Secret 即可）。
>
> 配额：免费 10,000 units/天。本脚本每频道 1 次 `playlistItems.list`（1 unit），11 频道 × 12 次/天 ≈ **132 units/天**，远低于上限。
>
> 本地想试：`export YOUTUBE_API_KEY=你的Key` 再跑 `--dry-run`（但本机网络需能连 googleapis.com）。
