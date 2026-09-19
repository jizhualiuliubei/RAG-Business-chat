"""
MCP 接入验证脚本
================
直接连 MCP 服务器，验证工具能正常调用（不经 Agent）。
用于开发时确认 MCP 连接和工具本身没问题。
运行：python mcp/test_mcp.py（在 backend 目录下，用项目所用的 Python 环境）
"""
import asyncio
import sys
from pathlib import Path

# 直接运行 mcp/test_mcp.py 时，sys.path[0] 是 backend/mcp；
# 手动把 backend 加进去，保证和 uvicorn 一样能导入 app 包。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters

from app.config import BASE_DIR

SERVER_PATH = BASE_DIR / "backend" / "mcp" / "calc_server.py"


async def main():
    # 用当前解释器（sys.executable），确保是 conda 环境的 python
    server_params = StdioServerParameters(command=sys.executable, args=[str(SERVER_PATH)])
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            # 列出服务器暴露的工具
            tools = await session.list_tools()
            print("MCP 服务器工具:", [t.name for t in tools.tools])

            # 测试调用：加法
            res = await session.call_tool("add", {"a": 23, "b": 45})
            print(f"调用 add(23,45) -> {res.content[0].text}")

            # 测试调用：乘法
            res = await session.call_tool("multiply", {"a": 12, "b": 8})
            print(f"调用 multiply(12,8) -> {res.content[0].text}")

            # 测试调用：日期
            res = await session.call_tool("get_current_date", {})
            print(f"调用 get_current_date -> {res.content[0].text}")

            # 测试调用：星期
            res = await session.call_tool("get_weekday", {})
            print(f"调用 get_weekday -> {res.content[0].text}")


if __name__ == "__main__":
    asyncio.run(main())
