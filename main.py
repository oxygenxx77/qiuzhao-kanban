import os
from contextlib import asynccontextmanager
from datetime import datetime
from typing import List, Optional

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel

import agenda
import agent
import board
import jobhunter
import sqlmirror
import storage as st


@asynccontextmanager
async def lifespan(app):
    sqlmirror.ensure_fresh()
    agenda.start_reminders()
    yield


app = FastAPI(title="秋招看板 API", lifespan=lifespan)
# 只允许本机页面（看板自己、求职助手、浏览器插件经由求职助手）跨域访问
app.add_middleware(CORSMiddleware, allow_origin_regex=r"http://(127\.0\.0\.1|localhost)(:\d+)?",
                   allow_methods=["*"], allow_headers=["*"])


@app.exception_handler(board.BoardError)
def board_error(request: Request, e: board.BoardError):
    return JSONResponse(status_code=400, content={"detail": str(e)})


# ---------- 请求体 ----------
class UpdateRequest(BaseModel):
    company: str
    new_company: Optional[str] = None
    new_status: Optional[str] = None
    new_note: Optional[str] = None
    new_deadline: Optional[str] = None
    new_positions: Optional[str] = None
    applied_at: Optional[str] = None


class AddRequest(BaseModel):
    company: str
    positions: str = ""
    status: str = "submitted"
    deadline: str = ""
    note: str = ""
    applied_at: str = ""


class CompanyRequest(BaseModel):
    company: str


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    message: str = ""
    history: Optional[List[ChatMessage]] = []   # 旧版前端会传，聊天记录现在以服务端为准
    images: List[str] = []    # 发给 AI 的图片（前端压缩过的 data URL）
    thumbs: List[str] = []    # 存进聊天记录的小缩略图


MAX_CHAT_IMAGES = 4
MAX_IMAGE_CHARS = 6_000_000     # 单张 data URL 上限，约 4.5MB 图片
MAX_THUMB_CHARS = 120_000


def check_images(urls, limit):
    if len(urls) > MAX_CHAT_IMAGES:
        raise HTTPException(400, f"一次最多发 {MAX_CHAT_IMAGES} 张图片")
    for u in urls:
        if not u.startswith(("data:image/png;base64,", "data:image/jpeg;base64,", "data:image/webp;base64,", "data:image/gif;base64,")):
            raise HTTPException(400, "只支持 PNG、JPG、WebP、GIF 图片")
        if len(u) > limit:
            raise HTTPException(400, "图片太大了，请截小一点再发")


class UndoRequest(BaseModel):
    undo_id: str
    force: bool = False


class InterviewRequest(BaseModel):
    id: Optional[str] = None
    company: str
    position: str = ""
    round: str = "一面"
    date: str = ""
    time: str = ""
    questions: str = ""
    reflection: str = ""
    result: str = "待定"


class ProfileRequest(BaseModel):
    target_cities: str = ""
    preferred_roles: str = ""
    avoid_companies: str = ""
    notes: str = ""


class MockRequest(BaseModel):
    company: str
    position: str
    round: str = "一面"
    action: str = "start"   # start / answer / finish
    history: List[dict] = []
    answer: str = ""


class ReviewRequest(BaseModel):
    report: str = ""


class SqlRequest(BaseModel):
    query: str


def ok(message, **extra):
    return {"status": "success", "message": message, **extra}


def need_ai():
    if not agent.ai_enabled():
        raise HTTPException(400, "AI 功能未启用：在 .env 里填入 OPENAI_API_KEY 后重启")


def ai_call(fn, *args):
    need_ai()
    try:
        return fn(*args)
    except Exception as e:
        print(f"AI 调用失败: {e}")
        raise HTTPException(502, f"AI 服务暂时不可用：{e}")


# ---------- 页面 ----------
@app.get("/")
@app.get("/index.html")
def index():
    return FileResponse(os.path.join(st.BASE_DIR, "index.html"), headers={"Cache-Control": "no-cache"})


@app.get("/api/status")
def status():
    return {"ai": agent.ai_enabled(), "model": agent.MODEL, "search": bool(os.getenv("TAVILY_API_KEY")),
            "push": bool(os.getenv("SERVERCHAN_KEY")), "job_hunter": jobhunter.is_available()}


# ---------- 看板 ----------
@app.get("/api/data")
def get_data():
    sqlmirror.ensure_fresh()
    return st.load_cards()


@app.post("/api/update")
def update(req: UpdateRequest):
    msg = board.update_company(req.company, new_company=req.new_company, positions=req.new_positions, status=req.new_status,
                               note=req.new_note, deadline=req.new_deadline, applied_at=req.applied_at, create=False)
    return ok(msg)


@app.post("/api/add")
def add(req: AddRequest):
    if st.find_card(st.load_cards(), req.company)[0]:
        raise HTTPException(400, f"看板里已经有「{req.company}」了，直接编辑它就好")
    msg = board.update_company(req.company, positions=req.positions, status=req.status or "submitted",
                               deadline=req.deadline, note=req.note, applied_at=req.applied_at or None)
    return ok(msg)


@app.post("/api/delete")
def delete(req: CompanyRequest):
    return ok(board.delete_company(req.company))


@app.get("/api/history")
def history(company: str = ""):
    items = st.load_history()
    if company:
        key = st.norm_company(company)
        items = [h for h in items if st.norm_company(h.get("company")) == key]
    return items[-200:]


@app.post("/api/undo")
def undo(req: UndoRequest):
    try:
        st.restore(req.undo_id, force=req.force)
    except st.UndoConflict as e:
        raise HTTPException(409, str(e))
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(400, str(e))
    return ok("已撤销这次修改")


# ---------- 日程 ----------
@app.get("/api/agenda")
def get_agenda():
    return agenda.agenda()


@app.get("/api/calendar.ics")
def calendar(download: int = 0):
    headers = {"Cache-Control": "no-cache"}
    if download:
        headers["Content-Disposition"] = 'attachment; filename="qiuzhao.ics"'
    return Response(agenda.ics(), media_type="text/calendar; charset=utf-8", headers=headers)


# ---------- SQL 练习（只读查询 kanban.db，jh. 前缀是求职助手的 jobs.db） ----------
@app.get("/api/sql/schema")
def sql_schema():
    return sqlmirror.schema()


@app.post("/api/sql")
def sql_query(req: SqlRequest):
    try:
        return sqlmirror.query(req.query)
    except sqlmirror.QueryError as e:
        raise HTTPException(400, str(e))


@app.post("/api/sql/sync")
def sql_sync():
    sqlmirror.sync()
    return ok("已同步", synced_at=sqlmirror.synced_at())


# ---------- 用户偏好 ----------
@app.get("/api/profile")
def get_profile():
    return st.read_json(st.PROFILE_FILE)


@app.post("/api/profile/save")
def save_profile(req: ProfileRequest):
    st.write_json(st.PROFILE_FILE, {**req.model_dump(), "updated_at": st.now_iso()})
    return ok("偏好已保存")


# ---------- 面试记录 ----------
@app.get("/api/interviews")
def get_interviews():
    return st.load_interviews()


@app.post("/api/interview/save")
def save_interview(req: InterviewRequest):
    return ok(board.save_interview(**req.model_dump()))


@app.post("/api/interview/delete/{interview_id}")
def delete_interview(interview_id: str):
    return ok(board.delete_interview(interview_id))


# ---------- 小招 ----------
@app.get("/api/chat_history")
def chat_history():
    return st.load_chat()


@app.post("/api/chat_clear")
def chat_clear():
    st.clear_chat()
    return ok("已清空")


@app.post("/api/chat")
def chat(req: ChatRequest):
    message = req.message.strip()
    if not message and not req.images:
        raise HTTPException(400, "说点什么或者发张图吧")
    check_images(req.images, MAX_IMAGE_CHARS)
    check_images(req.thumbs, MAX_THUMB_CHARS)
    try:
        return agent.chat(message, req.images, req.thumbs)
    except Exception as e:
        print(f"聊天报错: {e}")
        raise HTTPException(502, f"小招暂时连不上 AI 服务：{e}")


@app.post("/api/ai_update")
def ai_update(req: ChatRequest):
    r = chat(req)
    return ok(r.get("action_result") or r.get("reply", "已处理"), undo_id=r.get("undo_id"))


@app.post("/api/mock_interview")
def mock_interview(req: MockRequest):
    if not agent.ai_enabled():
        return {"status": "disabled", "content": "AI 模拟面试未启用，配置 API Key 后可用。"}
    content = ai_call(agent.mock_interview, req.company, req.position, req.round, req.action, req.history, req.answer)
    return {"status": "success", "content": content}


@app.post("/api/fetch_jd")
def fetch_jd(req: ChatRequest):
    if not agent.ai_enabled():
        return {"status": "disabled", "jd": "AI 抓取 JD 未启用，配置 API Key 后可用。"}
    return {"status": "success", "jd": ai_call(agent.fetch_jd, req.message)}


@app.get("/api/weekly_report")
def weekly_report():
    return {"report": agenda.weekly_report(), "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M")}


@app.post("/api/weekly_report/ai")
def weekly_review(req: ReviewRequest):
    return {"status": "success", "review": ai_call(agent.weekly_review, req.report or agenda.weekly_report())}
