# GPU 申请提醒

规则：GPU 只在每月 1 号 / 15 号生效，至少提前 15 天申请。
目标：截稿前 15 天、或 rebuttal 开始当天，GPU 已经到位。
在 GPU 申请提交截止前 **7 天** 和 **3 天** 各提醒一次，消息里列出 Abstract deadline、Full submission deadline、Rebuttal start date。

## 本地试用
```bash
python gpu_reminder.py plan                      # 看完整时间表
python gpu_reminder.py check --dry-run           # 看今天会发什么
python gpu_reminder.py check --dry-run --date 2026-12-01   # 模拟某天
python gpu_reminder.py ics -o calendar.ics       # 生成日历文件
```
需要 Python 3.11+，无第三方依赖。

## 部署（推荐：GitHub Actions，零服务器）
1. 新建一个 GitHub 仓库，把这三个文件推上去（含 `.github/workflows/gpu-reminder.yml`）。
2. Settings → Secrets and variables → Actions，按需添加：
   - Slack：`SLACK_WEBHOOK_URL`（Slack 里建一个 App → Incoming Webhooks → 选频道，复制 URL）
   - 邮件：`SMTP_HOST`、`SMTP_PORT`、`SMTP_USER`、`SMTP_PASSWORD`、`MAIL_TO`
     （Gmail 用 smtp.gmail.com / 465 + 应用专用密码；学校邮箱看 IT 文档）
3. Actions 页面手动 Run 一次确认能收到。

## 日历订阅
- 一次性：`python gpu_reminder.py ics` 后把 calendar.ics 导入 Google/Outlook/Apple 日历。
- 自动更新：workflow 每天会把 calendar.ics 提交回仓库。仓库公开时，用
  `https://raw.githubusercontent.com/<用户>/<仓库>/main/calendar.ics` 在日历里“通过 URL 订阅”。
  （私有仓库可开 GitHub Pages 或改为手动导入。）

## 维护
- 会议官方公布日期后，改 `conferences.toml` 里对应会议的 `abstract` / `paper` / `rebuttal`，并把 `estimated` 改成 `false`。
- 新会议/新一届直接追加一个 `[[conferences]]`（三个日期都可选）。
- 提醒节点改 `remind_offsets`；截稿按 abstract 还是 full paper 算改 `deadline_basis`。
- 如果确认学校规则是“生效日前满 15 天即可”（不需要在 1 号/15 号前交），把 `align_submission` 改成 `false`。
