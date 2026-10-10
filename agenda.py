"""日程：今日待办、日历订阅（.ics）、截止 / 面试前的微信提醒、周报"""
import os
import threading
import time
from datetime import datetime, timedelta, timezone

import storage as st
from board import is_mock, label, push_notification

STALE_DAYS = 14          # 「等结果」超过这么多天没动静，提醒跟进
CHECK_INTERVAL = 300     # 提醒检查间隔（秒）
DIGEST_HOUR = 9          # 每天几点推送当日待办
DEADLINE_ALERTS = [(timedelta(hours=24), "24h"), (timedelta(hours=2), "2h")]
INTERVIEW_ALERTS = [(timedelta(hours=24), "24h"), (timedelta(hours=1), "1h")]


def _dt(value, fmt):
    try:
        return datetime.strptime(value, fmt)
    except (TypeError, ValueError):
        return None


def events(include_done=False):
    """
    看板截止时间 + 面试 → 统一的日程列表。
    截止时间只对「待测评」算待办（其它状态的截止时间多半是做完了没清掉）。
    """
    out = []
    for c in st.load_cards():
        at = _dt(c.get("deadline"), "%Y-%m-%dT%H:%M")
        if not at or c.get("status") == "ended" or (c.get("status") != "pending" and not include_done and at < datetime.now()):
            continue
        out.append({"kind": "deadline", "uid": f"deadline-{c['id']}", "company": c["company"], "at": at, "all_day": False,
                    "title": f"⏰ {c['company']} 截止", "detail": f"{c.get('positions', '')}｜{label(c.get('status'))}｜{c.get('note', '')}",
                    "todo": c.get("status") == "pending"})
    for iv in st.load_interviews():
        if not iv.get("date") or is_mock(iv) or (iv.get("result", "待定") != "待定" and not include_done):
            continue
        at = _dt(f"{iv['date']} {iv.get('time') or '00:00'}", "%Y-%m-%d %H:%M")
        if not at:
            continue
        out.append({"kind": "interview", "uid": f"interview-{iv['id']}", "company": iv["company"], "at": at,
                    "all_day": not iv.get("time"), "title": f"🎤 {iv['company']} {iv.get('round', '面试')}",
                    "detail": f"{iv.get('position', '')}｜{iv.get('result', '待定')}", "todo": iv.get("result", "待定") == "待定"})
    return sorted(out, key=lambda e: e["at"])


def _last_touch():
    """每家公司最后一次有动静的时间（状态历史 / 卡片更新时间）"""
    last = {}
    for h in st.load_history():
        t = _dt(h.get("timestamp", "")[:19], "%Y-%m-%dT%H:%M:%S")
        if t:
            last[h["company"]] = max(last.get(h["company"], t), t)
    for c in st.load_cards():
        t = _dt((c.get("updated_at") or "")[:19], "%Y-%m-%dT%H:%M:%S") or _dt(c.get("applied_at"), "%Y-%m-%d")
        if t:
            last[c["company"]] = max(last.get(c["company"], t), t)
    return last


def agenda():
    """今日待办：已过期 / 今天 / 明天 / 7 天内的截止和面试，以及久等无果的公司"""
    now = datetime.now()
    today = now.date()
    items = []
    for e in events():
        if not e["todo"]:
            continue
        day = e["at"].date()
        if e["kind"] == "deadline" and e["at"] < now:
            bucket = "overdue"
        elif e["kind"] == "interview" and day < today:
            bucket = "overdue"
        elif day == today:
            bucket = "today"
        elif day == today + timedelta(days=1):
            bucket = "tomorrow"
        elif day <= today + timedelta(days=7):
            bucket = "week"
        else:
            continue
        items.append({"kind": e["kind"], "company": e["company"], "title": e["title"], "detail": e["detail"],
                      "at": e["at"].strftime("%Y-%m-%dT%H:%M"), "all_day": e["all_day"], "bucket": bucket})

    last = _last_touch()
    stale = []
    for c in st.load_cards():
        t = last.get(c["company"])
        if c.get("status") == "waiting" and t and (now - t).days >= STALE_DAYS:
            stale.append({"company": c["company"], "days": (now - t).days, "note": c.get("note", "")})
    stale.sort(key=lambda x: -x["days"])
    return {"items": items, "stale": stale}


def agenda_text():
    """给小招看的待办摘要"""
    a = agenda()
    names = {"overdue": "已过期", "today": "今天", "tomorrow": "明天", "week": "7天内"}
    lines = [f"- [{names[i['bucket']]}] {i['title']} {i['at'].replace('T', ' ')}" for i in a["items"]]
    lines += [f"- [久等] {s['company']} 等结果 {s['days']} 天没动静" for s in a["stale"][:8]]
    return "\n".join(lines) or "（暂无）"


# ---------- 日历订阅 ----------
def _esc(text):
    return str(text).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line):
    """iCalendar 要求每行不超过 75 字节，超出部分换行并以空格开头"""
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line
    parts, cur = [], b""
    for ch in line:
        b = ch.encode("utf-8")
        if len(cur) + len(b) > (75 if not parts else 74):
            parts.append(cur.decode("utf-8"))
            cur = b""
        cur += b
    parts.append(cur.decode("utf-8"))
    return "\r\n ".join(parts)


def ics():
    fmt = "%Y%m%dT%H%M%S"
    stamp = datetime.now(timezone.utc).strftime(fmt) + "Z"
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//qiuzhao-kanban//CN", "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
             "X-WR-CALNAME:秋招手帐", "X-WR-TIMEZONE:Asia/Shanghai", "REFRESH-INTERVAL;VALUE=DURATION:PT15M", "X-PUBLISHED-TTL:PT15M"]
    for e in events(include_done=True):
        lines += ["BEGIN:VEVENT", f"UID:{e['uid']}@qiuzhao-kanban", f"DTSTAMP:{stamp}", f"SUMMARY:{_esc(e['title'])}",
                  f"DESCRIPTION:{_esc(e['detail'])}"]
        if e["all_day"]:
            lines += [f"DTSTART;VALUE=DATE:{e['at']:%Y%m%d}", f"DTEND;VALUE=DATE:{e['at'] + timedelta(days=1):%Y%m%d}"]
        elif e["kind"] == "deadline":
            lines += [f"DTSTART:{e['at'] - timedelta(minutes=30):{fmt}}", f"DTEND:{e['at']:{fmt}}"]
        else:
            lines += [f"DTSTART:{e['at']:{fmt}}", f"DTEND:{e['at'] + timedelta(hours=1):{fmt}}"]
        alarms = ["-PT24H", "-PT2H"] if e["kind"] == "deadline" else ["-PT1H"]
        for trigger in alarms:
            lines += ["BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{_esc(e['title'])}", f"TRIGGER:{trigger}", "END:VALARM"]
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(l) for l in lines) + "\r\n"


# ---------- 微信提醒 ----------
def check_reminders():
    """截止前 24h / 2h、面试前 24h / 1h 各推一次；每天早上推当日待办。已推送的记在 reminders_sent.json"""
    now = datetime.now()
    sent = st.read_json(st.REMINDER_FILE)
    changed = False

    for e in events():
        if not e["todo"] or e["all_day"]:
            continue
        left = e["at"] - now
        rules = DEADLINE_ALERTS if e["kind"] == "deadline" else INTERVIEW_ALERTS
        due = [tag for window, tag in rules if timedelta(0) < left <= window]
        keys = [f"{e['uid']}|{e['at']:%Y%m%d%H%M}|{tag}" for tag in due]
        if keys and not all(k in sent for k in keys):
            hours = left.total_seconds() / 3600
            when = f"还有 {hours:.0f} 小时" if hours >= 1 else f"还有 {left.seconds // 60} 分钟"
            if push_notification(f"{e['title']}（{when}）", f"{e['at']:%m-%d %H:%M}｜{e['detail']}"):
                for k in keys:
                    sent[k] = now.isoformat(timespec="seconds")
                changed = True

    digest_key = f"digest|{now:%Y-%m-%d}"
    if now.hour >= DIGEST_HOUR and digest_key not in sent:
        a = agenda()
        todo = [i for i in a["items"] if i["bucket"] in ("overdue", "today")]
        ok = True
        if todo or a["stale"]:
            lines = [f"- {i['title']} {i['at'][11:] if not i['all_day'] else ''}" for i in todo]
            lines += [f"- {s['company']} 已等 {s['days']} 天，可以问问进度" for s in a["stale"][:5]]
            ok = push_notification(f"🍂 今日秋招待办 {len(todo)} 项", "\n".join(lines))
        if ok:
            sent[digest_key] = now.isoformat(timespec="seconds")
            changed = True

    if changed:
        cutoff = (now - timedelta(days=30)).isoformat()
        st.write_json(st.REMINDER_FILE, {k: v for k, v in sent.items() if v >= cutoff})


def start_reminders():
    """后台线程定时检查；没配置 Server 酱就不启动"""
    if not os.getenv("SERVERCHAN_KEY"):
        return

    def loop():
        time.sleep(10)
        while True:
            try:
                check_reminders()
            except Exception as e:
                print(f"提醒检查失败: {e}")
            time.sleep(CHECK_INTERVAL)

    threading.Thread(target=loop, daemon=True, name="reminders").start()


# ---------- 周报 ----------
def weekly_report():
    now = datetime.now()
    week_ago = now - timedelta(days=7)
    cards = st.load_cards()
    recent = [h for h in st.load_history() if (_dt(h.get("timestamp", "")[:19], "%Y-%m-%dT%H:%M:%S") or now) >= week_ago]

    new = {h["company"] for h in recent if not h.get("old_status") and h.get("new_status") not in ("", "deleted")}
    new |= {c["company"] for c in cards if (c.get("applied_at") or "") >= week_ago.strftime("%Y-%m-%d")}
    moves = [h for h in recent if h.get("old_status") and h.get("new_status") != h.get("old_status") and h.get("new_status") != "deleted"]
    ivs = [iv for iv in st.load_interviews()
           if not is_mock(iv) and week_ago.strftime("%Y-%m-%d") <= (iv.get("date") or "") <= now.strftime("%Y-%m-%d")]
    mocks = [iv for iv in st.load_interviews() if is_mock(iv) and (iv.get("date") or "") >= week_ago.strftime("%Y-%m-%d")]
    upcoming = [e for e in events() if e["todo"] and now <= e["at"] and e["at"].date() <= (now + timedelta(days=7)).date()]
    count = {s: sum(1 for c in cards if c.get("status") == s) for s in st.STATUS}

    lines = [f"📅 {week_ago:%m.%d} – {now:%m.%d}"]
    lines.append(f"\n📮 新投递 {len(new)} 家" + (f"：{'、'.join(sorted(new))}" if new else ""))
    lines.append(f"\n🔄 状态推进 {len(moves)} 次")
    lines += [f"  {h['timestamp'][5:10]} {h['company']}：{label(h['old_status'])} → {label(h['new_status'])}" for h in moves[-15:]]
    lines.append(f"\n🎤 本周面试 {len(ivs)} 场")
    lines += [f"  {iv['date'][5:]} {iv['company']} {iv.get('round', '')}｜{iv.get('result', '待定')}" for iv in ivs]
    if mocks:
        lines.append(f"  另外做了 {len(mocks)} 次模拟面试")
    lines.append(f"\n⏰ 未来 7 天 {len(upcoming)} 项")
    lines += [f"  {e['at']:%m-%d %H:%M} {e['title']}" for e in upcoming]
    lines.append("\n📊 当前：" + "｜".join(f"{v} {count[k]}" for k, v in st.STATUS.items()))
    return "\n".join(lines)
