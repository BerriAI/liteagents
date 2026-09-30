from agentchat import AgentChat, MessageContext
from agentchat.channels import Slack

from ..core import run_turn
from .mrkdwn import to_slack_mrkdwn


async def respond(context: MessageContext) -> str:
    return to_slack_mrkdwn(await run_turn(context.message.text, context.conversation.id))


async def main() -> None:
    app = AgentChat(channels=[Slack.from_env()])
    app.on_message(respond)
    await app.run()
