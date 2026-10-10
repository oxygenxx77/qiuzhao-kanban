"""
生成一套虚构的演示数据（截图 / 试用用），所有公司进度、面试和对话都是编的。

用法：把项目复制到一个新文件夹，再对那个文件夹运行
    cp -R qiuzhao-kanban /tmp/kanban-demo
    python3 demo/seed_demo.py /tmp/kanban-demo
目标文件夹里已经有看板数据时拒绝运行，避免覆盖真实记录。
"""
import json
import os
import sys
from datetime import datetime, timedelta

CARDS = [
    # 公司, 岗位, 状态, 投递于几天前, 截止在几天后（None 表示无）, 备注
    ("字节跳动", "电商运营、用户增长运营", "interview", 18, None, "二面后 HR 说一周内出结果"),
    ("美团", "到店事业群-商家运营", "interview", 15, None, "一面聊了很多数据分析"),
    ("小红书", "社区内容运营", "pending", 6, 2, "测评含行测 + 性格测试"),
    ("拼多多", "运营管培生", "waiting", 21, None, "终面结束，等 offer 审批"),
    ("网易", "游戏用户运营", "submitted", 4, None, ""),
    ("快手", "电商主播运营", "pending", 3, 4, "笔试：运营案例分析"),
    ("腾讯", "产品运营", "waiting", 25, None, "HR 面已完成"),
    ("京东", "采销管培生", "offer", 30, 6, "offer 确认截止前要回复"),
    ("阿里巴巴", "淘天集团-行业运营", "submitted", 2, None, "内推"),
    ("哔哩哔哩", "UP 主运营", "ended", 28, None, "一面挂，复盘：案例题准备不足"),
    ("泡泡玛特", "电商运营、品牌营销", "submitted", 1, None, ""),
    ("米哈游", "社区运营", "pending", 5, 1, "作品集 + 运营方案"),
    ("得物", "商家运营", "submitted", 7, None, ""),
    ("携程", "用户运营", "ended", 26, None, "简历挂"),
    ("蔚来", "用户关系运营", "interview", 12, None, ""),
    ("百度", "AI 产品运营", "submitted", 3, None, ""),
]

INTERVIEWS = [
    # 公司, 岗位, 轮次, 在几天后（负数是过去）, 时间, 结果, 问题, 复盘
    ("字节跳动", "电商运营", "一面", -9, "14:00", "通过",
     "1. 介绍一段最有成就感的实习\n2. GMV 下降怎么拆解\n3. 如果让你负责一个新品类冷启动，前两周做什么",
     "GMV 拆解答得比较顺，冷启动的节奏讲得太散，下次先给框架再展开"),
    ("字节跳动", "电商运营", "二面", -3, "10:30", "通过",
     "1. 讲一个用数据推动决策的例子\n2. 如何判断一场直播是否成功\n3. 反问环节",
     "这次先说结论再展开，面试官反馈清晰"),
    ("字节跳动", "电商运营", "HR 面", 2, "16:00", "待定", "", ""),
    ("美团", "商家运营", "一面", -5, "15:00", "通过",
     "1. 怎么说服一个不愿意上新的商家\n2. 写一条 SQL：统计每个商圈的复购率",
     "SQL 写对了但没考虑去重，要记得先问清口径"),
    ("美团", "商家运营", "二面", 1, "19:00", "待定", "", ""),
    ("蔚来", "用户关系运营", "一面", 3, "11:00", "待定", "", ""),
    ("哔哩哔哩", "UP 主运营", "一面", -20, "16:30", "已挂",
     "1. 你最喜欢的三个 UP 主，为什么\n2. 设计一个新人 UP 主扶持计划",
     "扶持计划没有量化目标，太像空想；以后案例题都要带指标和预算"),
]

CHAT = [
    ("user", "投了泡泡玛特的电商运营和品牌营销，快手发来笔试，周二晚上 12 点截止", None),
    ("assistant", "收到！两件事都记好了：泡泡玛特已加到「已投递」，快手改成「待测评」并设了截止时间，到时候我会提醒你。",
     [{"ok": True, "summary": "新增「泡泡玛特」（已投递），岗位：电商运营、品牌营销"},
      {"ok": True, "summary": "快手：已投递 → 待测评，截止时间设为周二 23:59"}]),
    ("user", "这周还有哪些事要做？", None),
    ("assistant", "这周的安排：\n\n1. **米哈游** 作品集 + 运营方案，明天截止，优先做\n2. **小红书** 测评，后天截止\n"
                  "3. **美团** 二面，明天晚上 19:00，建议先复习一下 SQL 去重和复购率口径\n"
                  "4. **字节跳动** HR 面，后天 16:00\n\n另外京东的 offer 6 天后要回复，可以先想想要问 HR 的问题。", None),
]

STATUS_LABEL = {"pending": "待测评", "interview": "面试中", "waiting": "等结果",
                "submitted": "已投递", "offer": "Offer", "ended": "已结束"}
FLOW = ["submitted", "pending", "interview", "waiting", "offer"]


def main(target):
    data_file = os.path.join(target, "kanban_data.json")
    if not os.path.exists(os.path.join(target, "main.py")):
        sys.exit(f"{target} 不是看板项目目录")
    if os.path.exists(data_file):
        sys.exit(f"{target} 里已经有看板数据，为避免覆盖真实记录，请换一个空的项目副本")

    now = datetime.now().replace(second=0, microsecond=0)
    day = lambda n: (now + timedelta(days=n)).strftime("%Y-%m-%d")
    stamp = lambda n, h=10: (now + timedelta(days=n)).replace(hour=h, minute=0).isoformat(timespec="seconds")

    cards, history = [], []
    for i, (company, positions, status, ago, due, note) in enumerate(CARDS, 1):
        cards.append({
            "id": str(i), "company": company, "positions": positions, "status": status,
            "deadline": f"{day(due)}T23:59" if due is not None else "",
            "note": note, "applied_at": day(-ago), "updated_at": stamp(-min(ago, 2)),
        })
        history.append({"timestamp": stamp(-ago), "company": company, "old_status": "",
                        "new_status": "submitted", "note": "网页新增"})
        steps = FLOW[1:FLOW.index(status) + 1] if status in FLOW else ["ended"]
        for k, s in enumerate(steps, 1):
            prev = history[-1]["new_status"]
            history.append({"timestamp": stamp(-ago + k * max(ago // (len(steps) + 1), 1), 15),
                            "company": company, "old_status": prev, "new_status": s, "note": ""})
    history.sort(key=lambda h: h["timestamp"])

    interviews = [{
        "id": str(i), "company": c, "position": p, "round": r, "date": day(d), "time": t,
        "result": res, "questions": q, "reflection": ref, "created_at": stamp(min(d, 0) - 3),
    } for i, (c, p, r, d, t, res, q, ref) in enumerate(INTERVIEWS, 1)]

    chat = []
    for k, (role, content, actions) in enumerate(CHAT):
        item = {"role": role, "content": content, "timestamp": stamp(-1, 21) if k < 2 else stamp(0, 9)}
        if actions:
            item["actions"] = actions
        chat.append(item)

    files = {"kanban_data.json": cards, "history.json": history,
             "interviews.json": interviews, "chat_history.json": chat}
    for name, value in files.items():
        with open(os.path.join(target, name), "w", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False, indent=2)
    print(f"已在 {target} 生成演示数据：{len(cards)} 家公司、{len(interviews)} 场面试、{len(history)} 条状态变化")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(os.path.abspath(sys.argv[1]))
