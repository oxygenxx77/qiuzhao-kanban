"""小招：AI 对话（通过工具调用直接改看板）、模拟面试、JD 抓取、周报点评"""
import json
import os
import re
from datetime import datetime

import agenda
import board
import jobhunter
import storage as st

MODEL = os.getenv("KANBAN_MODEL") or os.getenv("OPENAI_MODEL") or "ark-code-latest"
MAX_ROUNDS = 6          # 一轮对话里最多连续调用几次工具
HISTORY_MESSAGES = 12   # 带给 AI 的历史消息条数
WRITE_TOOLS = {"update_companies", "save_interview", "delete_company", "delete_interview"}

_client = None


def ai_enabled():
    return bool(os.getenv("OPENAI_API_KEY"))


def client():
    global _client
    if _client is None:
        from openai import OpenAI
        _client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"),
                         base_url=os.getenv("OPENAI_BASE_URL", "https://ark.cn-beijing.volces.com/api/coding/v3"),
                         timeout=180, max_retries=1)
    return _client


def complete(prompt, temperature=0.7, system=None):
    msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    r = client().chat.completions.create(model=MODEL, messages=msgs, temperature=temperature)
    return (r.choices[0].message.content or "").strip()


# ---------- 工具定义 ----------
_STATUS_ENUM = list(st.STATUS)
_STATUS_DESC = "，".join(f"{k}={v}" for k, v in st.STATUS.items())

TOOLS = [
    {"type": "function", "function": {
        "name": "update_companies",
        "description": "新增或更新看板里的公司，一次可以改多家。用户一句话里提到几家有进展的公司，就在 updates 里放几条，一次调用全部完成，一家都不能漏。看板里没有的公司会自动新增。",
        "parameters": {"type": "object", "properties": {"updates": {"type": "array", "items": {"type": "object", "properties": {
            "company": {"type": "string", "description": "公司名，用看板里的写法；简称也可以，系统会自动匹配"},
            "new_company": {"type": "string", "description": "改名后的公司名（用户说 A 就是 B、改名时用）"},
            "add_positions": {"type": "string", "description": "追加的岗位，多个用顿号分隔"},
            "positions": {"type": "string", "description": "整体替换岗位列表（只有用户要求改岗位时用）"},
            "status": {"type": "string", "enum": _STATUS_ENUM, "description": _STATUS_DESC},
            "append_note": {"type": "string", "description": "追加到备注末尾的简短进度，带日期，如「9.24 完成AI面」"},
            "note": {"type": "string", "description": "整体替换备注（只有用户要求改备注时用）"},
            "deadline": {"type": "string", "description": "截止时间 YYYY-MM-DD HH:MM；空字符串表示清空"},
            "applied_at": {"type": "string", "description": "投递日期 YYYY-MM-DD"},
        }, "required": ["company"]}}}, "required": ["updates"]},
    }},
    {"type": "function", "function": {
        "name": "save_interview",
        "description": "新增或修改面试记录。同一公司同一轮次、结果待定的面试会自动视为同一场（改时间不会重复）。新约的面试会自动把公司状态改成面试中。",
        "parameters": {"type": "object", "properties": {
            "company": {"type": "string"},
            "round": {"type": "string", "description": "一面 / 二面 / 三面 / 终面 / HR面 / 群面 / AI面"},
            "date": {"type": "string", "description": "YYYY-MM-DD"},
            "time": {"type": "string", "description": "HH:MM，没说具体时间就不填"},
            "position": {"type": "string"},
            "result": {"type": "string", "enum": list(board.INTERVIEW_RESULTS)},
            "questions": {"type": "string", "description": "面试题目"},
            "reflection": {"type": "string", "description": "复盘心得"},
            "id": {"type": "string", "description": "要修改的面试记录编号（可选）"},
        }, "required": ["company"]},
    }},
    {"type": "function", "function": {
        "name": "delete_company",
        "description": "从看板删除一家公司。只有用户明确说删除时才调用。",
        "parameters": {"type": "object", "properties": {"company": {"type": "string"}}, "required": ["company"]},
    }},
    {"type": "function", "function": {
        "name": "delete_interview",
        "description": "删除一条面试记录。只有用户明确说删除时才调用。",
        "parameters": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
    }},
    {"type": "function", "function": {
        "name": "web_search",
        "description": "联网搜索面经、公司、岗位、校招时间等最新信息",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    }},
]


def search_web(query, max_results=5):
    key = os.getenv("TAVILY_API_KEY")
    if not key:
        return "⚠️ 未配置 TAVILY_API_KEY，无法联网搜索"
    try:
        from tavily import TavilyClient
        r = TavilyClient(api_key=key).search(query=query, max_results=max_results,
                                             search_depth=os.getenv("TAVILY_SEARCH_DEPTH", "basic"))
        if not r.get("results"):
            return f"没有找到关于「{query}」的信息"
        return "\n".join(f"【{i}】{x.get('title', '')}\n来源：{x.get('url', '')}\n摘要：{x.get('content', '')}"
                         for i, x in enumerate(r["results"][:max_results], 1))
    except Exception as e:
        return f"搜索出错：{e}"


def _run(fn):
    try:
        return {"ok": True, "summary": fn()}
    except board.BoardError as e:
        return {"ok": False, "summary": str(e)}
    except Exception as e:
        return {"ok": False, "summary": f"操作失败：{e}"}


def execute_tool(name, args):
    """执行一个工具，返回 {"items": [{ok, summary}], "text": 给 AI 的结果}"""
    keep = lambda d, keys: {k: d[k] for k in keys if k in d and d[k] is not None}
    if name == "update_companies":
        fields = ("company", "new_company", "add_positions", "positions", "status", "append_note", "note", "deadline", "applied_at")
        items = [_run(lambda u=u: board.update_company(**keep(u, fields), source="小招"))
                 for u in args.get("updates") or [] if isinstance(u, dict)]
        if not items:
            items = [{"ok": False, "summary": "updates 为空，没有改任何公司"}]
    elif name == "save_interview":
        fields = ("company", "id", "position", "round", "date", "time", "result", "questions", "reflection")
        items = [_run(lambda: board.save_interview(**keep(args, fields), source="小招"))]
    elif name == "delete_company":
        items = [_run(lambda: board.delete_company(args.get("company")))]
    elif name == "delete_interview":
        items = [_run(lambda: board.delete_interview(args.get("id")))]
    elif name == "web_search":
        return {"items": [], "text": search_web(str(args.get("query", "")))}
    else:
        items = [{"ok": False, "summary": f"未知工具 {name}"}]
    return {"items": items, "text": json.dumps(items, ensure_ascii=False)}


# ---------- 上下文 ----------
def _now_text():
    now = datetime.now()
    return f"{now:%Y-%m-%d %H:%M} 星期{'一二三四五六日'[now.weekday()]}"


def build_system():
    cards = st.load_cards()
    order = list(st.STATUS)
    rows = sorted(cards, key=lambda c: (order.index(c["status"]) if c.get("status") in order else 9, c.get("deadline") or "9"))
    board_text = "\n".join(
        f"- {c['company']}｜{c.get('positions', '')}｜{board.label(c.get('status'))}｜"
        f"{(c.get('deadline') or '').replace('T', ' ') or '-'}｜{c.get('applied_at') or '-'}｜{c.get('note', '')}"
        for c in rows)
    stats = "，".join(f"{v} {sum(1 for c in cards if c.get('status') == k)}" for k, v in st.STATUS.items())

    ivs = st.load_interviews()
    today = datetime.now().strftime("%Y-%m-%d")
    shown = [iv for iv in ivs if (iv.get("date") or "9") >= today or iv.get("result", "待定") == "待定"] + ivs[-5:]
    seen, iv_lines = set(), []
    for iv in shown:
        if iv["id"] in seen:
            continue
        seen.add(iv["id"])
        iv_lines.append(f"- #{iv['id']} {iv['company']}｜{iv.get('round', '')}｜{iv.get('date') or '日期未定'} {iv.get('time', '')}｜"
                        f"{iv.get('position', '')}｜{iv.get('result', '待定')}")

    profile = st.read_json(st.PROFILE_FILE)
    profile_text = "\n".join(f"- {k}：{profile.get(f) or '未填'}" for k, f in
                             (("目标城市", "target_cities"), ("偏好岗位", "preferred_roles"),
                              ("不想去的公司", "avoid_companies"), ("其他", "notes"))) if profile else "（未填写）"
    jobs = jobhunter.job_summary(cards) or "（未找到求职助手数据）"
    resume = jobhunter.resume_text(1500) or "（未找到）"

    return f"""你是「小招」🍂，用户的秋招小助手：陪用户聊秋招、联网搜索，并用工具维护「秋招手帐」看板。
现在是 {_now_text()}。

## 看板（共 {len(cards)} 家：{stats}）
格式：公司｜岗位｜状态｜截止时间｜投递日期｜备注
{board_text}

## 面试记录（待定的和最近的）
{chr(10).join(iv_lines) or '（暂无）'}

## 近期待办
{agenda.agenda_text()}

## 用户偏好
{profile_text}

## 用户简历（摘要）
{resume}

## 求职助手岗位库
求职助手（http://127.0.0.1:8001）是用户的另一个工具，负责采集牛客岗位和 AI 匹配打分，在那里标记已投递的岗位会自动出现在看板上。
{jobs}

## 状态含义
pending=待测评（收到测评/笔试/AI面邀请还没做） interview=面试中（约了面试、在面试流程里） waiting=等结果（测评或面试做完了在等通知）
submitted=已投递（投了还没消息） offer=拿到 Offer ended=已结束（挂了、放弃、流程结束）

## 操作规则（最重要）
1. 用户只要讲了投递进展（投了、收到测评或面试通知、做完了、挂了、offer、放弃、改名、补岗位、截止时间……），就必须调用工具写进看板。
   一句话里提到几家公司，就在同一次 update_companies 的 updates 里放几条，一家都不能漏。
2. 没有调用工具并且成功，绝对不能说「已更新」「已记录」「已写入」之类的话；工具返回失败要如实告诉用户。
3. 时间一律以「现在」为准换算成绝对时间 YYYY-MM-DD HH:MM：「明天下午三点」→ 明天的日期 15:00；「5天内完成」→ 通知时间加 5 天；
   「9.24 22:37」→ 今年 9 月 24 日 22:37。没说时间的截止日期用 23:59。
4. 常见说法怎么操作：
   - 投了 X 的 A、B 岗 → status=submitted，add_positions="A、B"，applied_at=投递日期（没说就是今天）
   - X 发来测评/笔试/AI面，Y 前完成 → status=pending，deadline=Y，append_note="M.D 收到测评"
   - X 的测评/笔试/AI面做完了 → status=waiting，deadline=""（清空），append_note="M.D 完成测评"
   - X 约了一面，时间 T → save_interview(round=一面, date, time)，它会自动把状态改成面试中
   - X 一面面完了 → update_companies status=waiting，append_note="M.D 一面已面"
   - X 一面过了 / 挂了 → save_interview result=通过/已挂；挂了还要 update_companies status=ended
   - X 给了 offer → status=offer；放弃 X → status=ended，append_note="M.D 放弃"
   - A 就是 B / A 改名 B → update_companies company=A，new_company=B
   备注用 append_note 追加带日期的简短进度，不要覆盖原来的备注。
5. 公司名优先用看板里的写法；用户的简称（oppo、金山）直接传也行，系统会匹配。实在分不清是哪家时先问。
6. 只有用户明确要求时才删除公司或面试记录。
7. 改完之后用一两句话告诉用户改了什么，以工具返回的结果为准。
8. 用户发来截图（测评/面试通知邮件、短信、网申页面、offer 等）时，先认出公司、岗位、事件和时间，
   截图里的进展和用户说的话一样按规则 1~4 写进看板；截图看不清或不是求职进展时，说明看到了什么再问用户要做什么。

## 风格
亲切自然，简短有力，可以带点幽默和鼓励。回答求职问题时结合用户的偏好、简历和看板进度。
聊天窗口很窄：不要用 markdown 表格和 # 标题，用短句或「- 」列表，重点可以用 **加粗**。"""


def _history_messages():
    """取最近的聊天记录；AI 回复后附上当时实际执行的操作，避免它误以为做过没做的事"""
    out = []
    for h in st.load_chat()[-HISTORY_MESSAGES:]:
        content = h.get("content", "")
        if h.get("images"):
            content = (content + "\n" if content else "") + f"[当时附了 {len(h['images'])} 张图片，已看过]"
        if h.get("role") == "assistant" and h.get("actions"):
            content += "\n[本轮实际执行] " + "；".join(("✅" if a["ok"] else "⚠️") + a["summary"] for a in h["actions"])
        if content:
            out.append({"role": "user" if h.get("role") == "user" else "assistant", "content": content})
    return out


# 旧版格式：<action>{...}</action>，模型偶尔还会这么写，照样执行
_LEGACY = re.compile(r"<action>(.*?)</action>", re.DOTALL)
_SEARCH_TAG = re.compile(r"<search>(.*?)</search>", re.DOTALL)
# 嘴上说改了，实际没调用工具
_CLAIM = re.compile(r"(已经?|帮你|给你)(更新|记录|记下|写入|写进|新增|添加|标记|改成|改为|修改|录入|补上|补录|同步)|更新好了|记好了|改好了")


def _legacy_actions(reply):
    items = []
    for raw in _LEGACY.findall(reply):
        try:
            a = json.loads(raw.strip())
        except json.JSONDecodeError:
            items.append({"ok": False, "summary": "操作格式有误，没有执行"})
            continue
        if a.get("action") == "delete":
            items.append(_run(lambda: board.delete_company(a.get("company"))))
        else:
            items.append(_run(lambda: board.update_company(
                a.get("company"), new_company=a.get("new_company"), status=a.get("new_status"),
                append_note=a.get("new_note"), add_positions=a.get("positions"), source="小招")))
    return items


def _user_content(message, images):
    """有图片时按多模态格式组装：文字在前，图片在后"""
    if not images:
        return message
    text = message or "看看这张截图，有求职进展就更新看板。"
    return [{"type": "text", "text": text}] + [{"type": "image_url", "image_url": {"url": url}} for url in images]


def chat(message, images=None, thumbs=None):
    if not ai_enabled():
        return {"status": "disabled", "reply": "AI 功能未启用：在 .env 里填入 OPENAI_API_KEY 后重启即可。看板的其它功能都可以正常用。",
                "actions": [], "action_result": None, "undo_id": None}

    msgs = [{"role": "system", "content": build_system()}, *_history_messages(),
            {"role": "user", "content": _user_content(message, images)}]
    actions, undo_id, nudged, reply = [], None, False, ""

    for _ in range(MAX_ROUNDS):
        r = client().chat.completions.create(model=MODEL, messages=msgs, tools=TOOLS, temperature=0.3)
        m = r.choices[0].message
        if m.tool_calls:
            msgs.append({"role": "assistant", "content": m.content or "", "tool_calls": [
                {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in m.tool_calls]})
            for tc in m.tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    result = {"items": [{"ok": False, "summary": "工具参数不是合法 JSON"}], "text": "参数解析失败，请重新调用"}
                else:
                    if tc.function.name in WRITE_TOOLS and undo_id is None:
                        undo_id = st.snapshot()
                    result = execute_tool(tc.function.name, args)
                print(f"[小招] {tc.function.name} {tc.function.arguments} -> {result['text'][:300]}")
                actions += result["items"]
                msgs.append({"role": "tool", "tool_call_id": tc.id, "content": result["text"]})
            continue

        reply = (m.content or "").strip()
        if _LEGACY.search(reply):
            if undo_id is None:
                undo_id = st.snapshot()
            actions += _legacy_actions(reply)
            reply = _LEGACY.sub("", reply).strip()
        if not nudged and not any(a["ok"] for a in actions) and _CLAIM.search(reply):
            nudged = True
            msgs += [{"role": "assistant", "content": reply},
                     {"role": "user", "content": "【系统检查】你刚才的回复说已经改了看板，但其实没有调用任何工具，看板没有任何变化。"
                                                 "如果用户的话需要改看板，现在立刻调用工具完成；如果不需要，就直接重新回复用户。不要提到这条检查。"}]
            continue
        break

    reply = _SEARCH_TAG.sub("", reply).strip() or "；".join(a["summary"] for a in actions) or "好的"
    done = [a for a in actions if a["ok"]]
    if not done:
        undo_id = None
    elif undo_id:
        st.mark_after(undo_id)
    st.append_chat("user", message, images=thumbs)
    st.append_chat("assistant", reply, actions=actions, undo_id=undo_id)
    return {"status": "success", "reply": reply, "actions": actions,
            "action_result": "；".join(a["summary"] for a in done) or None, "undo_id": undo_id}


# ---------- 模拟面试 ----------
def mock_interview(company, position, round, action, history, answer):
    resume = jobhunter.resume_text(2500)
    who = f"岗位：{company} - {position}（{round}）" + (f"\n候选人简历：{resume}" if resume else "")
    if action == "start":
        prompt = f"""你是 {company} 的面试官，正在进行{round}。
{who}

请用一句有亲和力的话开场，然后提出第一个问题。问题要真实、具体，可以结合简历里的经历追问。
输出格式：
【开场】...
【问题1】..."""
        return complete(prompt, 0.8)

    dialog = "\n".join(f"{'面试官' if h.get('role') == 'interviewer' else '候选人'}：{h.get('content', '')}" for h in history)
    if action == "finish":
        prompt = f"""你是资深面试官，下面是一场模拟面试的完整记录。
{who}

{dialog}

请给出复盘：
1. 总评（一句话 + 百分制分数）
2. 亮点（2-3 条）
3. 需要改进（2-3 条，具体到哪道题怎么答更好）
4. 正式面试前最该准备的 3 件事
用简洁的中文，不要用 # 标题。"""
        return complete(prompt, 0.5)

    prompt = f"""你是面试官，正在面试候选人。
{who}

面试对话：
{dialog}

候选人刚才的回答：
{answer}

请先用一两句话点评这个回答（真诚、有建设性，指出亮点和可改进的地方），再根据岗位特点和简历提出下一个问题，逐步深入。
输出格式：
【点评】...
【问题】..."""
    return complete(prompt, 0.7)


def fetch_jd(query):
    sr = search_web(f"{query} 岗位职责 任职要求 JD", max_results=5)
    prompt = f"""根据以下搜索资料，整理「{query}」的岗位 JD 摘要：
1. 岗位职责（3-5 条）
2. 任职要求（3-5 条）
3. 最后一句点评这个岗位适合什么样的候选人
markdown 格式，不要用 # 标题，300 字以内。

搜索资料：
{sr}"""
    return complete(prompt, 0.3)


def weekly_review(report):
    resume = jobhunter.resume_text(1200)
    prompt = f"""这是用户本周的秋招周报：
{report}

用户简历摘要：{resume or '（无）'}

请以秋招教练的身份点评：本周节奏怎么样、哪些公司需要跟进或抓紧、下周建议做的 3 件事。200 字以内，亲切直接，不要用 # 标题。"""
    return complete(prompt, 0.6)
