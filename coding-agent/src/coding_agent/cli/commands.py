from __future__ import annotations

import html as _html
from typing import Any, Callable, Coroutine

from coding_agent.cli.terminal_output import get_prompt_output, print_html, print_pt


def _out():
    return get_prompt_output()


_COMMANDS: dict[str, Callable[..., Coroutine[Any, Any, None]]] = {}


def command(name: str, description: str = ""):
    def decorator(func: Callable[..., Coroutine[Any, Any, None]]):
        func._command_name = name
        func._command_description = description
        _COMMANDS[name] = func
        return func

    return decorator


@command("help", "Show available commands")
async def cmd_help(args: list[str], context: dict[str, Any]) -> None:
    output = _out()
    print_html("<b>Available Commands:</b>", output=output)
    print_pt(output=output)
    for name, func in sorted(_COMMANDS.items()):
        desc = _html.escape(getattr(func, "_command_description", ""))
        print_html(
            f"  <ansicyan>/{name}</ansicyan>  <ansibrightblack>{desc}</ansibrightblack>",
            output=output,
        )
    print_pt(output=output)
    print_html("<b>Shell Mode:</b>", output=output)
    print_pt("  ! - Instantly enter bash mode (no Enter needed)", output=output)
    print_pt("  Escape or Backspace on empty prompt - Return to chat", output=output)
    print_pt(output=output)
    print_html("<b>Input:</b>", output=output)
    print_pt("  Enter - Submit message", output=output)
    print_pt("  Shift+Enter - New line", output=output)
    print_pt("  Ctrl+C × 2 - Exit", output=output)
    print_pt(output=output)
    print_pt("Type your message normally to chat with the agent.", output=output)
    print_pt(output=output)


@command("exit", "Exit the agent")
async def cmd_exit(args: list[str], context: dict[str, Any]) -> None:
    print_html("<ansigreen>Goodbye!</ansigreen>", output=_out())
    context["should_exit"] = True


@command("quit", "Exit the agent (alias)")
async def cmd_quit(args: list[str], context: dict[str, Any]) -> None:
    await cmd_exit(args, context)


@command("clear", "Clear the screen")
async def cmd_clear(args: list[str], context: dict[str, Any]) -> None:
    print("\033[2J\033[H", end="")


@command("plan", "Show current plan")
async def cmd_plan(args: list[str], context: dict[str, Any]) -> None:
    planner = context.get("planner")
    if planner and planner.tasks:
        output = _out()
        print_html("<b>Current Plan:</b>", output=output)
        print_pt(output=output)
        print_pt(planner.to_text(), output=output)
    else:
        print_pt("No active plan. Use todo_write to create one.", output=_out())


@command("model", "Show or change model")
async def cmd_model(args: list[str], context: dict[str, Any]) -> None:
    if args:
        new_model = args[0]
        if len(new_model) < 2 or len(new_model) > 100:
            print_html(
                f"<ansired>Invalid model name: {new_model}</ansired>", output=_out()
            )
            return
        context["model"] = new_model
        output = _out()
        print_html(
            f"Model changed to: <ansicyan><b>{new_model}</b></ansicyan>", output=output
        )
        print_html(
            "<ansibrightblack>Note: Model change will take effect on next turn.</ansibrightblack>",
            output=output,
        )
    else:
        current = context.get("model", "unknown")
        print_html(
            f"Current model: <ansicyan><b>{current}</b></ansicyan>", output=_out()
        )


@command("tools", "List available tools")
async def cmd_tools(args: list[str], context: dict[str, Any]) -> None:
    registry = context.get("tool_registry")
    if registry:
        output = _out()
        print_html("<b>Available Tools:</b>", output=output)
        print_pt(output=output)
        for name in sorted(registry.list_tools()):
            print_html(f"  <ansicyan>•</ansicyan> {name}", output=output)
        print_pt(output=output)
    else:
        print_pt("No tool registry available", output=_out())


@command("skill", "List or activate skills  (/skill | /skill <name> | /skill off)")
async def cmd_skill(args: list[str], context: dict[str, Any]) -> None:
    output = _out()
    skills_plugin = context.get("skills_plugin")
    if skills_plugin is None:
        print_pt("Skills plugin is not enabled.", output=output)
        return

    if not args:
        skills_with_descs = skills_plugin.list_skills_with_descriptions()
        active_name = skills_plugin.active_skill_name
        if not skills_with_descs:
            print_pt(
                "No skills available. Add SKILL.md files to .agents/skills/ directories.",
                output=output,
            )
            return
        print_html("<b>Available skills:</b>", output=output)
        print_pt(output=output)
        for skill_name, desc in skills_with_descs:
            if active_name and active_name == skill_name:
                print_html(
                    f"  <ansicyan><b>• {skill_name}</b></ansicyan>  <ansibrightblack>{desc}</ansibrightblack>"
                    f"  <ansigreen>← active</ansigreen>",
                    output=output,
                )
            else:
                print_html(
                    f"  <ansicyan>•</ansicyan> <b>{skill_name}</b>  <ansibrightblack>{desc}</ansibrightblack>",
                    output=output,
                )
        print_pt(output=output)
        if active_name:
            print_html(
                f"Active skill: <ansigreen><b>{active_name}</b></ansigreen>",
                output=output,
            )
        else:
            print_html(
                "<ansibrightblack>No skill is currently active.</ansibrightblack>",
                output=output,
            )
        return

    cmd = args[0]

    if cmd == "off":
        skills_plugin.deactivate()
        print_html(
            "<ansibrightblack>Skill deactivated.</ansibrightblack>", output=output
        )
        return

    skill_name = cmd
    pipeline_ctx = context.get("pipeline_ctx")
    if pipeline_ctx is not None:
        msg = skills_plugin.request_skill(pipeline_ctx, skill_name)
    else:
        msg = skills_plugin.activate_immediately(skill_name)
    print_pt(msg, output=output)


@command("mcp", "Manage MCP servers  (/mcp | /mcp reload)")
async def cmd_mcp(args: list[str], context: dict[str, Any]) -> None:
    output = _out()
    mcp_plugin = context.get("mcp_plugin")
    if mcp_plugin is None:
        print_pt(
            "MCP plugin is not enabled. Add [mcp.servers.*] to agent.toml to configure servers.",
            output=output,
        )
        return

    if args and args[0] == "reload":
        msg = mcp_plugin.reload_servers()
        print_pt(msg, output=output)
        return

    servers = mcp_plugin.list_servers()
    if not servers:
        print_pt("No MCP servers configured.", output=output)
        return

    print_html("<b>MCP Servers:</b>", output=output)
    print_pt(output=output)
    for srv in servers:
        if srv["alive"]:
            status_html = "<ansigreen>✓ running</ansigreen>"
        else:
            status_html = "<ansired>✗ stopped</ansired>"
        print_html(f"  <b>{srv['name']}</b>  [{status_html}]", output=output)
        if srv["tools"]:
            for t in srv["tools"]:
                print_html(
                    f"    <ansibrightblack>• {t}</ansibrightblack>", output=output
                )
        else:
            print_html(
                "    <ansibrightblack>(no tools)</ansibrightblack>", output=output
            )
    print_pt(output=output)
    total = sum(len(s["tools"]) for s in servers)
    print_html(
        f"<ansibrightblack>{len(servers)} server(s), {total} tool(s) total.</ansibrightblack>",
        output=output,
    )


async def handle_command(input_text: str, context: dict[str, Any]) -> bool:
    if not input_text.startswith("/"):
        return False

    parts = input_text[1:].strip().split()
    if not parts:
        await cmd_help([], context)
        return True

    cmd_name = parts[0].lower()
    args = parts[1:]

    if cmd_name in _COMMANDS:
        try:
            await _COMMANDS[cmd_name](args, context)
        except Exception as e:
            print_html(f"<ansired>Command error: {e}</ansired>", output=_out())
        return True
    else:
        print_html(
            f"<ansired>Unknown command: /{cmd_name}.</ansired> Type /help for available commands.",
            output=_out(),
        )
        return True


def get_command_completions() -> list[str]:
    return [f"/{name}" for name in _COMMANDS.keys()]
