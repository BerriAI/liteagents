from liteagents.project import Cron

# Example: Cron(every=timedelta(hours=1), prompt="Summarize new issues", conversation_id="hourly")
CRONS: tuple[Cron, ...] = ()
