import asyncio
from voxos.agents.listener.agent import ListenerAgent, ListenerInput

async def main():
    agent = ListenerAgent()
    res = await agent.process(ListenerInput(text='open chrome and search about raj shamani'))
    print(res.model_dump_json(indent=2))

asyncio.run(main())
