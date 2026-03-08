"""
生成100条测试报销申请，分布如下：
  - 1~80:  AUTO_APPROVE  — 金额<1000元，票据完整，各项合规 → 系统自动审批
  - 81~90: PIPELINE      — 金额1000~5000元，合规 → 上级+财务审批后通过
  - 91~95: HUMAN_REVIEW  — 边缘情况（略超标准 / 票据存疑）→ 标记人工审核
  - 96~100: REJECT       — 明显违规（缺票、严重超标、类别错误）→ 退回
"""

import random
from datetime import datetime, timedelta
from typing import List, Dict

random.seed(42)

EMPLOYEES = [
    {"name": "张伟",   "dept": "技术部",   "level": "P5"},
    {"name": "李娜",   "dept": "销售部",   "level": "P6"},
    {"name": "王芳",   "dept": "市场部",   "level": "P5"},
    {"name": "刘洋",   "dept": "技术部",   "level": "P6"},
    {"name": "陈静",   "dept": "人事部",   "level": "P4"},
    {"name": "赵磊",   "dept": "销售部",   "level": "P7"},
    {"name": "周杰",   "dept": "运营部",   "level": "P5"},
    {"name": "吴敏",   "dept": "财务部",   "level": "P5"},
    {"name": "郑浩",   "dept": "市场部",   "level": "P6"},
    {"name": "孙雪",   "dept": "法务部",   "level": "P5"},
]

CITIES_T1 = ["北京", "上海", "广州", "深圳"]
CITIES_T2 = ["杭州", "南京", "成都", "武汉", "西安", "重庆"]
CITIES_OTHER = ["长沙", "合肥", "济南", "青岛", "宁波"]


def _days_ago(n: int) -> str:
    return (datetime.now() - timedelta(days=n)).strftime("%Y-%m-%d")


def _make_trip_dates(days_ago_start: int, duration: int):
    start = datetime.now() - timedelta(days=days_ago_start)
    end = start + timedelta(days=duration - 1)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


# ─────────────────────────────────────────────────────────────────────────────
# 分组1: 1~80  AUTO_APPROVE（金额<1000元，完全合规）
# ─────────────────────────────────────────────────────────────────────────────
def _gen_auto_approve(idx: int) -> Dict:
    emp = EMPLOYEES[idx % len(EMPLOYEES)]
    city = random.choice(CITIES_T2 + CITIES_OTHER)
    days = random.randint(1, 2)
    start_date, end_date = _make_trip_dates(random.randint(3, 20), days)

    # 各项费用均在标准内
    hotel_nightly = random.randint(180, 280)      # other城市上限300
    meal_daily    = random.randint(60, 120)        # 上限150
    taxi          = random.randint(30, 150)        # 上限200
    hotel_total   = hotel_nightly * days
    meal_total    = meal_daily * days
    total         = hotel_total + meal_total + taxi

    # 确保 < 1000
    while total >= 1000:
        taxi = random.randint(20, 80)
        total = hotel_total + meal_total + taxi

    return {
        "app_id":        f"EXP{idx:04d}",
        "applicant":     emp["name"],
        "department":    emp["dept"],
        "level":         emp["level"],
        "destination":   city,
        "trip_start":    start_date,
        "trip_end":      end_date,
        "trip_days":     days,
        "purpose":       "客户拜访",
        "expense_items": [
            {"category": "住宿费",  "amount": hotel_total, "has_receipt": True,
             "note": f"{city}连锁酒店，{hotel_nightly}元/晚×{days}晚"},
            {"category": "餐饮费",  "amount": meal_total,  "has_receipt": True,
             "note": f"餐饮{meal_daily}元/天×{days}天"},
            {"category": "交通费",  "amount": taxi,         "has_receipt": True,
             "note": "市内出租车"},
        ],
        "total_amount":  total,
        "has_all_receipts": True,
        "within_limits":    True,
        "submitted_days_after_trip": random.randint(1, 10),
        "expected_outcome": "AUTO_APPROVED",
    }


# ─────────────────────────────────────────────────────────────────────────────
# 分组2: 81~90  PIPELINE APPROVE（金额1000~5000元，合规，走完整审批流程）
# ─────────────────────────────────────────────────────────────────────────────
def _gen_pipeline_approve(idx: int) -> Dict:
    emp = EMPLOYEES[idx % len(EMPLOYEES)]
    city = random.choice(CITIES_T1 + CITIES_T2)
    days = random.randint(2, 4)
    start_date, end_date = _make_trip_dates(random.randint(3, 15), days)
    tier = "tier1" if city in CITIES_T1 else "tier2"
    # rule_002: tier1=600/晚, tier2=400/晚
    hotel_night_limit = 600 if tier == "tier1" else 400
    # rule_003: 餐饮150元/天（统一标准，无城市区分）
    meal_day_limit = 150

    # 严格控制在上限以内，避免 LLM 因超标拒绝
    hotel_night = random.randint(int(hotel_night_limit * 0.75), hotel_night_limit - 1)
    hotel_total = hotel_night * days
    meal_day    = random.randint(80, meal_day_limit - 1)
    meal_total  = meal_day * days
    flight      = random.randint(800, 1500)
    taxi        = random.randint(80, 150) * days
    total       = hotel_total + meal_total + flight + taxi

    # 确保在 [1000, 5000]，同时保持各项在限额内
    attempts = 0
    while not (1000 <= total <= 5000) and attempts < 20:
        flight = random.randint(500, 1200)
        total = hotel_total + meal_total + flight + taxi
        attempts += 1

    return {
        "app_id":        f"EXP{idx:04d}",
        "applicant":     emp["name"],
        "department":    emp["dept"],
        "level":         emp["level"],
        "destination":   city,
        "trip_start":    start_date,
        "trip_end":      end_date,
        "trip_days":     days,
        "purpose":       "项目出差",
        "expense_items": [
            {"category": "机票",   "amount": flight,      "has_receipt": True,
             "note": f"经济舱往返（rule_004合规）"},
            {"category": "住宿费", "amount": hotel_total, "has_receipt": True,
             "note": f"{hotel_night}元/晚×{days}晚，上限{hotel_night_limit}元/晚，合规（rule_002）"},
            {"category": "餐饮费", "amount": meal_total,  "has_receipt": True,
             "note": f"{meal_day}元/天×{days}天，上限150元/天，合规（rule_003）"},
            {"category": "交通费", "amount": taxi,        "has_receipt": True,
             "note": "市内出租车，凭发票（rule_006）"},
        ],
        "total_amount":  total,
        "has_all_receipts": True,
        "within_limits":    True,
        "submitted_days_after_trip": random.randint(1, 15),
        "expected_outcome": "APPROVED",
    }


# ─────────────────────────────────────────────────────────────────────────────
# 分组3: 91~95  HUMAN_REVIEW（边缘情况，需人工判断）
# ─────────────────────────────────────────────────────────────────────────────
HUMAN_REVIEW_CASES = [
    {
        "app_id": "EXP0091",
        "applicant": "赵磊", "department": "销售部", "level": "P7",
        "destination": "上海",
        "trip_start": _days_ago(10), "trip_end": _days_ago(7),
        "trip_days": 4, "purpose": "大客户签约谈判",
        "expense_items": [
            {"category": "住宿费",  "amount": 2800, "has_receipt": True,
             "note": "客户指定酒店，660元/晚×4晚，超一线城市标准600元/晚"},
            {"category": "机票",    "amount": 1800, "has_receipt": True,
             "note": "经济舱往返，含行李"},
            {"category": "餐饮费",  "amount": 600,  "has_receipt": True,
             "note": "工作餐，150元/天×4天"},
            {"category": "交通费",  "amount": 560,  "has_receipt": True,
             "note": "市内出行，多次出租车"},
        ],
        "total_amount": 5760,
        "has_all_receipts": True,
        "within_limits": False,
        "justification": "客户指定五星酒店，住宿超标需审批；总监已口头同意",
        "submitted_days_after_trip": 8,
        "expected_outcome": "PENDING_HUMAN_REVIEW",
    },
    {
        "app_id": "EXP0092",
        "applicant": "郑浩", "department": "市场部", "level": "P6",
        "destination": "北京",
        "trip_start": _days_ago(25), "trip_end": _days_ago(23),
        "trip_days": 3, "purpose": "行业峰会参会",
        "expense_items": [
            {"category": "机票",    "amount": 2200, "has_receipt": True,
             "note": "出发时机票已涨价，未提前3天购买"},
            {"category": "住宿费",  "amount": 1800, "has_receipt": True,
             "note": "会议指定酒店，600元/晚×3晚"},
            {"category": "餐饮费",  "amount": 450,  "has_receipt": True,
             "note": "工作餐"},
            {"category": "交通费",  "amount": 300,  "has_receipt": True,
             "note": "机场大巴+出租车"},
        ],
        "total_amount": 4750,
        "has_all_receipts": True,
        "within_limits": True,
        "justification": "峰会临时通知，无法提前3天购票",
        "submitted_days_after_trip": 22,
        "expected_outcome": "PENDING_HUMAN_REVIEW",
    },
    {
        "app_id": "EXP0093",
        "applicant": "刘洋", "department": "技术部", "level": "P6",
        "destination": "深圳",
        "trip_start": _days_ago(6), "trip_end": _days_ago(4),
        "trip_days": 3, "purpose": "技术评审",
        "expense_items": [
            {"category": "机票",    "amount": 1600, "has_receipt": True,  "note": "经济舱"},
            {"category": "住宿费",  "amount": 1800, "has_receipt": True,  "note": "600元/晚×3晚"},
            {"category": "餐饮费",  "amount": 420,  "has_receipt": False,
             "note": "部分餐饮无发票（现金支付）"},
            {"category": "交通费",  "amount": 280,  "has_receipt": True,  "note": "出租车"},
        ],
        "total_amount": 4100,
        "has_all_receipts": False,
        "within_limits": True,
        "justification": "部分餐厅无法提供发票，已尽力收集",
        "submitted_days_after_trip": 5,
        "expected_outcome": "PENDING_HUMAN_REVIEW",
    },
    {
        "app_id": "EXP0094",
        "applicant": "周杰", "department": "运营部", "level": "P5",
        "destination": "成都",
        "trip_start": _days_ago(9), "trip_end": _days_ago(7),
        "trip_days": 3, "purpose": "供应商考察",
        "expense_items": [
            {"category": "机票",    "amount": 1400, "has_receipt": True,  "note": "经济舱"},
            {"category": "住宿费",  "amount": 1350, "has_receipt": True,  "note": "450元/晚×3晚，超二线城市标准400元/晚"},
            {"category": "餐饮费",  "amount": 390,  "has_receipt": True,  "note": "工作餐"},
            {"category": "交通费",  "amount": 200,  "has_receipt": True,  "note": "出租车"},
        ],
        "total_amount": 3340,
        "has_all_receipts": True,
        "within_limits": False,
        "justification": "当地合适酒店已满，最低价格为450元，超标50元/晚",
        "submitted_days_after_trip": 7,
        "expected_outcome": "PENDING_HUMAN_REVIEW",
        "budget_note": "运营部预算使用率已达85%，需总监审批",
    },
    {
        "app_id": "EXP0095",
        "applicant": "李娜", "department": "销售部", "level": "P6",
        "destination": "广州",
        "trip_start": _days_ago(35), "trip_end": _days_ago(32),
        "trip_days": 4, "purpose": "客户维护",
        "expense_items": [
            {"category": "机票",    "amount": 1800, "has_receipt": True,  "note": "经济舱往返"},
            {"category": "住宿费",  "amount": 2400, "has_receipt": True,  "note": "600元/晚×4晚"},
            {"category": "餐饮费",  "amount": 600,  "has_receipt": True,  "note": "工作餐"},
            {"category": "交通费",  "amount": 400,  "has_receipt": True,  "note": "出租车"},
        ],
        "total_amount": 5200,
        "has_all_receipts": True,
        "within_limits": True,
        "justification": "出差已超30天未报销，因项目繁忙延迟提交",
        "submitted_days_after_trip": 33,
        "expected_outcome": "PENDING_HUMAN_REVIEW",
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# 分组4: 96~100  REJECT（明显违规）
# ─────────────────────────────────────────────────────────────────────────────
REJECT_CASES = [
    {
        "app_id": "EXP0096",
        "applicant": "张伟", "department": "技术部", "level": "P5",
        "destination": "上海",
        "trip_start": _days_ago(5), "trip_end": _days_ago(4),
        "trip_days": 2, "purpose": "会议",
        "expense_items": [
            {"category": "住宿费",  "amount": 1600, "has_receipt": False,
             "note": "住宿发票遗失"},
            {"category": "餐饮费",  "amount": 300,  "has_receipt": False,
             "note": "无餐饮发票"},
            {"category": "交通费",  "amount": 600,  "has_receipt": False,
             "note": "无出租车发票"},
        ],
        "total_amount": 2500,
        "has_all_receipts": False,
        "within_limits": False,
        "justification": "票据全部遗失",
        "submitted_days_after_trip": 4,
        "expected_outcome": "REJECTED",
        "reject_reason": "所有费用项均无发票，不符合报销要求",
    },
    {
        "app_id": "EXP0097",
        "applicant": "王芳", "department": "市场部", "level": "P5",
        "destination": "北京",
        "trip_start": _days_ago(8), "trip_end": _days_ago(6),
        "trip_days": 3, "purpose": "内部培训",
        "expense_items": [
            {"category": "机票",    "amount": 3800, "has_receipt": True,
             "note": "商务舱机票，未经审批"},
            {"category": "住宿费",  "amount": 2400, "has_receipt": True,
             "note": "五星酒店，800元/晚"},
            {"category": "餐饮费",  "amount": 900,  "has_receipt": True,
             "note": "含宴请，人均超标"},
        ],
        "total_amount": 7100,
        "has_all_receipts": True,
        "within_limits": False,
        "justification": "",
        "submitted_days_after_trip": 6,
        "expected_outcome": "REJECTED",
        "reject_reason": "商务舱未经审批；住宿严重超标；餐饮超标；三项均不符合公司规定",
    },
    {
        "app_id": "EXP0098",
        "applicant": "陈静", "department": "人事部", "level": "P4",
        "destination": "杭州",
        "trip_start": _days_ago(3), "trip_end": _days_ago(2),
        "trip_days": 2, "purpose": "招聘面试",
        "expense_items": [
            {"category": "购物",    "amount": 1200, "has_receipt": True,
             "note": "出差期间个人购物，误填入报销"},
            {"category": "住宿费",  "amount": 800,  "has_receipt": True,
             "note": "400元/晚×2晚，合规"},
            {"category": "交通费",  "amount": 300,  "has_receipt": True,
             "note": "高铁二等座往返"},
        ],
        "total_amount": 2300,
        "has_all_receipts": True,
        "within_limits": False,
        "justification": "购物发票误夹入报销材料",
        "submitted_days_after_trip": 2,
        "expected_outcome": "REJECTED",
        "reject_reason": "包含非报销类别（个人购物），需删除后重新提交",
    },
    {
        "app_id": "EXP0099",
        "applicant": "孙雪", "department": "法务部", "level": "P5",
        "destination": "武汉",
        "trip_start": _days_ago(70), "trip_end": _days_ago(68),
        "trip_days": 3, "purpose": "合同谈判",
        "expense_items": [
            {"category": "机票",    "amount": 1200, "has_receipt": True,  "note": "经济舱"},
            {"category": "住宿费",  "amount": 1200, "has_receipt": True,  "note": "400元/晚×3晚"},
            {"category": "餐饮费",  "amount": 450,  "has_receipt": True,  "note": "工作餐"},
        ],
        "total_amount": 2850,
        "has_all_receipts": True,
        "within_limits": True,
        "justification": "因项目延期一直未提交",
        "submitted_days_after_trip": 67,
        "expected_outcome": "REJECTED",
        "reject_reason": "出差结束后67天提交，超过30天报销时限，且无总监审批豁免",
    },
    {
        "app_id": "EXP0100",
        "applicant": "吴敏", "department": "财务部", "level": "P5",
        "destination": "深圳",
        "trip_start": _days_ago(4), "trip_end": _days_ago(3),
        "trip_days": 2, "purpose": "审计",
        "expense_items": [
            {"category": "住宿费",  "amount": 1200, "has_receipt": True,
             "note": "600元/晚×2晚，合规"},
            {"category": "餐饮费",  "amount": 1500, "has_receipt": True,
             "note": "含客户招待，但走差旅报销通道"},
            {"category": "交通费",  "amount": 200,  "has_receipt": True,
             "note": "出租车"},
        ],
        "total_amount": 2900,
        "has_all_receipts": True,
        "within_limits": False,
        "justification": "招待客户餐费金额较大",
        "submitted_days_after_trip": 3,
        "expected_outcome": "REJECTED",
        "reject_reason": "客户招待费不能走差旅报销渠道，需走独立客户招待审批流程，餐饮严重超标",
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# 主生成函数
# ─────────────────────────────────────────────────────────────────────────────
def generate_mock_applications() -> List[Dict]:
    apps = []

    # Group 1: 1~80 auto-approve
    for i in range(1, 81):
        apps.append(_gen_auto_approve(i))

    # Group 2: 81~90 pipeline approve
    for i in range(81, 91):
        apps.append(_gen_pipeline_approve(i))

    # Group 3: 91~95 human review
    apps.extend(HUMAN_REVIEW_CASES)

    # Group 4: 96~100 reject
    apps.extend(REJECT_CASES)

    return apps


if __name__ == "__main__":
    apps = generate_mock_applications()
    from collections import Counter
    outcomes = Counter(a["expected_outcome"] for a in apps)
    print(f"总计生成: {len(apps)} 条申请")
    for outcome, count in sorted(outcomes.items()):
        print(f"  {outcome:<25} {count} 条")
    print(f"\n示例 (AUTO_APPROVE):")
    a = apps[0]
    print(f"  {a['app_id']} | {a['applicant']} | {a['department']} | {a['destination']} | {a['total_amount']}元")
    print(f"\n示例 (HUMAN_REVIEW):")
    a = apps[90]
    print(f"  {a['app_id']} | {a['applicant']} | {a['total_amount']}元 | {a.get('justification','')[:40]}")
    print(f"\n示例 (REJECTED):")
    a = apps[95]
    print(f"  {a['app_id']} | {a['applicant']} | 原因: {a.get('reject_reason','')[:50]}")
