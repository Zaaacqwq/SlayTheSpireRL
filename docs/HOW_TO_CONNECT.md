# 如何连接 STS2LLM

## 前置条件
- Slay the Spire 2 已安装,当前版本 v0.107.1(游戏路径:`D:\Steam\steamapps\common\Slay the Spire 2`)
- .NET 9 SDK(用于编译 mod)
- `uv`(用于运行 Python MCP server)

## 第一次安装

1. 编译并部署 mod:
   ```powershell
   .\tools\deploy_mod.ps1 -GameDir "D:\Steam\steamapps\common\Slay the Spire 2"
   ```
2. 启动游戏,进入 设置(Settings) → Mods,勾选启用 `STS2 MCP`,重启游戏。
3. 验证 mod 已加载:
   ```powershell
   Invoke-WebRequest http://localhost:15526/
   ```
   应返回 `{"message": "Hello from STS2 MCP v0.4.0", "status": "ok"}`。

   注意:这个 mod **没有 `/health` 路径**,根路径 `/` 就是健康检查。

## 连接 Claude Code

项目根目录下的 `.mcp.json` 已经配置好,指向 `mcp/server.py`(通过 `uv run` 启动,stdio transport)。

在 `E:\STS2LLM` 目录下打开/重启 Claude Code 即可自动加载 `sts2` 这个 MCP server。可以用 `.claude/commands/playsts2.md`(继承自上游 STS2MCP)驱动一整局游戏。

## 常见问题

- **mod 已找到但没加载**:检查 `%APPDATA%\SlayTheSpire2\logs\godot.log`,搜索 `mod`。如果看到 `it is set to disabled in settings`,去游戏内 Mods 设置里手动启用并重启。
- **游戏版本升级后 mod 编译失败**:STS2 处于抢先体验阶段,API 会随版本变化。先查上游 [STS2MCP issues](https://github.com/Gennadiyev/STS2MCP/issues) 有没有人报告同样的编译错误 / 对应的修复 PR；RL 后端升级还必须按 `plan/rl_v2_current_stage.md` 记录并重新通过协议、确定性和回归测试。
