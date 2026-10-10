"""看板业务操作：网页和 AI 共用同一套逻辑（校验、公司名匹配、时间规整、记历史、微信推送）"""
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

import storage as st

PLACEHOLDER_POSITIONS = {"", "待补充", "（待补充）", "(待补充)"}
INTERVIEW_RESULTS = ("待定", "通过", "已挂")


class BoardError(Exception):
    """操作参数有误或找不到公司，消息会原样展示给用户 / 返回给 AI"""


PUSH_STATUSES = {"interview", "offer"}   # 免费版 Server 酱每天额度很少，只推重要节点和提醒


def push_notification(title, content):
    """Server 酱微信推送，未配置 SERVERCHAN_KEY 时什么也不做；返回是否成功"""
    key = os.getenv("SERVERCHAN_KEY")
    if not key:
        return False
    try:
        data = urllib.parse.urlencode({"title": title[:32], "desp": content}).encode("utf-8")
        urllib.request.urlopen(urllib.request.Request(f"https://sctapi.ftqq.com/{key}.send", data=data), timeout=8)
        return True
    except urllib.error.HTTPError as e:
        print(f"推送失败: HTTP {e.code} {e.read()[:200].decode('utf-8', 'replace')}")
    except Exception as e:
        print(f"推送失败: {e}")
    return False


# ---------- 时间规整 ----------
_DT = re.compile(r"(?:(\d{4})-)?(\d{1,2})-(\d{1,2})(?:[ T]+(\d{1,2}):(\d{2})(?::\d{2})?)?")


def _parse(value):
    s = str(value).strip()
    s = re.sub(r"[年月/.]", "-", s).replace("日", " ").replace("号", " ").replace("：", ":")
    s = re.sub(r"\s+", " ", s).strip()
    m = _DT.fullmatch(s)
    if not m:
        raise BoardError(f"看不懂的时间「{value}」，请用 2026-09-24 22:37 这样的格式")
    year = int(m[1] or datetime.now().year)
    try:
        return datetime(year, int(m[2]), int(m[3]), int(m[4] or 23), int(m[5] or 59)), bool(m[4])
    except ValueError:
        raise BoardError(f"不存在的日期「{value}」")


def norm_deadline(value):
    """统一成 2026-09-24T22:37（网页 datetime-local 的格式）；None 表示不改，空字符串表示清空；只有日期时取当天 23:59"""
    if value is None:
        return None
    if not str(value).strip():
        return ""
    return _parse(value)[0].strftime("%Y-%m-%dT%H:%M")


def norm_date(value):
    if value is None:
        return None
    if not str(value).strip():
        return ""
    return _parse(value)[0].strftime("%Y-%m-%d")


def norm_time(value):
    if value is None:
        return None
    s = str(value).strip().replace("：", ":")
    if not s:
        return ""
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", s)
    if not m or int(m[1]) > 23 or int(m[2]) > 59:
        raise BoardError(f"看不懂的时间「{value}」，请用 15:00 这样的格式")
    return f"{int(m[1]):02d}:{m[2]}"


def label(status):
    return st.STATUS.get(status, status or "")


# ---------- 公司卡片 ----------
def _split_positions(text):
    return [p.strip() for p in re.split(r"[、,，;；/\n]+", text or "") if p.strip() and p.strip() not in PLACEHOLDER_POSITIONS]


def _merge_positions(existing, new):
    items = _split_positions(existing)
    for p in _split_positions(new):
        if p not in items:
            items.append(p)
    return "、".join(items) or "（待补充）"


def _locate(cards, company):
    card, candidates = st.find_card(cards, company)
    if card is None and candidates:
        names = "、".join(c["company"] for c in candidates)
        raise BoardError(f"「{company}」可能指 {names}，请说具体是哪一家")
    return card


def update_company(company, new_company=None, positions=None, add_positions=None, status=None,
                   note=None, append_note=None, deadline=None, applied_at=None, create=True, source="网页"):
    """
    新增或更新一家公司。参数为 None 表示不改；deadline / note 传空字符串表示清空。
    返回一句给人看的结果描述。
    """
    company = (company or "").strip()
    if not company:
        raise BoardError("没有给出公司名")
    if status is not None and status not in st.STATUS:
        raise BoardError(f"无效状态「{status}」，可选：{'、'.join(f'{k}={v}' for k, v in st.STATUS.items())}")
    deadline, applied_at = norm_deadline(deadline), norm_date(applied_at)

    with st.lock:
        cards = st.load_cards()
        card = _locate(cards, company)

        if card is None:
            if not create:
                raise BoardError(f"看板里没有「{company}」")
            name = (new_company or company).strip()
            card = {
                "id": st.next_id(cards),
                "company": name,
                "positions": _merge_positions(positions or "", add_positions or ""),
                "status": status or "submitted",
                "deadline": deadline or "",
                "note": "；".join(x for x in (note, append_note) if x) or "",
                "applied_at": applied_at or datetime.now().strftime("%Y-%m-%d"),
                "updated_at": st.now_iso(),
            }
            cards.append(card)
            st.save_cards(cards)
            st.append_history(name, "", card["status"], f"{source}新增")
            return f"新增「{name}」（{label(card['status'])}）"

        old_name, old_status = card["company"], card.get("status", "")
        changes = []

        if new_company and new_company.strip() and new_company.strip() != old_name:
            target = new_company.strip()
            clash = [c for c in cards if c is not card and st.norm_company(c["company"]) == st.norm_company(target)]
            if clash:
                raise BoardError(f"看板里已经有「{clash[0]['company']}」了，不能改成同名")
            card["company"] = target
            _rename_interviews(old_name, target)
            changes.append(f"改名为「{target}」")
        if positions is not None:
            merged = _merge_positions("", positions)
            if merged != card.get("positions"):
                card["positions"] = merged
                changes.append(f"岗位改为「{merged}」")
        if add_positions:
            merged = _merge_positions(card.get("positions", ""), add_positions)
            if merged != card.get("positions"):
                card["positions"] = merged
                changes.append(f"岗位 +{add_positions}")
        if status and status != old_status:
            card["status"] = status
            changes.append(f"{label(old_status)} → {label(status)}")
        if note is not None and note != card.get("note", ""):
            card["note"] = note
            changes.append(f"备注改为「{note}」" if note else "清空备注")
        if append_note and append_note not in card.get("note", ""):
            card["note"] = "；".join(x for x in (card.get("note", ""), append_note) if x)
            changes.append(f"备注 +「{append_note}」")
        if deadline is not None and deadline != card.get("deadline", ""):
            card["deadline"] = deadline
            changes.append(f"截止时间 {deadline.replace('T', ' ')}" if deadline else "清空截止时间")
        if applied_at is not None and applied_at != card.get("applied_at", ""):
            card["applied_at"] = applied_at
            changes.append(f"投递日期 {applied_at}")

        if not changes:
            return f"「{card['company']}」无需改动"
        card["updated_at"] = st.now_iso()
        st.save_cards(cards)
        st.append_history(card["company"], old_status, card["status"], append_note or note or "；".join(changes))
        if card["status"] != old_status and card["status"] in PUSH_STATUSES:
            emoji = "🎉" if card["status"] == "offer" else "🎤"
            push_notification(f"{emoji} {card['company']} {label(card['status'])}", f"从「{label(old_status)}」→「{label(card['status'])}」\n\n{card.get('note', '')}")
        return f"「{old_name}」：" + "，".join(changes)


def delete_company(company):
    with st.lock:
        cards = st.load_cards()
        card = _locate(cards, company)
        if card is None:
            raise BoardError(f"看板里没有「{company}」")
        st.save_cards([c for c in cards if c is not card])
        st.append_history(card["company"], card.get("status"), "deleted", "删除")
        return f"已删除「{card['company']}」"


# ---------- 面试记录 ----------
def is_mock(iv):
    return "模拟" in (iv.get("round") or "")


def _rename_interviews(old, new):
    ivs = st.load_interviews()
    changed = False
    for iv in ivs:
        if iv.get("company") == old:
            iv["company"] = new
            changed = True
    if changed:
        st.save_interviews(ivs)


def save_interview(company=None, id=None, position=None, round=None, date=None, time=None,
                   result=None, questions=None, reflection=None, source="网页"):
    """
    新增或修改面试记录。没给 id 时，同公司同轮次且结果待定的记录视为同一场（改时间不会重复建）。
    新约的面试（结果待定、日期不早于今天）会把公司状态自动改为「面试中」。
    """
    date, time = norm_date(date), norm_time(time)
    if result is not None and result not in INTERVIEW_RESULTS:
        raise BoardError(f"面试结果只能是 {'、'.join(INTERVIEW_RESULTS)}")

    with st.lock:
        cards = st.load_cards()
        ivs = st.load_interviews()
        if id:
            iv = next((x for x in ivs if str(x.get("id")) == str(id)), None)
            if iv is None:
                raise BoardError(f"没有编号为 {id} 的面试记录")
        else:
            company = (company or "").strip()
            if not company:
                raise BoardError("没有给出公司名")
            card = _locate(cards, company)
            company = card["company"] if card else company
            round = (round or "一面").strip()
            iv = None if "模拟" in round else next(
                (x for x in ivs if st.norm_company(x.get("company")) == st.norm_company(company)
                 and x.get("round") == round and x.get("result", "待定") == "待定"), None)
            if iv is None:
                iv = {"id": st.next_id(ivs), "company": company, "position": "", "round": round, "date": "", "time": "",
                      "questions": "", "reflection": "", "result": "待定", "created_at": st.now_iso()}
                ivs.append(iv)

        fields = {"company": company if id and company else None, "position": position, "round": round if id else None,
                  "date": date, "time": time, "result": result, "questions": questions, "reflection": reflection}
        for k, v in fields.items():
            if v is not None:
                iv[k] = v
        iv["updated_at"] = st.now_iso()
        st.save_interviews(ivs)

    when = " ".join(x for x in (iv.get("date"), iv.get("time")) if x) or "时间未定"
    summary = f"面试记录：{iv['company']} {iv.get('round', '')}｜{when}｜{iv.get('result', '待定')}"

    if is_mock(iv):
        return summary
    upcoming = iv.get("result", "待定") == "待定" and (not iv.get("date") or iv["date"] >= datetime.now().strftime("%Y-%m-%d"))
    card, _ = st.find_card(st.load_cards(), iv["company"])
    if upcoming and card and card.get("status") in ("submitted", "pending", "waiting"):
        summary += "；" + update_company(card["company"], status="interview", source=source)
    return summary


def delete_interview(id):
    with st.lock:
        ivs = st.load_interviews()
        left = [x for x in ivs if str(x.get("id")) != str(id)]
        if len(left) == len(ivs):
            raise BoardError(f"没有编号为 {id} 的面试记录")
        st.save_interviews(left)
    return "已删除面试记录"
