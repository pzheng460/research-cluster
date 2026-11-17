"""Run the optional model-backed coding agent in a separate CPU process."""
import os
import signal
import sys
from pathlib import Path


def main():
    def stop(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop)
    from openhands.sdk import Agent, Conversation, LLM, Tool
    from openhands.tools.file_editor import FileEditorTool
    from openhands.tools.terminal import TerminalTool

    options = {"model": os.environ["LLM_MODEL"], "api_key": os.environ["LLM_API_KEY"]}
    if os.getenv("LLM_BASE_URL"):
        options["base_url"] = os.environ["LLM_BASE_URL"]
    agent = Agent(llm=LLM(**options), tools=[Tool(name=TerminalTool.name, params={"terminal_type": "subprocess"}), Tool(name=FileEditorTool.name)])
    conversation = Conversation(agent=agent, workspace=str(Path(sys.argv[1]).resolve()),
        max_iteration_per_run=int(sys.argv[3]), max_budget_per_run=float(sys.argv[4]),
        persistence_dir=str(Path(sys.argv[1]).parent / "agent-state" / Path(sys.argv[1]).name), delete_on_close=False)
    try:
        conversation.send_message(Path(sys.argv[2]).read_text())
        conversation.run()
    finally:
        conversation.close()


if __name__ == "__main__":
    main()
