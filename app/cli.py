"""CLI interface for DEX Agentic Bot."""

from __future__ import annotations

import argparse
import asyncio
import re
import shlex
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import load_settings
from app.mcp_client import MCPManager
from app.output import CLIOutput, OutputFormat
from app.telegram_notifier import TelegramNotifier
from app.types import PlannerResult
from app.watchlist import WatchlistDB
from app.watchlist_poller import WatchlistPoller, TriggeredAlert
from app.autonomous_agent import AutonomousWatchlistAgent
from app.autonomous_scheduler import AutonomousScheduler


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
    watchlist_db: WatchlistDB,
    mcp_manager: MCPManager,
    poller: Optional[WatchlistPoller] = None,
    telegram: Optional[TelegramNotifier] = None,
    autonomous_scheduler: Optional[AutonomousScheduler] = None,
) -> None:
    """Run interactive REPL session."""
    output.info("DEX Agentic Bot - Interactive Mode")
    output.info("Type your queries, or use /help for commands")
    output.info("-" * 50)

    context: Dict[str, Any] = {}
    conversation_history: List[Dict[str, str]] = []
    recent_tokens: List[Dict[str, str]] = []

    # Alert queue for background notifications
    alert_queue: asyncio.Queue[TriggeredAlert] = asyncio.Queue()

    def alert_callback(alert: TriggeredAlert) -> None:
        """Queue alerts for display and send to Telegram."""
        alert_queue.put_nowait(alert)
        # Send to Telegram in background (fire and forget)
        if telegram and telegram.is_configured:
            asyncio.create_task(_send_telegram_alert(telegram, alert, output))

    # Background task to display alerts immediately
    async def alert_display_loop() -> None:
        """Monitor queue and display alerts in real-time."""
        while True:
            try:
                alert = await alert_queue.get()
                output.alert_notification(alert)
            except asyncio.CancelledError:
                break

    alert_display_task: Optional[asyncio.Task] = None

    # Start poller if enabled
    if poller:
        poller.alert_callback = alert_callback
        await poller.start()
        output.info("📡 Background price monitoring started")
        # Start alert display task
        alert_display_task = asyncio.create_task(alert_display_loop())

    # Start Telegram polling if enabled
    if telegram and telegram.is_configured:
        await telegram.start_polling()
        output.info("📱 Telegram notifications enabled (send /help to bot)")

    # Start autonomous scheduler if enabled
    if autonomous_scheduler:
        await autonomous_scheduler.start()
        interval_mins = autonomous_scheduler.interval_seconds // 60
        output.info(f"🤖 Autonomous watchlist manager started (every {interval_mins} mins)")

    try:
        while True:
            try:
                loop = asyncio.get_running_loop()
                query = (await loop.run_in_executor(None, input, "\n> ")).strip()
            except (EOFError, KeyboardInterrupt):
                output.info("\nGoodbye!")
                break

            if not query:
                continue

            # Handle commands
            if query.startswith("/"):
                handled = await _handle_command(
                    query, output, watchlist_db, poller, mcp_manager,
                    conversation_history, recent_tokens, autonomous_scheduler
                )
                if handled == "quit":
                    break
                if handled:
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

    finally:
        if alert_display_task:
            alert_display_task.cancel()
            try:
                await alert_display_task
            except asyncio.CancelledError:
                pass
        if autonomous_scheduler:
            await autonomous_scheduler.stop()
        if poller:
            await poller.stop()
        if telegram:
            await telegram.close()


async def _send_telegram_alert(
    telegram: TelegramNotifier,
    alert: TriggeredAlert,
    output: CLIOutput,
) -> None:
    """Send alert to Telegram in background."""
    try:
        success = await telegram.send_alert(alert)
        if not success:
            output.debug("Failed to send Telegram alert")
    except Exception as e:
        output.debug(f"Telegram error: {e}")


async def _handle_command(
    query: str,
    output: CLIOutput,
    db: WatchlistDB,
    poller: Optional[WatchlistPoller],
    mcp_manager: MCPManager,
    conversation_history: List[Dict[str, str]],
    recent_tokens: List[Dict[str, str]],
    autonomous_scheduler: Optional[AutonomousScheduler] = None,
) -> Optional[str]:
    """Handle slash commands. Returns 'quit' to exit, True if handled, None otherwise."""
    parts = query.split()
    cmd = parts[0].lower()

    # Exit commands
    if cmd in ("/quit", "/exit", "/q"):
        output.info("Goodbye!")
        return "quit"

    # Clear context
    if cmd in ("/clear", "/reset"):
        conversation_history.clear()
        recent_tokens.clear()
        output.info("Context cleared.")
        return True

    # Show context
    if cmd in ("/context", "/ctx"):
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
        return True

    # Help
    if cmd in ("/help", "/h"):
        output.help_panel()
        return True

    # Watchlist commands
    if cmd == "/watch":
        await _cmd_watch(parts[1:], output, db, mcp_manager, recent_tokens)
        return True

    if cmd == "/unwatch":
        await _cmd_unwatch(parts[1:], output, db)
        return True

    if cmd == "/watchlist":
        await _cmd_watchlist(output, db, poller)
        return True

    if cmd == "/alert":
        await _cmd_alert(parts[1:], output, db)
        return True

    if cmd == "/alerts":
        await _cmd_alerts(parts[1:], output, db)
        return True

    # Autonomous agent commands
    if cmd == "/autonomous":
        await _cmd_autonomous(parts[1:], output, db, autonomous_scheduler)
        return True

    output.warning(f"Unknown command: {query}. Use /help for available commands.")
    return True


async def _search_token(
    symbol: str,
    chain: Optional[str],
    mcp_manager: MCPManager,
) -> Optional[Dict[str, str]]:
    """Search for a token using MCP tools and return its info."""
    # Try DexScreener first
    dexscreener = mcp_manager.get_client("dexscreener")
    if dexscreener:
        try:
            # Search for the token
            query = f"{symbol} {chain}" if chain else symbol
            result = await dexscreener.call_tool("searchPairs", {"query": query})
            
            if isinstance(result, dict) and result.get("pairs"):
                pairs = result["pairs"]
                
                # Filter by chain if specified
                for pair in pairs:
                    base_token = pair.get("baseToken", {})
                    pair_chain = pair.get("chainId", "").lower()
                    
                    if base_token.get("symbol", "").upper() == symbol.upper():
                        if chain is None or pair_chain == chain.lower():
                            return {
                                "address": base_token.get("address", ""),
                                "symbol": base_token.get("symbol", symbol),
                                "chain": pair_chain,
                            }
                
                # If exact match not found, return first result
                if pairs:
                    first_pair = pairs[0]
                    base_token = first_pair.get("baseToken", {})
                    return {
                        "address": base_token.get("address", ""),
                        "symbol": base_token.get("symbol", symbol),
                        "chain": first_pair.get("chainId", chain or "unknown"),
                    }
        except Exception:
            pass

    # Fallback to DexPaprika search
    dexpaprika = mcp_manager.get_client("dexpaprika")
    if dexpaprika:
        try:
            result = await dexpaprika.call_tool("search", {"query": symbol})
            
            if isinstance(result, dict):
                tokens = result.get("tokens", [])
                for token in tokens:
                    token_chain = token.get("network", "").lower()
                    if chain is None or token_chain == chain.lower():
                        return {
                            "address": token.get("address", ""),
                            "symbol": token.get("symbol", symbol),
                            "chain": token_chain,
                        }
        except Exception:
            pass

    return None


async def _cmd_watch(
    args: List[str],
    output: CLIOutput,
    db: WatchlistDB,
    mcp_manager: MCPManager,
    recent_tokens: List[Dict[str, str]],
) -> None:
    """Handle /watch command."""
    if not args:
        output.error("Usage: /watch <token_address_or_symbol> [chain]")
        return

    token_input = args[0]
    chain = args[1].lower() if len(args) > 1 else None

    # Check if it's an address (starts with 0x or is long alphanumeric)
    is_address = token_input.startswith("0x") or len(token_input) > 20

    if is_address:
        # Direct address - need chain
        if not chain:
            output.error("Chain required when using address. Usage: /watch <address> <chain>")
            return

        entry = await db.add_entry(
            token_address=token_input,
            symbol=token_input[:8].upper(),  # Placeholder symbol
            chain=chain,
        )
        output.info(f"✅ Added {token_input[:10]}... on {chain} to watchlist")
    else:
        # Symbol - look in recent tokens first
        symbol = token_input.upper()
        found = None

        for t in recent_tokens:
            if t.get("symbol", "").upper() == symbol:
                if chain is None or t.get("chainId", "").lower() == chain:
                    found = t
                    break

        if found:
            entry = await db.add_entry(
                token_address=found.get("address", ""),
                symbol=symbol,
                chain=found.get("chainId", chain or "unknown"),
            )
            addr_short = entry.token_address[:10] + "..."
            output.info(f"✅ Added {symbol} ({addr_short}) on {entry.chain} to watchlist")
        else:
            # No match in context - auto-search for the token
            output.status(f"Searching for {symbol}...")
            
            search_result = await _search_token(symbol, chain, mcp_manager)
            
            if search_result and search_result.get("address"):
                entry = await db.add_entry(
                    token_address=search_result["address"],
                    symbol=search_result.get("symbol", symbol),
                    chain=search_result.get("chain", chain or "unknown"),
                )
                addr_short = entry.token_address[:10] + "..."
                output.info(f"✅ Added {entry.symbol} ({addr_short}) on {entry.chain} to watchlist")
            else:
                output.error(f"Could not find token {symbol}. Try: /watch <address> <chain>")


async def _cmd_unwatch(args: List[str], output: CLIOutput, db: WatchlistDB) -> None:
    """Handle /unwatch command."""
    if not args:
        output.error("Usage: /unwatch <token_address_or_symbol> [chain]")
        return

    token_input = args[0]
    chain = args[1].lower() if len(args) > 1 else None

    is_address = token_input.startswith("0x") or len(token_input) > 20

    if is_address:
        removed = await db.remove_entry(token_input, chain)
    else:
        removed = await db.remove_entry_by_symbol(token_input.upper(), chain)

    if removed:
        output.info(f"✅ Removed {token_input} from watchlist")
    else:
        output.warning(f"Token {token_input} not found in watchlist")


async def _cmd_watchlist(
    output: CLIOutput,
    db: WatchlistDB,
    poller: Optional[WatchlistPoller],
) -> None:
    """Handle /watchlist command."""
    entries = await db.list_entries()

    # Optionally trigger a price refresh
    if poller and entries:
        output.status("Refreshing prices...")
        await poller.check_now()
        entries = await db.list_entries()  # Reload with updated prices

    output.watchlist_table(entries)


async def _cmd_alert(args: List[str], output: CLIOutput, db: WatchlistDB) -> None:
    """Handle /alert command."""
    # Parse: /alert <token> above|below <price>
    if len(args) < 3:
        output.error("Usage: /alert <token> above|below <price>")
        return

    token_input = args[0]
    direction = args[1].lower()
    price_str = args[2]

    if direction not in ("above", "below"):
        output.error("Direction must be 'above' or 'below'")
        return

    try:
        price = float(price_str.replace("$", "").replace(",", ""))
    except ValueError:
        output.error(f"Invalid price: {price_str}")
        return

    # Find the entry
    is_address = token_input.startswith("0x") or len(token_input) > 20
    if is_address:
        entry = await db.get_entry(token_address=token_input)
    else:
        entry = await db.get_entry(symbol=token_input.upper())

    if not entry:
        output.error(f"Token {token_input} not in watchlist. Add it first with /watch")
        return

    # Update alert
    if direction == "above":
        await db.update_alert(entry.id, alert_above=price)
    else:
        await db.update_alert(entry.id, alert_below=price)

    output.info(f"✅ Alert set: {entry.symbol} {direction} ${price}")


async def _cmd_alerts(args: List[str], output: CLIOutput, db: WatchlistDB) -> None:
    """Handle /alerts command."""
    if args and args[0].lower() == "clear":
        count = await db.acknowledge_alerts()
        output.info(f"✅ Cleared {count} alert(s)")
        return

    if args and args[0].lower() == "history":
        history = await db.get_alert_history(limit=20)
        output.alerts_table(history)
        return

    # Default: show unacknowledged alerts
    alerts = await db.get_unacknowledged_alerts()
    if not alerts:
        output.info("No pending alerts")
        return

    output.alerts_table(alerts)


async def _cmd_autonomous(
    args: List[str],
    output: CLIOutput,
    db: WatchlistDB,
    scheduler: Optional[AutonomousScheduler],
) -> None:
    """Handle /autonomous command."""
    if not args:
        # Show help
        output.info("Autonomous Watchlist Manager Commands:")
        output.info("  /autonomous status  - Show current status")
        output.info("  /autonomous run     - Trigger immediate cycle")
        output.info("  /autonomous start   - Start the scheduler")
        output.info("  /autonomous stop    - Stop the scheduler")
        output.info("  /autonomous list    - List autonomous tokens")
        output.info("  /autonomous clear   - Remove all autonomous tokens")
        return

    subcmd = args[0].lower()

    if subcmd == "status":
        if not scheduler:
            output.warning("Autonomous mode is not enabled. Use --autonomous flag.")
            return
        status = scheduler.get_status()
        output.info("🤖 Autonomous Watchlist Status:")
        output.info(f"  Running: {'✅ Yes' if status['running'] else '❌ No'}")
        output.info(f"  Interval: {status['interval_seconds'] // 60} minutes")
        output.info(f"  Max tokens: {status['max_tokens']}")
        output.info(f"  Cycles completed: {status['cycle_count']}")
        if status['last_cycle']:
            output.info(f"  Last cycle: {status['last_cycle']}")
        if status['last_summary']:
            output.info(f"  Last result: {status['last_summary']}")
        return

    if subcmd == "run":
        if not scheduler:
            output.warning("Autonomous mode is not enabled. Use --autonomous flag.")
            return
        output.status("Running autonomous cycle...")
        try:
            result = await scheduler.run_cycle_now()
            output.info(f"✅ Cycle complete: {result.summary}")
            if result.errors:
                for err in result.errors:
                    output.warning(f"  Error: {err}")
        except Exception as e:
            output.error(f"Cycle failed: {e}")
        return

    if subcmd == "start":
        if not scheduler:
            output.warning("Autonomous mode is not enabled. Use --autonomous flag.")
            return
        if scheduler.is_running:
            output.info("Scheduler is already running.")
            return
        await scheduler.start()
        output.info("✅ Autonomous scheduler started")
        return

    if subcmd == "stop":
        if not scheduler:
            output.warning("Autonomous mode is not enabled. Use --autonomous flag.")
            return
        if not scheduler.is_running:
            output.info("Scheduler is not running.")
            return
        await scheduler.stop()
        output.info("✅ Autonomous scheduler stopped")
        return

    if subcmd == "list":
        entries = await db.list_autonomous_entries()
        if not entries:
            output.info("No tokens in autonomous watchlist.")
            return
        output.info(f"🤖 Autonomous Watchlist ({len(entries)} tokens):")
        for entry in entries:
            price_fmt = f"${entry.last_price:.8f}" if entry.last_price else "—"
            score = entry.momentum_score or 0
            output.info(
                f"  • {entry.symbol} @ {price_fmt} (Score: {score:.0f})"
            )
            if entry.alert_above or entry.alert_below:
                above = f"${entry.alert_above:.8f}" if entry.alert_above else "—"
                below = f"${entry.alert_below:.8f}" if entry.alert_below else "—"
                output.info(f"    Triggers: ↑{above} ↓{below}")
        return

    if subcmd == "clear":
        count = await db.clear_autonomous_watchlist()
        output.info(f"✅ Cleared {count} autonomous token(s)")
        return

    output.warning(f"Unknown subcommand: {subcmd}. Use /autonomous for help.")


def _validate_command_exists(command: str, label: str, optional: bool = False) -> None:
    """Ensure the first token of a command is available on PATH or as a file."""
    if not command:
        if optional:
            return
        raise ValueError(f"{label} command is empty.")
    parts = shlex.split(command)
    if not parts:
        if optional:
            return
        raise ValueError(f"{label} command is invalid.")
    binary = parts[0]
    if shutil.which(binary) or Path(binary).exists():
        return
    raise FileNotFoundError(
        f"{label} command not found: '{binary}'. Install it or update MCP settings."
    )


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
    parser.add_argument(
        "--no-honeypot",
        action="store_true",
        help="Disable honeypot MCP server (faster startup)",
    )
    parser.add_argument(
        "--no-rugcheck",
        action="store_true",
        help="Disable rugcheck MCP server (faster startup)",
    )
    parser.add_argument(
        "--no-polling",
        action="store_true",
        help="Disable background price polling for watchlist alerts",
    )
    parser.add_argument(
        "--no-telegram",
        action="store_true",
        help="Disable Telegram notifications",
    )
    parser.add_argument(
        "--autonomous",
        action="store_true",
        help="Enable autonomous watchlist management",
    )
    parser.add_argument(
        "--autonomous-interval",
        type=int,
        default=None,
        help="Autonomous cycle interval in minutes (default: 60)",
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

    # Validate external MCP commands early
    try:
        _validate_command_exists(settings.mcp_dexscreener_cmd, "DexScreener")
        _validate_command_exists(settings.mcp_dexpaprika_cmd, "DexPaprika")
        _validate_command_exists(
            settings.mcp_honeypot_cmd,
            "Honeypot",
            optional=args.no_honeypot or not settings.mcp_honeypot_cmd,
        )
        _validate_command_exists(
            settings.mcp_rugcheck_cmd,
            "Rugcheck",
            optional=args.no_rugcheck or not settings.mcp_rugcheck_cmd,
        )
    except Exception as exc:
        output.error(str(exc))
        sys.exit(1)

    # Initialize MCP manager
    output.status("Starting MCP servers...")
    mcp_manager = MCPManager(
        dexscreener_cmd=settings.mcp_dexscreener_cmd,
        dexpaprika_cmd=settings.mcp_dexpaprika_cmd,
        honeypot_cmd="" if args.no_honeypot else settings.mcp_honeypot_cmd,
        rugcheck_cmd="" if args.no_rugcheck else settings.mcp_rugcheck_cmd,
    )

    try:
        await mcp_manager.start()
        output.status("MCP servers ready")
    except Exception as exc:
        output.error(f"Failed to start MCP servers: {exc}")
        sys.exit(1)

    # Initialize watchlist database
    watchlist_db = WatchlistDB(db_path=settings.watchlist_db_path)
    try:
        await watchlist_db.connect()
    except Exception as exc:
        output.error(f"Failed to initialize watchlist database: {exc}")
        await mcp_manager.shutdown()
        sys.exit(1)

    # Initialize poller if enabled
    poller: Optional[WatchlistPoller] = None
    if args.interactive and settings.watchlist_poll_enabled and not args.no_polling:
        poller = WatchlistPoller(
            db=watchlist_db,
            mcp_manager=mcp_manager,
            poll_interval=settings.watchlist_poll_interval,
        )

    # Initialize Telegram notifier if enabled
    telegram: Optional[TelegramNotifier] = None
    if (
        args.interactive
        and settings.telegram_alerts_enabled
        and not args.no_telegram
        and settings.telegram_bot_token
        and settings.telegram_chat_id
    ):
        telegram = TelegramNotifier(
            bot_token=settings.telegram_bot_token,
            chat_id=settings.telegram_chat_id,
        )

    # Create log callback for verbose mode
    def log_callback(level: str, message: str, data: Optional[Dict[str, Any]] = None) -> None:
        if level == "error":
            output.error(message)
        elif level == "tool":
            output.debug(message, data)
        else:
            output.debug(message, data)

    # Initialize autonomous scheduler if enabled
    autonomous_scheduler: Optional[AutonomousScheduler] = None
    autonomous_enabled = args.autonomous or settings.autonomous_enabled
    if args.interactive and autonomous_enabled:
        # Create autonomous agent
        autonomous_agent = AutonomousWatchlistAgent(
            api_key=settings.gemini_api_key,
            mcp_manager=mcp_manager,
            model_name=settings.gemini_model,
            max_tokens=settings.autonomous_max_tokens,
            min_volume_usd=settings.autonomous_min_volume_usd,
            min_liquidity_usd=settings.autonomous_min_liquidity_usd,
            verbose=args.verbose,
            log_callback=log_callback if args.verbose else None,
        )

        # Calculate interval in seconds
        interval_mins = args.autonomous_interval or settings.autonomous_interval_mins
        interval_seconds = interval_mins * 60

        autonomous_scheduler = AutonomousScheduler(
            agent=autonomous_agent,
            db=watchlist_db,
            telegram=telegram,
            interval_seconds=interval_seconds,
            max_tokens=settings.autonomous_max_tokens,
            verbose=args.verbose,
            log_callback=log_callback if args.verbose else None,
        )

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
            await run_interactive(
                planner, output, watchlist_db, mcp_manager,
                poller, telegram, autonomous_scheduler
            )
        elif query:
            await run_single_query(planner, query, output, context={})
    except KeyboardInterrupt:
        output.info("\nInterrupted")
    finally:
        output.status("Shutting down...")
        await watchlist_db.close()
        await mcp_manager.shutdown()


def main() -> None:
    """Synchronous wrapper for CLI entry."""
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
