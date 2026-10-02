#!/usr/bin/env python3
"""validate.py — work-tri-layer 职业转型计划七维校验器

输入: 职业转型/工作三层拆解计划 JSON（schema 见 tests/sample_good.json）
  {
    "scan": {
      "monthly_minutes": 30,               // 每月名单扫描 ≥30 分钟（例行输入）
      "sources": ["人社部新职业公示", "行业头部招聘 JD"],  // 非空
      "signals_tracked": ["新岗位名称", "JD 反复出现的技能词"]  // 只看这两件事
    },
    "layers": {
      "inventory_weekly": true,            // 每周盘点
      "items": [
        {"name": "写周报初稿", "weekly_hours": 2, "layer": "replaceable"},
        {"name": "定下季度OKR", "weekly_hours": 1, "layer": "command"},
        {"name": "审AI生成的方案", "weekly_hours": 1.5, "layer": "judgment"},
        {"name": "客户访谈翻译需求", "weekly_hours": 3, "layer": "people"}
      ],
      "layer_values": ["replaceable", "command", "judgment", "people"],
      "misplaced": []                      // 已识别的错层项（须为空，错层项必须改层）
    },
    "compression": {
      "replaceable_hours_before": 8,       // 可替层原耗时
      "replaceable_hours_after": 4,        // 压缩后耗时（≤ 50% of before）
      "acceptance_checklists_ready": true, // 每项交 AI 前已有验收清单
      "time_reallocation": [               // 省下时间的去向（写满，非空）
        {"target": "command", "hours": 1.5, "concrete": "把方案类任务的验收标准写到5条"},
        {"target": "people", "hours": 1, "concrete": "承接2次客户访谈并产出需求拆解"}
      ]
    },
    "coach_loop": {
      "goal_owner": "human",               // 目标与验收标准由人写（AI 只能起草）
      "stepwise_execution": true,          // 分步下指令+逐步验收
      "max_rework_rounds": 2,              // 打回上限 ≤2
      "assets": {
        "prompt_lib_min_entries": 5,       // 提示词库 ≥5 条（每条带验证记录）
        "acceptance_checklist_min_items": 3, // 每类任务验收清单 ≥3 条
        "acceptance_checklist_max_items": 5, // ≤5 条（多了检查形同虚设）
        "failure_log_entries": 8,          // 翻车记录条数
        "repeated_failures_promoted": true // 重复≥3次翻车已强制进验收清单
      }
    },
    "people_side": {
      "capability": "需求翻译",             // 需求翻译/共情沟通/现场交付信任建立
      "delivery_last_month": "3 次客户访谈，产出 3 份需求拆解文档",  // 上月交付证据（非空）
      "in_workflow": true                  // 已做成工作流里可交付的部分
    },
    "opportunity_screen": {
      "rule": "serve_ai|serve_people|exit", // 一句话筛机会规则已建立
      "exit_targets": ["纯数据搬运的报表岗"], // 都沾不上且高度重复的撤出对象（可为空=暂无）
      "current_role_classified": true      // 当前岗位已三分类（伺候AI/伺候人/中间层）
    },
    "experiment_90d": {
      "direction": "适老化改造模拟方案",     // 可迁移方向
      "direction_class": "serve_people",   // serve_ai | serve_people（中间层不许做实验方向）
      "external_feedback_source": "公司养老事业部试点团队（3名真实用户）", // 外部反馈来源
      "start_date": "2026-10-06"
    }
  }

七维校验（任一 FAIL → exit 1）：
  D1 例行扫描: monthly_minutes ≥30 且 sources 非空 且 signals_tracked 覆盖
     新岗位名称+技能词两类信号（只看这两件事，趋势文不算信号源）
  D2 三层拆解: inventory_weekly=true 且 items 非空且每项 layer ∈
     {replaceable, command, judgment, people} 且 weekly_hours>0；
     错层检测：名称含"定目标/定方向/拍板/决定"归 replaceable = 错层；
     含"检查/审/验收/判断"归 replaceable = 错层；
     misplaced 非空 = 已识别错层未改层，拦截
  D3 可替层压缩: after ≤ 0.5×before 且 before>0 且 acceptance_checklists_ready=true
     且 time_reallocation 非空且每项 target/hours/concrete 齐全
     （省下时间不声明去向 = 被碎片吃掉）
  D4 AI 教练闭环: goal_owner=human（目标与验收标准人写）且 stepwise_execution=true
     且 max_rework_rounds ∈ [1,2]；资产：prompt_lib ≥5、验收清单 3-5 条、
     failure_log 有条数、repeated_failures_promoted=true（重复翻车必须进清单）
  D5 伺候人侧加仓: capability 非空 且 in_workflow=true 且 delivery_last_month 非空
     （答不出上月交付了什么 = 停在"我想培养"）
  D6 机会筛选: rule 含 serve_ai/serve_people/exit 三分类 且
     current_role_classified=true（当前岗位已分类）
  D7 90 天实验: direction 非空 且 direction_class ∈ {serve_ai, serve_people}
     （中间层不做实验方向）且 external_feedback_source 非空（自嗨实验不算实验）

退出码：0=通过 / 1=有缺陷 / 2=文件/格式错误
"""
import json
import sys

LAYERS = {"replaceable", "command", "judgment", "people"}
COMMAND_WORDS = ("定目标", "定方向", "拍板", "决定", "定标准")
JUDGMENT_WORDS = ("检查", "审", "验收", "判断", "评估产出")
PEOPLE_CAPS = ("需求翻译", "共情沟通", "现场交付", "信任建立")
DIRS = {"serve_ai", "serve_people"}


def fail(checks, code, msg):
    checks.append(("FAIL", code, msg))


def ok(checks, code, msg):
    checks.append(("OK", code, msg))


def is_nonempty_str(v):
    return isinstance(v, str) and v.strip() != ""


def validate(plan):
    checks = []

    # D1 例行扫描
    sc = plan.get("scan") or {}
    mm = sc.get("monthly_minutes")
    if not isinstance(mm, (int, float)) or mm < 30:
        fail(checks, "D1", f"monthly_minutes={mm!r}——每月名单扫描 ≥30 分钟才是例行输入")
    else:
        srcs = sc.get("sources") or []
        sigs = " ".join(sc.get("signals_tracked") or [])
        if not any(is_nonempty_str(x) for x in srcs):
            fail(checks, "D1", "sources 为空——没有订阅源（公示+JD），扫描无从做")
        elif ("岗位" not in sigs) or ("技能" not in sigs):
            fail(checks, "D1", f"signals_tracked 缺信号类型——只看两件事：新岗位名称 + JD 反复出现的技能词")
        else:
            ok(checks, "D1", f"例行扫描已建（{mm} 分钟/月，{len(srcs)} 个源）")

    # D2 三层拆解
    ly = plan.get("layers") or {}
    items = ly.get("items") or []
    if ly.get("inventory_weekly") is not True:
        fail(checks, "D2", "inventory_weekly=false——三层盘点必须每周做，一次盘点是照片不是录像")
    elif not items:
        fail(checks, "D2", "items 为空——没列出每周做的事，三层拆解无从做")
    else:
        bad_items = []
        for it in items:
            nm = str(it.get("name", "?"))
            if it.get("layer") not in LAYERS:
                bad_items.append(f"{nm}(layer 非法)")
                continue
            if not isinstance(it.get("weekly_hours"), (int, float)) or it.get("weekly_hours") <= 0:
                bad_items.append(f"{nm}(weekly_hours 须 >0)")
                continue
            if it["layer"] == "replaceable" and any(w in nm for w in COMMAND_WORDS):
                bad_items.append(f"{nm}(定目标/拍板是指挥层，错归可替层)")
            elif it["layer"] == "replaceable" and any(w in nm for w in JUDGMENT_WORDS):
                bad_items.append(f"{nm}(检查/验收是判断层，错归可替层)")
        if bad_items:
            fail(checks, "D2", "归层错误: " + "；".join(bad_items))
        elif ly.get("misplaced"):
            fail(checks, "D2", f"已识别错层未改层: {ly['misplaced']}——识别了就必须改")
        else:
            n = len(items)
            ok(checks, "D2", f"三层拆解合规（{n} 项事项全部归层，每周盘点）")

    # D3 可替层压缩
    cp = plan.get("compression") or {}
    b, a = cp.get("replaceable_hours_before"), cp.get("replaceable_hours_after")
    if not isinstance(b, (int, float)) or b <= 0:
        fail(checks, "D3", f"replaceable_hours_before={b!r}——先量化可替层原耗时（用盘点表数字，不凭记忆）")
    elif not isinstance(a, (int, float)) or a < 0 or a > 0.5 * b:
        fail(checks, "D3", f"压缩后 {a!r} > 原来 {b} 的 50%——一周压一半没达标")
    elif cp.get("acceptance_checklists_ready") is not True:
        fail(checks, "D3", "acceptance_checklists_ready=false——没有验收清单就交 AI = 返工双份活")
    else:
        alloc = cp.get("time_reallocation") or []
        bad_alloc = [x.get("target", "?") for x in alloc
                     if x.get("target") not in LAYERS
                     or not isinstance(x.get("hours"), (int, float)) or x.get("hours") <= 0
                     or not is_nonempty_str(x.get("concrete"))]
        if not any(is_nonempty_str(x.get("concrete")) for x in alloc):
            fail(checks, "D3", "time_reallocation 为空——省下的时间没声明去向 = 被碎片吃掉")
        elif bad_alloc:
            fail(checks, "D3", f"时间去向不全: {bad_alloc}——每项须有 target/hours/concrete（具体做什么）")
        else:
            ok(checks, "D3", f"可替层压缩达标（{b}h → {a}h，省下时间已分配 {len(alloc)} 项）")

    # D4 AI 教练闭环
    cl = plan.get("coach_loop") or {}
    if cl.get("goal_owner") != "human":
        fail(checks, "D4", f"goal_owner={cl.get('goal_owner')!r}——目标与验收标准必须人写（AI 只能起草初稿），外包=判断层丢失")
    elif cl.get("stepwise_execution") is not True:
        fail(checks, "D4", "stepwise_execution=false——一次下完整条链=中间错了全链返工，须分步下指令+逐步验收")
    else:
        mr = cl.get("max_rework_rounds")
        if not isinstance(mr, int) or mr < 1 or mr > 2:
            fail(checks, "D4", f"max_rework_rounds={mr!r}——打回上限须 1-2（第 3 轮=成本失控，禁止）")
        else:
            asrt = cl.get("assets") or {}
            plib = asrt.get("prompt_lib_min_entries")
            ci_min = asrt.get("acceptance_checklist_min_items")
            ci_max = asrt.get("acceptance_checklist_max_items")
            fl = asrt.get("failure_log_entries")
            if not isinstance(plib, int) or plib < 5:
                fail(checks, "D4", f"提示词库 {plib!r} 条——<5 条=资产没攒起来（每条须带验证记录）")
            elif not (isinstance(ci_min, int) and isinstance(ci_max, int) and 3 <= ci_min <= ci_max <= 5):
                fail(checks, "D4", f"验收清单 {ci_min}-{ci_max} 条——每类任务 3-5 条（<3 判断没成型，>5 检查形同虚设）")
            elif not isinstance(fl, int) or fl < 0:
                fail(checks, "D4", f"翻车记录 {fl!r} 条——非法")
            elif asrt.get("repeated_failures_promoted") is not True:
                fail(checks, "D4", "repeated_failures_promoted=false——重复≥3次的翻车没进验收清单 = 每次用判断力拦同一个错")
            else:
                ok(checks, "D4", f"AI 教练闭环合规（{plib} 条提示词 + 清单 {ci_min}-{ci_max} 条 + {fl} 条翻车已转清单）")

    # D5 伺候人侧加仓
    pp = plan.get("people_side") or {}
    cap = str(pp.get("capability") or "")
    if not is_nonempty_str(cap) or not any(w in cap for w in PEOPLE_CAPS):
        fail(checks, "D5", f"capability='{cap}'——须选一项机器做不了的能力（需求翻译/共情沟通/现场交付与信任建立）")
    elif pp.get("in_workflow") is not True:
        fail(checks, "D5", "in_workflow=false——能力没进工作流 = 停在'我想培养'，不是生产")
    elif not is_nonempty_str(pp.get("delivery_last_month")):
        fail(checks, "D5", "delivery_last_month 为空——答不出'上个月交付了什么' = 没有积累（无人验证、无案例、无信任）")
    else:
        ok(checks, "D5", f"伺候人侧已加仓（{cap}，上月交付: {pp['delivery_last_month'][:20]}…）")

    # D6 机会筛选
    osn = plan.get("opportunity_screen") or {}
    rule = str(osn.get("rule") or "")
    if not all(k in rule for k in ("serve_ai", "serve_people", "exit")):
        fail(checks, "D6", f"rule='{rule}'——一句话筛机会须含三分类：伺候AI/伺候人/都不沾且重复→撤出")
    elif osn.get("current_role_classified") is not True:
        fail(checks, "D6", "current_role_classified=false——当前岗位还没三分类，不知道自己站在哪，筛选无从做")
    else:
        exits = osn.get("exit_targets") or []
        ok(checks, "D6", f"机会筛选已建（三分类规则 + 撤出对象 {len(exits)} 个）")

    # D7 90 天实验
    ex = plan.get("experiment_90d") or {}
    if not is_nonempty_str(ex.get("direction")):
        fail(checks, "D7", "direction 为空——没选可迁移方向，90 天实验无从谈起")
    elif ex.get("direction_class") not in DIRS:
        fail(checks, "D7", f"direction_class={ex.get('direction_class')!r}——实验方向须伺候AI或伺候人；中间层（都不沾）不是投资方向是撤出对象")
    elif not is_nonempty_str(ex.get("external_feedback_source")):
        fail(checks, "D7", "external_feedback_source 为空——没有外部反馈来源 = 自嗨实验（自测通过≠方向对）")
    else:
        ok(checks, "D7", f"90 天实验已设计（{ex['direction']}，外部反馈: {ex['external_feedback_source'][:15]}…）")

    return checks


def main():
    if len(sys.argv) != 2:
        print("usage: python validate.py plan.json", file=sys.stderr)
        return 2
    try:
        with open(sys.argv[1], encoding="utf-8") as f:
            plan = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"ERROR: 无法读取/解析: {e}", file=sys.stderr)
        return 2
    if not isinstance(plan, dict):
        print("ERROR: 顶层必须是 JSON 对象", file=sys.stderr)
        return 2

    checks = validate(plan)
    fails = [c for c in checks if c[0] == "FAIL"]
    for status, code, msg in checks:
        if status == "FAIL":
            print(f"[{code}] FAIL {msg}")
    print(f"== {len(checks) - len(fails)} OK / {len(fails)} FAIL ==")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
