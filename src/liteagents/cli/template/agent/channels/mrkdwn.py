import re

_BOLD = re.compile(r"\*\*(.+?)\*\*")
_HEADING = re.compile(r"^#{1,6}\s*(.+)$", re.MULTILINE)


def to_slack_mrkdwn(text: str) -> str:
    """Slack shows **bold** and # headings literally, so convert them to mrkdwn."""
    text = _HEADING.sub(lambda m: f"*{m.group(1).strip()}*", text)
    return _BOLD.sub(lambda m: f"*{m.group(1)}*", text)
