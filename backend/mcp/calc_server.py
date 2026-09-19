"""
计算器 + 日期时间 MCP 服务器
================
作用：用 FastMCP 封装一组本地工具（计算器 + 日期时间），
     作为独立的 MCP 服务器运行，供 Agent 通过 MCP 协议调用。

对应知识点：MCP（Model Context Protocol）+ fastmcp（课程已装）
- FastMCP 是 MCP 服务器的 Python 实现：用 @mcp.tool() 注册工具
- MCP 服务器是独立进程，通过 stdio / HTTP 与客户端通信
- 这里用 stdio 模式：Agent 启动子进程运行本脚本，通过标准输入输出通信

运行方式：python mcp/calc_server.py
"""
from datetime import datetime

from mcp.server.fastmcp import FastMCP

# 创建 MCP 服务器实例，name 是服务器标识
mcp = FastMCP(name="calc-datetime")


@mcp.tool()
def add(a: float, b: float) -> float:
    """计算两个数的加法：a + b"""
    return a + b


@mcp.tool()
def subtract(a: float, b: float) -> float:
    """计算两个数的减法：a - b"""
    return a - b


@mcp.tool()
def multiply(a: float, b: float) -> float:
    """计算两个数的乘法：a × b"""
    return a * b


@mcp.tool()
def divide(a: float, b: float) -> float:
    """计算两个数的除法：a ÷ b（除数不能为 0）"""
    if b == 0:
        return "除数不能为 0"
    return a / b


@mcp.tool()
def get_current_date() -> str:
    """获取今天的日期（年-月-日），例如 2026-08-25"""
    return datetime.now().strftime("%Y-%m-%d")


@mcp.tool()
def get_weekday() -> str:
    """获取今天是星期几（中文），例如 星期一"""
    weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    # weekday() 返回 0(周一)~6(周日)
    return weekdays[datetime.now().weekday()]


if __name__ == "__main__":
    # 以 stdio 模式运行：等待客户端通过标准输入/输出调用工具
    mcp.run(transport="stdio")
