import argparse
import time

from app.logging_config import configure_logging
from app.pipeline import MessagePipeline
from app.settings import get_settings
from db.session import init_db
from telegram.collector import TelegramCollector


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=[
            "init-db",
            "collector",
            "history",
            "pipeline",
            "refresh",
            "position-refresh",
            "live-position-refresh",
            "live-entry-setup-refresh",
            "manual-live-trigger-refresh",
            "live-order-execute",
            "live-control-bot",
            "closed-position-refresh",
            "all",
        ],
        default="pipeline",
    )
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--poll-seconds", type=int, default=5)
    args = parser.parse_args()

    configure_logging()
    init_db()

    if args.mode == "init-db":
        return
    if args.mode == "collector":
        from telegram.collector import run_collector

        run_collector()
        return
    if args.mode == "history":
        import asyncio

        count = asyncio.run(TelegramCollector().collect_history_once(limit_per_channel=args.limit))
        print(f"Collected {count} historical messages.")
        return
    if args.mode == "live-control-bot":
        from live.control_bot import run_live_control_bot

        run_live_control_bot()
        return

    pipeline = MessagePipeline()
    if args.mode == "pipeline":
        count = pipeline.process_unanalyzed_messages(limit=args.limit)
        print(f"Processed {count} messages.")
        return
    if args.mode == "refresh":
        count = pipeline.refresh_open_events(limit=args.limit, force=True)
        print(f"Refreshed {count} events.")
        return
    if args.mode == "position-refresh":
        count = pipeline.refresh_open_positions(force=True)
        print(f"Fast-refreshed {count} paper positions.")
        return
    if args.mode == "closed-position-refresh":
        count = pipeline.refresh_closed_positions(force=True)
        print(f"Post-exit tracked {count} closed paper positions.")
        return
    if args.mode == "live-position-refresh":
        count = pipeline.refresh_live_positions()
        print(f"Evaluated {count} live positions.")
        return
    if args.mode == "live-entry-setup-refresh":
        count = pipeline.refresh_live_entry_setups(force=True)
        print(f"Evaluated {count} live entry setups.")
        return
    if args.mode == "manual-live-trigger-refresh":
        count = pipeline.refresh_manual_live_triggers()
        print(f"Evaluated {count} manual live triggers.")
        return
    if args.mode == "live-order-execute":
        count = pipeline.execute_live_orders()
        print(f"Executed {count} live orders.")
        return
    while True:
        pipeline.process_unanalyzed_messages(limit=args.limit)
        pipeline.refresh_open_events(limit=args.limit)
        pipeline.refresh_open_positions()
        pipeline.refresh_live_entry_setups()
        pipeline.refresh_manual_live_triggers()
        pipeline.refresh_live_positions()
        pipeline.execute_live_orders()
        pipeline.refresh_closed_positions()
        time.sleep(min(args.poll_seconds, get_settings().paper_fast_monitor_seconds))


if __name__ == "__main__":
    main()
