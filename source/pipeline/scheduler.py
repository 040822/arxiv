"""APScheduler configuration."""

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from source.settings import get_schedule_config
from .orchestrator import daily_pipeline

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler()


def configure_daily_job(schedule=None):
    """按 settings.json 中的配置启用、禁用或重建每日任务。"""
    schedule = schedule or get_schedule_config()
    try:
        if hasattr(scheduler, "get_job") and scheduler.get_job("daily_pipeline"):
            scheduler.remove_job("daily_pipeline")
    except Exception as e:
        logger.warning(f"Failed to remove existing daily job: {e}")

    if not schedule.get("enabled", True):
        logger.info("Scheduler daily job disabled.")
        return

    scheduler.add_job(
        daily_pipeline,
        "cron",
        day_of_week=",".join(schedule.get("days_of_week", ["mon", "tue", "wed", "thu", "fri", "sat", "sun"])),
        hour=schedule["hour"],
        minute=schedule["minute"],
        id="daily_pipeline",
        name="AI 论文日报",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=60,
    )
    logger.info(f"Scheduler daily job configured: {schedule['hour']:02d}:{schedule['minute']:02d}")
