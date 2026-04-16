# repo-pulse

抓取 GitHub 热门项目并推送到飞书的助手项目。

## Setup

项目目标 Python 版本为 `3.12+`。推荐使用 `uv` 管理环境与依赖：

```bash
brew install uv
```

1. 复制环境变量模板并按需填写：
   ```bash
   cp .env.example .env
   ```
2. 安装依赖：
   ```bash
   uv sync --dev
   ```
3. 准备本地数据目录（SQLite 默认写入 `./data/app.db`）：
   ```bash
   mkdir -p data
   ```

## Local Run

```bash
uv run uvicorn repo_pulse.main:create_app --factory --host 0.0.0.0 --port 9527
```

启动时会装配真实运行时容器，完成：

- SQLite 建表初始化
- APScheduler 日报任务注册
- GitHub 发现 / 排行 / 飞书推送链路装配
- 飞书官方 SDK 长连接客户端启动
- 群内 slash 命令处理链路装配
- 飞书文档创建与内容同步

## Commands

飞书群里默认同时支持 slash 文本命令和 `@机器人` 形式的旧触发：

- `/a <repo|url|keyword>` 或 `/analyze <repo|url|keyword>`：生成项目详情
- `/d [topN]` 或 `/daily [topN]`：触发日榜
- `/w [topN]` 或 `/weekly [topN]`：触发周榜
- `/h` 或 `/help`：查看帮助

说明：

- 也支持 `@机器人 日榜 [topN]`、`@机器人 周榜 [topN]`、`@机器人 <repo|url|keyword>` 这种旧触发
- 上面的 `@机器人` 只是占位符，请以群里的实际机器人显示名为准
- 所有已识别命令执行时（如 `/a`、`/d`、`/w`、`/h` 及其长别名），机器人都会先给原消息加一个 `Typing` 表情，完成后自动移除
- 默认使用飞书官方 SDK 长连接接收群消息；如需临时回退 HTTP 回调，可设置 `FEISHU_LONG_CONNECTION_ENABLED=false`
- 如需关闭旧的 `@机器人` 兼容模式，可设置 `FEISHU_ALLOW_LEGACY_MENTION_COMMANDS=false`
- 榜单结果默认启用短期缓存：日榜 `2h`、周榜 `24h`，可通过 `DAILY_DIGEST_CACHE_TTL_SECONDS` / `WEEKLY_DIGEST_CACHE_TTL_SECONDS` 调整
- 项目详情默认缓存 `24h`，可通过 `DETAIL_CACHE_TTL_SECONDS` 调整；过期后会重新研究并复用已有飞书文档

## Tests

```bash
uv run pytest -q
```

## Manual Dry Run

用 CLI 验证 `run-digest --dry-run` 参数解析：

```bash
uv run python -m repo_pulse.cli run-digest --dry-run
```

## Docker Run

当前 Docker 镜像会直接启动完整 FastAPI 运行时：

- `create_app()` 启动时自动创建 runtime container
- 初始化数据库与日报调度器
- `/internal/run-digest` 可直接触发完整日报链路
- 日报会通过飞书 `post + md` 发送更稳定的富文本摘要（含 emoji / 列表 / 链接）
- 日报里“一句话”会优先翻成中文后再推送
- 飞书群消息默认通过官方 SDK 长连接进入真实详情处理逻辑，并回传详情摘要与最佳实践

当前限制：

- 若默认百炼未配置 `DASHSCOPE_API_KEY`，详情研究会返回明确失败信息，日报主链路仍可运行
- `/h` 帮助中的“关于我”链接可通过 `FEISHU_ABOUT_DOC_URL` 配置；留空时不会展示该入口，代码里不内置默认地址
- 若配置了 `FEISHU_DOC_FOLDER_TOKEN`，详情文档会创建到指定文件夹；不配则创建到应用可写根目录
- 长连接模式仍需在飞书开放平台订阅 `im.message.receive_v1` 并发布应用版本

## Research Provider

详情研究现在默认使用阿里百炼（中国北京区域 `qwen-deep-research`）：

```bash
RESEARCH_PROVIDER=dashscope
DASHSCOPE_API_KEY=your_dashscope_key
DASHSCOPE_MODEL=qwen-deep-research
DASHSCOPE_STRUCTURER_MODEL=qwen-plus
DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/api/v1
DASHSCOPE_RESEARCH_TIMEOUT_SECONDS=600
DASHSCOPE_STRUCTURER_TIMEOUT_SECONDS=600
DASHSCOPE_RESEARCH_MAX_RETRIES=2
DASHSCOPE_RESEARCH_RETRY_BACKOFF_SECONDS=1
```

说明：

- `qwen-deep-research` 负责联网深度调研
- `qwen-plus` 负责把研究报告整理成系统内部需要的结构化 JSON
- 默认超时为研究阶段 `600s`、结构化阶段 `600s`；如果你的网络或模型响应更慢，可以继续在 `.env` 里调大
- 研究报告阶段默认会对可恢复的流式网络错误做 `2` 次有限重试，退避间隔默认为 `1s`、`2s`
- 如需切回 OpenAI，可显式设置 `RESEARCH_PROVIDER=openai`
- 详情调研提示词会注入“仓库一手证据”（README 摘要、近期提交、版本发布等）；缺失信息会明确标注“信息不足以确认”
- 引用策略默认优先官方来源（仓库 / docs / blog / release notes），社区资料只作为补充

## New Config

以下环境变量可用于控制详情缓存与仓库证据采样上限（括号内为默认值）：

- `FEISHU_ABOUT_DOC_URL`（可选）：`/h` 帮助中“关于我介绍”的目标文档链接；留空时不展示该入口
- `DETAIL_CACHE_TTL_SECONDS`（`86400`）：项目详情缓存 TTL（秒）
- `DASHSCOPE_RESEARCH_MAX_RETRIES`（`2`）：研究报告阶段的最大重试次数
- `DASHSCOPE_RESEARCH_RETRY_BACKOFF_SECONDS`（`1`）：研究报告阶段重试退避基数（秒）
- `RESEARCH_README_CHAR_LIMIT`（`4000`）：README 截断字符上限
- `RESEARCH_RELEASE_LIMIT`（`3`）：抓取最近 Release 的数量上限
- `RESEARCH_COMMIT_LIMIT`（`5`）：抓取最近 Commit 的数量上限

## Follow-Up Work

“先回复已受理，稍后再推送最终研究结果”的异步交付模式需要独立的队列与状态跟踪方案（如任务持久化、状态查询、失败重试与回传机制），该能力已明确不在本次计划范围内。

使用 compose 启动服务（自动读取 `.env`，并挂载 `./data`）：

```bash
docker compose up -d --build
docker compose ps
curl http://127.0.0.1:9527/healthz
```

或直接构建并运行镜像：

```bash
docker build -t repo-pulse .
docker run --rm --env-file .env -p 9527:9527 -v "$(pwd)/data:/app/data" repo-pulse
```

补充说明：

- Dockerfile 已改为 `uv sync --frozen --no-dev`，通常会比 `pip install .` 更快
- 镜像内置了 `HEALTHCHECK`，会定期探活 `/healthz`
- `docker-compose.yml` 已配置 `restart: unless-stopped`
