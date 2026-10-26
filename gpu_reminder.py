#!/usr/bin/env python3
"""
GPU 申请提醒器 —— 只依赖 Python 3.11+ 标准库。

规则：GPU 只在每月 1 号 / 15 号生效，且至少提前 lead_days 天申请。
每个会议产生两次申请需求：
  截稿：need_by = paper(或 abstract) - deadline_buffer_days
  Rebuttal：need_by = rebuttal - rebuttal_buffer_days
  生效日 effective = need_by 之前（含）最近的 1 号或 15 号
  提交截止 apply_by = effective - lead_days（align_submission=true 时再向前对齐到 1 号/15 号）
在 apply_by 前 remind_offsets 天（默认 7 天、3 天）发提醒。

用法：
  python gpu_reminder.py plan                 # 打印所有会议的申请时间表
  python gpu_reminder.py check [--dry-run]    # 今天该提醒的就发 Slack / 邮件（给 cron 用）
  python gpu_reminder.py ics -o calendar.ics  # 生成日历文件（提醒同样是提前 7 天、3 天）
  可加 --date 2026-12-01 模拟某一天，--config 指定配置文件。

通知渠道（环境变量，配哪个发哪个，都不配就只打印）：
  SLACK_WEBHOOK_URL
  SMTP_HOST, SMTP_PORT(默认465), SMTP_USER, SMTP_PASSWORD, MAIL_TO(逗号分隔), MAIL_FROM(默认=SMTP_USER)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import smtplib
import sys
import tomllib
import urllib.request
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

DAY = dt.timedelta(days=1)
WEEKDAYS = "一二三四五六日"


# ---------- 1号/15号 周期工具 ----------
def cycle_floor(d: dt.date) -> dt.date:
    """d 之前（含）最近的 1 号或 15 号"""
    return d.replace(day=15) if d.day >= 15 else d.replace(day=1)


def cycle_ceil(d: dt.date) -> dt.date:
    """d 之后（含）最近的 1 号或 15 号"""
    if d.day in (1, 15):
        return d
    if d.day < 15:
        return d.replace(day=15)
    return (d.replace(day=28) + 4 * DAY).replace(day=1)


def apply_by_for(effective: dt.date, s: dict) -> dt.date:
    d = effective - s["lead_days"] * DAY
    return cycle_floor(d) if s["align_submission"] else d


# ---------- 数据结构 ----------
@dataclass
class Conference:
    name: str
    abstract: dt.date | None
    paper: dt.date | None
    rebuttal: dt.date | None
    estimated: bool
    note: str
    url: str


@dataclass
class Plan:
    conf: Conference
    purpose: str          # "截稿" / "Rebuttal"
    need_by: dt.date
    effective: dt.date
    apply_by: dt.date

    @property
    def title(self) -> str:
        return f"{self.conf.name}（{self.purpose}用）"


def make_plans(c: Conference, s: dict) -> list[Plan]:
    plans = []
    basis = c.abstract if s.get("deadline_basis") == "abstract" and c.abstract else c.paper
    for purpose, date, buf in (("截稿", basis, s["deadline_buffer_days"]),
                               ("Rebuttal", c.rebuttal, s["rebuttal_buffer_days"])):
        if date is None:
            continue
        need_by = date - buf * DAY
        eff = cycle_floor(need_by)
        plans.append(Plan(c, purpose, need_by, eff, apply_by_for(eff, s)))
    return plans


def load(path: Path) -> tuple[dict, list[Plan]]:
    cfg = tomllib.loads(path.read_text(encoding="utf-8"))
    s = cfg["settings"]
    plans: list[Plan] = []
    for raw in cfg["conferences"]:
        c = Conference(raw["name"], raw.get("abstract"), raw.get("paper"), raw.get("rebuttal"),
                       raw.get("estimated", False), raw.get("note", ""), raw.get("url", ""))
        plans += make_plans(c, s)
    return s, sorted(plans, key=lambda p: p.apply_by)


def today_in(s: dict) -> dt.date:
    return dt.datetime.now(ZoneInfo(s.get("timezone", "UTC"))).date()


# ---------- 文本 ----------
def fmt(d: dt.date | None) -> str:
    return "无 / 未公布" if d is None else f"{d:%Y-%m-%d}（周{WEEKDAYS[d.weekday()]}）"


def message(p: Plan, left: int) -> str:
    c = p.conf
    head = f"⏰ 还剩 {left} 天：请提交 GPU 申请 —— {p.title}"
    lines = [
        head,
        f"  Abstract deadline:         {fmt(c.abstract)}",
        f"  Full submission deadline:  {fmt(c.paper)}",
        f"  Rebuttal start date:       {fmt(c.rebuttal)}",
        f"  ▶ GPU 申请提交截止：{fmt(p.apply_by)}",
        f"  ▶ 生效日：{fmt(p.effective)}（需要 GPU 的日期：{fmt(p.need_by)}）",
    ]
    if c.estimated:
        lines.append("  ⚠ 部分日期为按往年推算的预估值，请核对官网")
    if c.note or c.url:
        lines.append("  " + " ".join(x for x in (c.note, c.url) if x))
    return "\n".join(lines)


def due_messages(plans: list[Plan], today: dt.date, s: dict) -> list[str]:
    return [message(p, (p.apply_by - today).days) for p in plans
            if (p.apply_by - today).days in s["remind_offsets"]]


# ---------- 通知渠道 ----------
def send_slack(text: str) -> None:
    req = urllib.request.Request(os.environ["SLACK_WEBHOOK_URL"],
                                 data=json.dumps({"text": f"```{text}```"}).encode(),
                                 headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=20).read()


def send_email(subject: str, body: str) -> None:
    host, user = os.environ["SMTP_HOST"], os.environ["SMTP_USER"]
    port = int(os.environ.get("SMTP_PORT") or "465")
    msg = EmailMessage()
    msg["Subject"], msg["From"] = subject, os.environ.get("MAIL_FROM") or user
    msg["To"] = os.environ["MAIL_TO"]
    msg.set_content(body)
    smtp_cls = smtplib.SMTP_SSL if port == 465 else smtplib.SMTP
    with smtp_cls(host, port, timeout=30) as smtp:
        if port != 465:
            smtp.starttls()
        smtp.login(user, os.environ["SMTP_PASSWORD"])
        smtp.send_message(msg)


# ---------- ICS 日历 ----------
def ics_escape(t: str) -> str:
    return t.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def ics_trigger(days_before: int) -> str:
    """全天事件从 0 点算起；换算成“提前 N 天的上午 9 点”"""
    return f"-P{days_before - 1}DT15H" if days_before > 0 else "PT9H"


def build_ics(plans: list[Plan], today: dt.date, s: dict) -> str:
    out = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//gpu-reminder//CN",
           "CALSCALE:GREGORIAN", "X-WR-CALNAME:GPU 申请提醒"]
    for p in plans:
        if p.apply_by < today:  # 已过提交截止的不再放进日历
            continue
        c = p.conf
        uid = f"{c.name}-{p.purpose}".replace(" ", "_") + "@gpu-reminder"
        desc = (f"Abstract deadline: {fmt(c.abstract)}\nFull submission deadline: {fmt(c.paper)}\n"
                f"Rebuttal start date: {fmt(c.rebuttal)}\n生效日: {fmt(p.effective)}\n"
                + ("部分日期为预估值，请核对官网\n" if c.estimated else "") + c.url)
        out += ["BEGIN:VEVENT", f"UID:{uid}",
                f"DTSTAMP:{p.apply_by:%Y%m%d}T000000Z",  # 固定值，避免每天都产生 diff
                f"DTSTART;VALUE=DATE:{p.apply_by:%Y%m%d}",
                f"DTEND;VALUE=DATE:{p.apply_by + DAY:%Y%m%d}",
                f"SUMMARY:{ics_escape('⚠️ GPU申请截止：' + p.title)}",
                f"DESCRIPTION:{ics_escape(desc)}"]
        if c.url:
            out.append(f"URL:{c.url}")
        for n in s["remind_offsets"]:
            out += ["BEGIN:VALARM", "ACTION:DISPLAY", f"TRIGGER:{ics_trigger(n)}",
                    f"DESCRIPTION:{ics_escape(f'还剩 {n} 天：GPU 申请 {p.title}')}", "END:VALARM"]
        out.append("END:VEVENT")
    out.append("END:VCALENDAR")
    return "\r\n".join(out) + "\r\n"


# ---------- CLI ----------
def main() -> int:
    ap = argparse.ArgumentParser(description="GPU 申请提醒")
    ap.add_argument("cmd", choices=["plan", "check", "ics", "selftest"])
    ap.add_argument("--config", default=Path(__file__).with_name("conferences.toml"), type=Path)
    ap.add_argument("--date", help="模拟今天的日期 YYYY-MM-DD")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不发送")
    ap.add_argument("-o", "--output", default="calendar.ics")
    a = ap.parse_args()

    s, plans = load(a.config)
    today = dt.date.fromisoformat(a.date) if a.date else today_in(s)

    if a.cmd == "selftest":
        s1 = {"lead_days": 15, "align_submission": True}
        assert cycle_floor(dt.date(2026, 11, 14)) == dt.date(2026, 11, 1)
        assert apply_by_for(dt.date(2026, 11, 1), s1) == dt.date(2026, 10, 15)  # 你给的例子
        assert cycle_ceil(dt.date(2026, 12, 20)) == dt.date(2027, 1, 1)
        print("selftest ok")
        return 0

    if a.cmd == "plan":
        print(f"今天 {fmt(today)}；提前 {s['lead_days']} 天，"
              f"{'提交截止对齐 1/15 号' if s['align_submission'] else '严格按天数'}，"
              f"提醒节点 {s['remind_offsets']} 天\n")
        for p in plans:
            left = (p.apply_by - today).days
            st = f"还剩 {left} 天" if left >= 0 else "已过"
            reminders = ", ".join(f"{p.apply_by - n * DAY:%m-%d}" for n in s["remind_offsets"])
            print(f"{p.title}{'（预估）' if p.conf.estimated else ''}\n"
                  f"    Abstract {p.conf.abstract or '-'} | Full {p.conf.paper or '-'} | "
                  f"Rebuttal {p.conf.rebuttal or '-'}\n"
                  f"    生效 {p.effective} ← 最晚提交 {p.apply_by}（{st}）；提醒日 {reminders}")
        return 0

    if a.cmd == "ics":
        Path(a.output).write_text(build_ics(plans, today, s), encoding="utf-8")
        print(f"写入 {a.output}")
        return 0

    # check
    msgs = due_messages(plans, today, s)
    if not msgs:
        print(f"{today}: 今天没有需要发的提醒")
        return 0
    text = "\n\n".join(msgs)
    print(text)
    if a.dry_run:
        return 0
    ok = True
    if os.environ.get("SLACK_WEBHOOK_URL"):
        try:
            send_slack(text)
        except Exception as e:  # noqa: BLE001
            print(f"Slack 发送失败: {e}", file=sys.stderr)
            ok = False
    if os.environ.get("SMTP_HOST"):
        try:
            send_email(f"[GPU 申请提醒] {today}", text)
        except Exception as e:  # noqa: BLE001
            print(f"邮件发送失败: {e}", file=sys.stderr)
            ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
