"""与「求职助手」联动（只读）：岗位库和简历"""
import json
import os
import sqlite3

import storage as st

JOB_HUNTER_DB = os.getenv("JOB_HUNTER_DB") or os.path.expanduser("~/Desktop/job-hunter/backend/jobs.db")
JOB_HUNTER_RESUME = os.getenv("JOB_HUNTER_RESUME") or os.path.expanduser("~/Desktop/job-hunter/backend/resume.json")
JOB_STATUS = {"pending": "待确认", "approved": "待投递", "applied": "已投递", "rejected": "已拒绝"}


def is_available():
    return os.path.exists(JOB_HUNTER_DB)


def job_summary(cards, top=15):
    """给小招看的岗位库摘要：还没投的高分岗位 + 看板公司在求职助手里的岗位"""
    if not is_available():
        return ""
    try:
        conn = sqlite3.connect(f"file:{JOB_HUNTER_DB}?mode=ro", uri=True, timeout=3)
        rows = conn.execute("SELECT company,title,city,status,match_score,match_advice FROM jobs "
                            "ORDER BY match_score IS NULL, match_score DESC").fetchall()
        conn.close()
    except Exception as e:
        print(f"读取求职助手失败: {e}")
        return ""
    if not rows:
        return ""

    def fmt(r):
        score = r[4] if r[4] is not None else "分析中"
        return f"- {r[0]}｜{r[1]}｜{r[2] or '城市未知'}｜{JOB_STATUS.get(r[3], r[3])}｜匹配 {score}｜{r[5] or ''}"

    keys = {k for k in (st.norm_company(c["company"]) for c in cards) if len(k) >= 2}
    on_board = [r for r in rows if any(k in st.norm_company(r[0]) for k in keys)]
    todo = [r for r in rows if r[3] in ("pending", "approved") and r not in on_board][:top]
    cnt = {s: sum(1 for r in rows if r[3] == s) for s in JOB_STATUS}
    text = (f"共 {len(rows)} 个岗位（待确认 {cnt['pending']}，待投递 {cnt['approved']}，"
            f"已投递 {cnt['applied']}，已拒绝 {cnt['rejected']}）")
    if todo:
        text += "\n还没投的岗位（按匹配分排序）：\n" + "\n".join(fmt(r) for r in todo)
    if on_board:
        text += "\n看板公司在求职助手里的岗位：\n" + "\n".join(fmt(r) for r in on_board[:20])
    return text


def resume_text(limit=3000):
    """求职助手里的简历（JSON 原文截断），供小招答疑和模拟面试追问经历"""
    try:
        with open(JOB_HUNTER_RESUME, "r", encoding="utf-8") as f:
            return json.dumps(json.load(f), ensure_ascii=False)[:limit]
    except (FileNotFoundError, json.JSONDecodeError):
        return ""
