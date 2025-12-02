"""CLI interface for DEX Agentic Bot."""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Any, Dict, List, Optional

from app.config import load_settings
from app.mcp_client import MCPManager
from app.output import CLIOutput, OutputFormat
from app.types import PlannerResult


async def run_single_query(
    planner: Any,
    query: str,
    output: CLIOutput,
    context: Dict[str, Any],
) -> None:
    """Execute a single query and display the result."""
    output.status(f"Processing: {query}")

    try:
        result = await planner.run(query, context)
        output.result(result)
    except Exception as exc:
        output.error(f"Query failed: {exc}")
        raise


async def run_interactive(
    planner: Any,
    output: CLIOutput,
) -> None:
    """Run interactive REPL session."""
    output.info("DEX Agentic Bot - Interactive Mode")
    output.info("Type your queries, or use /quit to exit, /clear to reset context")
    output.info("-" * 50)

    context: Dict[str, Any] = {}
    conversation_history: List[Dict[str, str]] = []
    recent_tokens: List[Dict[str, str]] = []

    while True:
        try:
            query = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            output.info("\nGoodbye!")
            break

        if not query:
            continue

        # Handle commands
        if query.startswith("/"):
            cmd = query.lower()
            if cmd in ("/quit", "/exit", "/q"):
                output.info("Goodbye!")
                break
            elif cmd in ("/clear", "/reset"):
                conversation_history.clear()
                recent_tokens.clear()
                output.info("Context cleared.")
                continue
            elif cmd in ("/context", "/ctx"):
                if recent_tokens:
                    output.info("Recent tokens in context:")
                    for t in recent_tokens:
                        symbol = t.get("symbol", "?")
                        addr = t.get("address", "?")
                        addr_short = addr[:10] + "..." if len(addr) > 10 else addr
                        chain = t.get("chainId", "unknown")
                        output.info(f"  • {symbol} ({addr_short}) on {chain}")
                else:
                    output.info("No tokens in context")
                output.info(f"\nConversation history: {len(conversation_history)} messages")
                continue
            elif cmd in ("/help", "/h"):
                output.info("Commands: /quit, /clear, /context, /help")
                continue
            else:
                output.warning(f"Unknown command: {query}")
                continue

        # Build context
        context = {
            "conversation_history": conversation_history,
            "recent_tokens": recent_tokens,
        }

        try:
            result = await planner.run(query, context)
            output.result(result)

            # Update conversation history
            conversation_history.append({"role": "user", "content": query})
            conversation_history.append({"role": "assistant", "content": result.message})

            # Keep history bounded
            if len(conversation_history) > 20:
                conversation_history = conversation_history[-20:]

            # Update token context
            if result.tokens:
                recent_tokens = result.tokens[:10]

        except Exception as exc:
            output.error(f"Error: {exc}")


async def async_main() -> None:
    """Async CLI entrypoint."""
    parser = argparse.ArgumentParser(
        description="DEX Agentic Bot - Query token info across blockchains",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m app "search for PEPE on ethereum"
  python -m app --interactive
  python -m app --output json "top pools on base"
  python -m app "trending tokens"
        """,
    )

    parser.add_argument(
        "query",
        nargs="?",
        help="Natural language query (e.g., 'search for PEPE')",
    )
    parser.add_argument(
        "-i", "--interactive",
        action="store_true",
        help="Start interactive REPL mode",
    )
    parser.add_argument(
        "-o", "--output",
        choices=["text", "json", "table"],
        default="table",
        help="Output format (default: table)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Show debug information",
    )
    parser.add_argument(
        "--stdin",
        action="store_true",
        help="Read query from stdin",
    )

    args = parser.parse_args()

    # Determine output format
    try:
        output_format = OutputFormat(args.output)
    except ValueError:
        output_format = OutputFormat.TABLE

    output = CLIOutput(format=output_format, verbose=args.verbose)

    # Validate arguments
    if not args.interactive and not args.query and not args.stdin:
        parser.print_help()
        sys.exit(1)

    # Get query from stdin if requested
    query: Optional[str] = args.query
    if args.stdin:
        query = sys.stdin.read().strip()
        if not query:
            output.error("No query provided via stdin")
            sys.exit(1)

    # Load settings
    try:
        settings = load_settings()
    except Exception as exc:
        output.error(f"Failed to load settings: {exc}")
        output.info("Ensure .env file exists with GEMINI_API_KEY set")
        sys.exit(1)

    # Initialize MCP manager
    output.status("Starting MCP servers...")
    mcp_manager = MCPManager(
        dexscreener_cmd=settings.mcp_dexscreener_cmd,
        dexpaprika_cmd=settings.mcp_dexpaprika_cmd,
        honeypot_cmd=settings.mcp_honeypot_cmd,
    )

    try:
        await mcp_manager.start()
        output.status("MCP servers ready")
    except Exception as exc:
        output.error(f"Failed to start MCP servers: {exc}")
        sys.exit(1)

    # Create log callback for verbose mode
    def log_callback(level: str, message: str, data: Optional[Dict[str, Any]] = None) -> None:
        if level == "error":
            output.error(message)
        elif level == "tool":
            output.debug(message, data)
        else:
            output.debug(message, data)

    # Initialize planner
    from app.agent import AgenticPlanner

    planner = AgenticPlanner(
        api_key=settings.gemini_api_key,
        mcp_manager=mcp_manager,
        model_name=settings.gemini_model,
        max_iterations=settings.agentic_max_iterations,
        max_tool_calls=settings.agentic_max_tool_calls,
        timeout_seconds=settings.agentic_timeout_seconds,
        verbose=args.verbose,
        log_callback=log_callback if args.verbose else None,
    )

    try:
        if args.interactive:
            await run_interactive(planner, output)
        elif query:
            await run_single_query(planner, query, output, context={})
    except KeyboardInterrupt:
        output.info("\nInterrupted")
    finally:
        output.status("Shutting down MCP servers...")
        await mcp_manager.shutdown()


def main() -> None:
    """Synchronous wrapper for CLI entry."""
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
