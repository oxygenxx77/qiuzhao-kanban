"""数据文件读写：原子写入、自动备份、撤销快照、公司名匹配"""
import hashlib
import json
import os
import re
import shutil
import threading
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, "kanban_data.json")
HISTORY_FILE = os.path.join(BASE_DIR, "history.json")
CHAT_HISTORY_FILE = os.path.join(BASE_DIR, "chat_history.json")
INTERVIEW_FILE = os.path.join(BASE_DIR, "interviews.json")
PROFILE_FILE = os.path.join(BASE_DIR, "user_profile.json")
REMINDER_FILE = os.path.join(BASE_DIR, "reminders_sent.json")
BACKUP_DIR = os.path.join(BASE_DIR, "backups")
UNDO_DIR = os.path.join(BACKUP_DIR, "undo")
MAX_BACKUPS = 30
MAX_UNDO = 50
MAX_CHAT_HISTORY = 200

# 投递流程的先后顺序，也是看板排序和统计的顺序
STATUS = {
    "pending": "待测评",
    "interview": "面试中",
    "waiting": "等结果",
    "submitted": "已投递",
    "offer": "Offer",
    "ended": "已结束",
}

# 所有写操作串行执行（网页、AI、求职助手可能同时写）
lock = threading.RLock()

# 看板 / 面试 / 历史数据写入后依次调用（sqlmirror 用它同步 kanban.db）
on_change = []


def _changed():
    for fn in on_change:
        fn()


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def read_json(path, default=None):
    if default is None:
        default = {} if path in (PROFILE_FILE, REMINDER_FILE) else []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path, data):
    """先写临时文件再替换，写到一半断电也不会把文件写坏"""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _stamp():
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")


def _prune(folder, prefix, keep):
    files = sorted(f for f in os.listdir(folder) if f.startswith(prefix))
    for f in files[:-keep]:
        os.remove(os.path.join(folder, f))


# ---------- 看板 ----------
def load_cards():
    return read_json(DATA_FILE)


def save_cards(cards):
    with lock:
        if os.path.exists(DATA_FILE):
            os.makedirs(BACKUP_DIR, exist_ok=True)
            shutil.copy2(DATA_FILE, os.path.join(BACKUP_DIR, f"kanban_data_{_stamp()}.json"))
            _prune(BACKUP_DIR, "kanban_data_", MAX_BACKUPS)
        write_json(DATA_FILE, cards)
        _changed()


def next_id(items):
    """取最大编号 + 1（用条数 + 1 会在删除后撞号）"""
    return str(max((int(x["id"]) for x in items if str(x.get("id", "")).isdigit()), default=0) + 1)


# ---------- 面试记录 ----------
def load_interviews():
    return read_json(INTERVIEW_FILE)


def save_interviews(items):
    with lock:
        write_json(INTERVIEW_FILE, items)
        _changed()


# ---------- 状态变更历史（周报、时间线用） ----------
def load_history():
    return read_json(HISTORY_FILE)


def append_history(company, old_status, new_status, note=""):
    with lock:
        h = load_history()
        h.append({"timestamp": now_iso(), "company": company, "old_status": old_status or "",
                  "new_status": new_status or old_status or "", "note": note or ""})
        write_json(HISTORY_FILE, h)
        _changed()


# ---------- 聊天记录 ----------
def load_chat():
    return read_json(CHAT_HISTORY_FILE)


def append_chat(role, content, actions=None, undo_id=None, images=None):
    with lock:
        h = load_chat()
        item = {"role": role, "content": content, "timestamp": now_iso()}
        if images:
            item["images"] = images   # 只存前端生成的小缩略图，原图不落盘
        if actions:
            item["actions"] = actions
        if undo_id:
            item["undo_id"] = undo_id
        h.append(item)
        write_json(CHAT_HISTORY_FILE, h[-MAX_CHAT_HISTORY:])


def clear_chat():
    write_json(CHAT_HISTORY_FILE, [])


# ---------- 撤销快照：AI 每次改动前存一份看板 + 面试记录 ----------
class UndoConflict(Exception):
    """快照之后看板又被改过，撤销会把那些改动一起回退"""


def _state_hash():
    raw = json.dumps([load_cards(), load_interviews()], ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _undo_path(sid):
    if not re.fullmatch(r"\d{8}_\d{6}_\d{6}", sid or ""):
        raise ValueError("无效的撤销编号")
    return os.path.join(UNDO_DIR, f"undo_{sid}.json")


def snapshot():
    with lock:
        os.makedirs(UNDO_DIR, exist_ok=True)
        sid = _stamp()
        write_json(_undo_path(sid), {"cards": load_cards(), "interviews": load_interviews()})
        _prune(UNDO_DIR, "undo_", MAX_UNDO)
        return sid


def mark_after(sid):
    """记下这次改完之后的状态，撤销时用来判断中间有没有别的改动"""
    with lock:
        path = _undo_path(sid)
        snap = read_json(path, {})
        if snap:
            snap["after"] = _state_hash()
            write_json(path, snap)


def restore(sid, force=False):
    path = _undo_path(sid)
    if not os.path.exists(path):
        raise FileNotFoundError("这次操作的快照已过期，无法撤销")
    with lock:
        snap = read_json(path, {})
        if not force and snap.get("after") and snap["after"] != _state_hash():
            raise UndoConflict("这次修改之后看板还有别的改动，撤销会把它们一起回退")
        save_cards(snap.get("cards", []))
        save_interviews(snap.get("interviews", []))


# ---------- 公司名匹配（与求职助手 kanban.py 的规则一致） ----------
_CITY = re.compile(r"^(北京|上海|广州|深圳|杭州|成都|武汉|南京|西安|苏州|天津|重庆|长沙|厦门|珠海|东莞|佛山)(市)?")
_SUFFIX = re.compile(r"(（.*?）|\(.*?\)|股份有限公司|有限责任公司|有限公司|集团|控股|股份|公司|信息技术|信息科技|网络科技|科技|技术|网络|信息|中国)")


def norm_company(name):
    s = re.sub(r"\s+", "", name or "").lower()
    s = _SUFFIX.sub("", _CITY.sub("", s))
    return s or re.sub(r"\s+", "", name or "").lower()


def _overlap(a, b):
    return len(a) >= 2 and len(b) >= 2 and (a in b or b in a)


def find_card(cards, name):
    """
    找公司卡片：规整后完全相同优先；否则只有一张卡片互相包含时才算命中
    （「金山」能找到「金山办公」，但「网易」同时像「网易」「网易互娱」时以完全相同的为准）
    返回 (卡片, 候选列表)；找不到时卡片为 None，候选列表非空表示有歧义
    """
    key = norm_company(name)
    if not key:
        return None, []
    exact = [c for c in cards if norm_company(c.get("company")) == key]
    if exact:
        return exact[0], []
    partial = [c for c in cards if _overlap(norm_company(c.get("company")), key)]
    if len(partial) == 1:
        return partial[0], []
    return None, partial
