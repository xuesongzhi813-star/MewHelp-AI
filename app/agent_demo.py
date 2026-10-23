"""Run the bare loop without graph, archive writes or ticket side effects."""
import argparse
import asyncio
import json

from app.config import get_settings
from app.executor import execute_tool
from app.llm import build_workflow_model
from app.workflow.agent import run_agent_loop
from app.workflow.models import ModelGateway


async def main(message: str):
    result = await run_agent_loop(message, gateway=ModelGateway(build_workflow_model(get_settings())),
                                  execute=execute_tool, context={})
    result.pop("events")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--message", required=True)
    asyncio.run(main(parser.parse_args().message))
