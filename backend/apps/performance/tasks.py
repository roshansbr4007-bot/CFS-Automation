from celery import shared_task

from .scheduling import run_daily, run_month_close


@shared_task(name="performance.daily_kra_calculation")
def daily_kra_calculation() -> dict:
    """Celery Beat, daily (Phase 7.3, E17): provisional current month + previous month."""
    return run_daily()


@shared_task(name="performance.kra_month_close")
def kra_month_close() -> dict:
    """Celery Beat, the 1st of each month (Phase 7.3, E17): final previous month."""
    return run_month_close()
