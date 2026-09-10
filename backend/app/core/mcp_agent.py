"""
MCP 接入封装
================
作用：把 MCP 服务器（计算器 + 日期时间）的工具接入到 Agent，让 Agent 能调用。

对应知识点：MCP 接入（课程已装 fastmcp / mcp / langchain-mcp-adapters）
- stdio 连接：启动 MCP 服务器子进程，通过标准输入/输出通信
- load_mcp_tools：把 MCP 服务器暴露的工具，加载成 LangChain 工具
- create_agent(tools=[...])：挂给 Agent，Agent 自主决定何时调用

与 7b 的区别：
- 7b 检索工具：本地 Python 函数直接挂给 Agent（@tool）
- 7c MCP 工具：工具运行在独立的 MCP 服务器进程，通过 MCP 协议通信
  （体现"工具即服务"的松耦合设计）

注意：
- stdio_client / ClientSession 是【异步上下文管理器】，必须用 AsyncExitStack
- 子进程命令用 sys.executable（当前解释器），确保是 conda 环境的 python
"""
import asyncio
import contextlib
import sys

from langchain.agents import create_agent
from langchain_mcp_adapters.tools import load_mcp_tools
from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters

from app.config import BASE_DIR
from app.core.llm import get_model

# MCP 服务器脚本路径：backend/mcp/calc_server.py
MCP_SERVER_PATH = BASE_DIR / "backend" / "mcp" / "calc_server.py"


async def _load_mcp_tools():
    """异步加载 MCP 工具：启动子进程 + 建立会话 + 加载工具。

    返回 (tools, stack)：stack 是 AsyncExitStack，调用方用完必须 stack.aclose()。
    """
    # 1. 定义 MCP 服务器进程参数：用当前解释器运行 calc_server.py
    #    sys.executable 确保是 conda 环境的 python（不能用裸 "python"，
    #    可能指向别的解释器导致 import 不到 mcp 包）
    server_params = StdioServerParameters(command=sys.executable, args=[str(MCP_SERVER_PATH)])

    # 2. AsyncExitStack 统一管理所有异步资源的生命周期
    stack = contextlib.AsyncExitStack()

    # 3. stdio_client() 启动子进程；ClientSession 与之通信（都是异步上下文管理器）
    read, write = await stack.enter_async_context(stdio_client(server_params))
    session = await stack.enter_async_context(ClientSession(read, write))

    # 4. 初始化会话（MCP 协议要求先 initialize）
    await session.initialize()

    # 5. 把 MCP 工具加载成 LangChain 工具
    #    load_mcp_tools 是普通 async 函数（不是上下文管理器），await 即可
    tools = await load_mcp_tools(session)
    return tools, stack


def ask_mcp_agent(question: str) -> str:
    """用挂载了 MCP 工具的 Agent 回答问题。

    Agent 收到问题后，自主决定是否调用计算器 / 日期时间工具。
    （同步入口，内部跑事件循环；每次调用新建连接，用完即关）
    """
    async def _run():
        tools, stack = await _load_mcp_tools()
        try:
            agent = create_agent(
                model=get_model(),
                tools=tools,   # 挂载 MCP 工具（计算器 + 日期时间）
                system_prompt=(
                    "你是一个智能助手。"
                    "当用户需要计算或查询日期、星期时，调用对应工具。"
                    "调用工具后，根据工具返回结果组织回答。"
                ),
            )
            result = await agent.ainvoke(
                {"messages": [{"role": "user", "content": question}]}
            )
            return result["messages"][-1].content
        finally:
            await stack.aclose()  # 关闭子进程和会话，避免残留

    return asyncio.run(_run())
