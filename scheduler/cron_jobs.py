"""
定时任务调度 — APScheduler

生产部署：
  python scheduler/cron_jobs.py   # 持续运行，09:00 和 17:00 自动触发批处理

PoC 演示：
  from scheduler.cron_jobs import run_now   # 立即触发一次完整批处理
"""

import logging
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)

# 全局 store 和 pipeline，由外部注入（解耦）
_store    = None
_pipeline = None


def init(store, pipeline_module):
    """注入 store 和 pipeline，在启动调度器前调用。"""
    global _store, _pipeline
    _store    = store
    _pipeline = pipeline_module


def _manager_batch_job():
    """09:00 / 17:00 触发：处理所有 PENDING_MANAGER 队列。"""
    if _store is None or _pipeline is None:
        logger.error("调度器未初始化，请先调用 init()")
        return

    pending = _store.get_by_status("PENDING_MANAGER")
    if not pending:
        logger.info("[Manager Batch] 无待审批申请，跳过")
        return

    logger.info("[Manager Batch] 开始批处理，共 %d 条申请", len(pending))
    approved, rejected, flagged = 0, 0, 0

    for app in pending:
        result = _pipeline.process_manager_review(app, _store)
        if result == "APPROVED":
            approved += 1
        elif result == "REJECTED":
            rejected += 1
        else:
            flagged += 1

    logger.info(
        "[Manager Batch] 完成 | 通过→财务: %d | 拒绝: %d | 人工标记: %d",
        approved, rejected, flagged,
    )

    # 触发财务批处理（紧跟经理批处理之后）
    _finance_batch_job()


def _finance_batch_job():
    """经理批处理后自动触发：处理所有 PENDING_FINANCE 队列。"""
    if _store is None or _pipeline is None:
        return

    pending = _store.get_by_status("PENDING_FINANCE")
    if not pending:
        logger.info("[Finance Batch] 无待财务审批申请，跳过")
        return

    logger.info("[Finance Batch] 开始批处理，共 %d 条申请", len(pending))
    approved, rejected, flagged = 0, 0, 0

    for app in pending:
        manager_decision = app.get("manager_decision")
        result = _pipeline.process_finance_review(app, _store, manager_decision)
        if result == "APPROVED":
            approved += 1
        elif result == "REJECTED":
            rejected += 1
        else:
            flagged += 1

    logger.info(
        "[Finance Batch] 完成 | 最终批准: %d | 拒绝: %d | 上报人工: %d",
        approved, rejected, flagged,
    )


def start_blocking(store, pipeline_module):
    """启动阻塞式调度器（生产环境使用）。"""
    init(store, pipeline_module)
    scheduler = BlockingScheduler(timezone="Asia/Shanghai")

    # 每天 09:00 和 17:00 触发经理批处理
    scheduler.add_job(_manager_batch_job, CronTrigger(hour=9,  minute=0), id="manager_batch_morning")
    scheduler.add_job(_manager_batch_job, CronTrigger(hour=17, minute=0), id="manager_batch_afternoon")

    print("调度器已启动：每日 09:00 / 17:00 执行批处理")
    print("按 Ctrl+C 停止")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        print("调度器已停止")


def start_background(store, pipeline_module) -> BackgroundScheduler:
    """启动后台调度器（测试 / 集成时使用），返回 scheduler 对象。"""
    init(store, pipeline_module)
    scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
    scheduler.add_job(_manager_batch_job, CronTrigger(hour=9,  minute=0), id="manager_batch_morning")
    scheduler.add_job(_manager_batch_job, CronTrigger(hour=17, minute=0), id="manager_batch_afternoon")
    scheduler.start()
    return scheduler


def run_now(store, pipeline_module):
    """立即触发一次完整批处理（demo / 测试用）。"""
    init(store, pipeline_module)
    logger.info("手动触发批处理...")
    _manager_batch_job()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    # 独立运行时，需要在外部初始化 store 和 pipeline
    from state.store import ApplicationStore
    import pipeline as pl

    store = ApplicationStore()
    # 加载数据（生产环境从数据库读）
    from data.mock_applications import generate_mock_applications
    from pipeline import run_auto_approve_batch

    apps = generate_mock_applications()
    store.load_applications(apps)
    run_auto_approve_batch(store)

    start_blocking(store, pl)
