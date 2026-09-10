"""প্রতি ২৪ ঘণ্টায় অটোমেটিক ডেটা আপডেট শিডিউলার"""
import threading
import time
from datetime import datetime, timedelta

import config


def _seconds_until_next_run(now: datetime | None = None) -> float:
    """পরবর্তী রান টাইম (DAILY_SYNC_HOUR:DAILY_SYNC_MINUTE) পর্যন্ত সেকেন্ড"""
    now = now or datetime.now()
    target = now.replace(
        hour=config.DAILY_SYNC_HOUR,
        minute=config.DAILY_SYNC_MINUTE,
        second=0,
        microsecond=0,
    )
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def start_scheduler(job) -> threading.Thread:
    """ব্যাকগ্রাউন্ড থ্রেডে ডেইলি সিঙ্ক শিডিউল করে"""

    def loop():
        while True:
            wait = _seconds_until_next_run()
            print(f"[scheduler] পরবর্তী অটো-আপডেট: {wait/3600:.1f} ঘণ্টা পরে")
            time.sleep(wait)
            try:
                result = job()
                print(f"[scheduler] অটো-আপডেট সফল: {result}")
            except Exception as exc:  # noqa: BLE001
                print(f"[scheduler] অটো-আপডেট ব্যর্থ: {exc}")
            time.sleep(60)  # একই মিনিটে ডাবল রান এড়ানো

    thread = threading.Thread(target=loop, name="daily-sync", daemon=True)
    thread.start()
    return thread
