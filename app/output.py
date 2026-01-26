"""CLI output formatting with table support."""

from __future__ import annotations

import json
import re
import sys
from enum import Enum
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from app.types import PlannerResult

if TYPE_CHECKING:
    from app.watchlist import WatchlistEntry, AlertRecord
    from app.watchlist_poller import TriggeredAlert


class OutputFormat(Enum):
    """Supported output formats."""

    TEXT = "text"
    JSON = "json"
    TABLE = "table"


class CLIOutput:
    """Unified output handler for CLI."""

    def __init__(
        self,
        format: OutputFormat = OutputFormat.TABLE,
        verbose: bool = False,
        stream: Any = None,
    ) -> None:
        self.format = format
        self.verbose = verbose
        self.stream = stream or sys.stdout
        self._console: Optional[Any] = None

        # Initialize rich console for table output
        if format == OutputFormat.TABLE:
            try:
                from rich.console import Console
                self._console = Console()
            except ImportError:
                self.format = OutputFormat.TEXT
                print("⚠️  'rich' library not installed, using text output", file=sys.stderr)

    def result(self, result: PlannerResult) -> None:
        """Output a planner result."""
        if self.format == OutputFormat.JSON:
            self._json_result(result)
        elif self.format == OutputFormat.TABLE:
            self._table_result(result)
        else:
            self._text_result(result)

    def _text_result(self, result: PlannerResult) -> None:
        """Plain text output."""
        message = self._strip_markdown(result.message)
        print(message, file=self.stream)

    def _json_result(self, result: PlannerResult) -> None:
        """JSON output for scripting."""
        output = {
            "message": self._strip_markdown(result.message),
            "tokens": result.tokens,
        }
        print(json.dumps(output, indent=2), file=self.stream)

    def _table_result(self, result: PlannerResult) -> None:
        """Rich terminal output with tables."""
        if not self._console:
            self._text_result(result)
            return

        from rich.markdown import Markdown

        message = result.message

        # Check if message contains Markdown tables
        if "|" in message and "---" in message:
            self._render_with_tables(message)
        else:
            self._console.print(Markdown(message))

        # Show discovered tokens in verbose mode
        if result.tokens and self.verbose:
            self._show_token_context(result.tokens)

    def _render_with_tables(self, message: str) -> None:
        """Parse and render message containing Markdown tables."""
        from rich.markdown import Markdown
        from rich.table import Table

        lines = message.split("\n")
        buffer: List[str] = []
        i = 0

        while i < len(lines):
            line = lines[i]

            # Detect start of a table
            if "|" in line and i + 1 < len(lines) and "---" in lines[i + 1]:
                # Flush buffered text
                if buffer:
                    text = "\n".join(buffer).strip()
                    if text:
                        self._console.print(Markdown(text))
                    buffer = []

                # Parse the table
                table_lines = [line]
                i += 1
                while i < len(lines) and "|" in lines[i]:
                    table_lines.append(lines[i])
                    i += 1

                # Render the table
                rich_table = self._parse_markdown_table(table_lines)
                if rich_table:
                    self._console.print(rich_table)
            else:
                buffer.append(line)
                i += 1

        # Flush remaining text
        if buffer:
            text = "\n".join(buffer).strip()
            if text:
                self._console.print(Markdown(text))

    def _parse_markdown_table(self, lines: List[str]) -> Optional[Any]:
        """Convert Markdown table lines to rich Table."""
        from rich.table import Table

        if len(lines) < 2:
            return None

        # Parse header
        header_line = lines[0]
        headers = [self._clean_cell_content(h) for h in header_line.split("|") if h.strip()]

        if not headers:
            return None

        table = Table(show_header=True, header_style="bold cyan", expand=False)
        for header in headers:
            table.add_column(header, overflow="fold")

        # Parse data rows (skip separator line)
        for line in lines[2:]:
            if "---" in line:
                continue
            cells = [self._clean_cell_content(c) for c in line.split("|") if c.strip()]
            if cells:
                # Pad cells if needed
                while len(cells) < len(headers):
                    cells.append("")
                table.add_row(*cells[:len(headers)])

        return table

    def _show_token_context(self, tokens: List[Dict[str, str]]) -> None:
        """Display discovered tokens."""
        from rich.table import Table

        table = Table(title="Discovered Tokens")
        table.add_column("Symbol", style="cyan")
        table.add_column("Address", style="dim", no_wrap=True)
        table.add_column("Chain", style="magenta")

        for token in tokens[:10]:
            table.add_row(
                token.get("symbol", "?"),
                token.get("address", "?"),
                token.get("chainId", "?"),
            )

        self._console.print(table)

    def status(self, message: str) -> None:
        """Output a status message."""
        if self.format == OutputFormat.JSON:
            return
        if self._console:
            self._console.print(f"[dim]⏳ {message}[/dim]")
        else:
            print(f"⏳ {message}", file=self.stream)

    def info(self, message: str) -> None:
        """Output an info message."""
        if self.format == OutputFormat.JSON:
            return
        if self._console:
            self._console.print(f"[blue]ℹ️  {message}[/blue]")
        else:
            print(f"ℹ️  {message}", file=self.stream)

    def debug(self, message: str, data: Optional[Dict[str, Any]] = None) -> None:
        """Output a debug message (only in verbose mode)."""
        if not self.verbose:
            return
        if self.format == OutputFormat.JSON:
            output = {"debug": message}
            if data:
                output["data"] = data
            print(json.dumps(output), file=sys.stderr)
            return
        if self._console:
            self._console.print(f"[dim]🔍 {message}[/dim]")
            if data:
                # Pretty print data
                data_str = json.dumps(data, indent=2, default=str)
                if len(data_str) < 200:
                    self._console.print(f"[dim]   {data_str}[/dim]")
        else:
            print(f"🔍 {message}", file=sys.stderr)
            if data:
                print(f"   {data}", file=sys.stderr)

    def warning(self, message: str) -> None:
        """Output a warning message."""
        if self.format == OutputFormat.JSON:
            print(json.dumps({"warning": message}), file=sys.stderr)
            return
        if self._console:
            self._console.print(f"[yellow]⚠️  {message}[/yellow]")
        else:
            print(f"⚠️  {message}", file=sys.stderr)

    def error(self, message: str) -> None:
        """Output an error message."""
        if self.format == OutputFormat.JSON:
            print(json.dumps({"error": message}), file=sys.stderr)
            return
        if self._console:
            self._console.print(f"[red]❌ {message}[/red]")
        else:
            print(f"❌ {message}", file=sys.stderr)

    @staticmethod
    def _strip_markdown(text: str) -> str:
        """Remove Markdown formatting for plain text output."""
        if not text:
            return ""

        result = text

        # Remove backslash escapes
        escape_chars = r"\_*[]()~`>#+-=|{}.!$"
        for char in escape_chars:
            result = result.replace(f"\\{char}", char)

        # Convert links: [text](url) -> text (url)
        result = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", result)

        return result

    @staticmethod
    def _clean_cell_content(text: str) -> str:
        """Clean HTML tags and normalize markdown in table cell content."""
        if not text:
            return ""

        result = text.strip()

        # Replace <br> tags with space
        result = re.sub(r"<br\s*/?>", " ", result, flags=re.IGNORECASE)

        # Remove HTML tags like <strong>, </strong>, etc.
        result = re.sub(r"</?[a-zA-Z][^>]*>", "", result)

        # Convert **bold** to plain text (strip the markers)
        result = re.sub(r"\*\*([^*]+)\*\*", r"\1", result)

        # Clean up multiple spaces
        result = re.sub(r" +", " ", result)

        return result.strip()

    # --- Watchlist Output Methods ---

    def watchlist_table(self, entries: List["WatchlistEntry"]) -> None:
        """Display watchlist entries in a table."""
        if not entries:
            self.info("Watchlist is empty")
            return

        if self.format == OutputFormat.JSON:
            output = [
                {
                    "symbol": e.symbol,
                    "address": e.token_address,
                    "chain": e.chain,
                    "last_price": e.last_price,
                    "alert_above": e.alert_above,
                    "alert_below": e.alert_below,
                }
                for e in entries
            ]
            print(json.dumps(output, indent=2), file=self.stream)
            return

        if not self._console:
            # Plain text fallback
            for e in entries:
                price = f"${e.last_price:.8f}" if e.last_price else "-"
                above = f"${e.alert_above:.8f}" if e.alert_above else "-"
                below = f"${e.alert_below:.8f}" if e.alert_below else "-"
                print(f"{e.symbol} ({e.chain}): {price} | Above: {above} | Below: {below}")
            return

        from rich.table import Table

        table = Table(title="📋 Watchlist", show_header=True, header_style="bold cyan")
        table.add_column("Symbol", style="cyan", no_wrap=True)
        table.add_column("Address", style="dim", no_wrap=True)
        table.add_column("Chain", style="magenta")
        table.add_column("Last Price", style="green", justify="right")
        table.add_column("Alert Above", justify="right")
        table.add_column("Alert Below", justify="right")

        for entry in entries:
            addr_short = entry.token_address[:10] + "..." if len(entry.token_address) > 10 else entry.token_address
            price = self._format_price(entry.last_price) if entry.last_price else "-"
            above = self._format_price(entry.alert_above) if entry.alert_above else "-"
            below = self._format_price(entry.alert_below) if entry.alert_below else "-"

            table.add_row(
                entry.symbol,
                addr_short,
                entry.chain,
                price,
                above,
                below,
            )

        self._console.print(table)

    def alert_notification(self, alert: "TriggeredAlert") -> None:
        """Display a triggered alert notification."""
        if alert.alert_type == "above":
            emoji = "🔺"
            direction = "crossed above"
            color = "green"
        else:
            emoji = "🔻"
            direction = "dropped below"
            color = "red"

        threshold = self._format_price(alert.threshold)
        current = self._format_price(alert.current_price)

        if self.format == OutputFormat.JSON:
            output = {
                "alert": True,
                "symbol": alert.symbol,
                "chain": alert.chain,
                "type": alert.alert_type,
                "threshold": alert.threshold,
                "current_price": alert.current_price,
            }
            print(json.dumps(output), file=self.stream)
            return

        message = f"{emoji} ALERT: {alert.symbol} ({alert.chain}) {direction} {threshold} (current: {current})"

        if self._console:
            self._console.print(f"[bold {color}]{message}[/bold {color}]")
        else:
            print(f"🔔 {message}", file=self.stream)

    def alerts_table(self, alerts: List["AlertRecord"]) -> None:
        """Display alert history in a table."""
        if not alerts:
            self.info("No alerts")
            return

        if self.format == OutputFormat.JSON:
            output = [
                {
                    "symbol": a.symbol,
                    "chain": a.chain,
                    "type": a.alert_type,
                    "threshold": a.threshold,
                    "triggered_price": a.triggered_price,
                    "triggered_at": a.triggered_at.isoformat(),
                    "acknowledged": a.acknowledged,
                }
                for a in alerts
            ]
            print(json.dumps(output, indent=2), file=self.stream)
            return

        if not self._console:
            for a in alerts:
                status = "✓" if a.acknowledged else "•"
                print(f"{status} {a.symbol}: {a.alert_type} {a.threshold} @ {a.triggered_price}")
            return

        from rich.table import Table

        table = Table(title="🔔 Alerts", show_header=True, header_style="bold cyan")
        table.add_column("Symbol", style="cyan")
        table.add_column("Chain", style="magenta")
        table.add_column("Type")
        table.add_column("Threshold", justify="right")
        table.add_column("Triggered At", justify="right")
        table.add_column("Time", style="dim")

        for alert in alerts:
            alert_type = "🔺 above" if alert.alert_type == "above" else "🔻 below"
            threshold = self._format_price(alert.threshold)
            triggered = self._format_price(alert.triggered_price)
            time_str = alert.triggered_at.strftime("%Y-%m-%d %H:%M")

            table.add_row(
                alert.symbol or "?",
                alert.chain or "?",
                alert_type,
                threshold,
                triggered,
                time_str,
            )

        self._console.print(table)

    def help_panel(self, version: str = "0.1.0") -> None:
        """Display help information panel."""
        if self.format == OutputFormat.JSON:
            output = {
                "name": "DEX Agentic Bot",
                "version": version,
                "commands": {
                    "/quit, /q": "Exit the CLI",
                    "/clear": "Clear conversation context",
                    "/context": "Show recent tokens in context",
                    "/watch <token> [chain]": "Add token to watchlist",
                    "/unwatch <token>": "Remove token from watchlist",
                    "/watchlist": "Show watched tokens with prices",
                    "/clearwatchlist": "Remove all tokens from watchlist",
                    "/alert <token> above|below <price>": "Set price alert",
                    "/alerts": "Show triggered alerts",
                    "/alerts clear": "Acknowledge all alerts",
                    "/alerts history": "Show alert history",
                    "/fix-addresses": "Fix lowercase Solana addresses",
                    "/help": "Show this help",
                },
            }
            print(json.dumps(output, indent=2), file=self.stream)
            return

        if not self._console:
            # Plain text fallback
            print(f"\n🤖 DEX Agentic Bot v{version}")
            print("Blockchain-agnostic token & pool info across DEXs\n")
            print("Commands:")
            print("  /quit, /q         Exit the CLI")
            print("  /clear            Clear conversation context")
            print("  /context          Show recent tokens in context")
            print("\nWatchlist:")
            print("  /watch <token> [chain]       Add token to watchlist")
            print("  /unwatch <token>             Remove from watchlist")
            print("  /watchlist                   Show watched tokens")
            print("  /clearwatchlist              Clear entire watchlist")
            print("  /alert <token> above|below <price>  Set price alert")
            print("  /alerts                      Show triggered alerts")
            print("  /alerts clear                Acknowledge alerts")
            print("  /alerts history              Show alert history")
            print("  /fix-addresses               Fix lowercase Solana addresses")
            print("\n  /help             Show this help")
            print("\nExamples:")
            print("  > search for PEPE on ethereum")
            print("  > /watch PEPE ethereum")
            print("  > /alert PEPE above 0.00002")
            return

        from rich.panel import Panel
        from rich.text import Text
        from rich.table import Table
        from rich.console import Group

        # Header
        header = Text()
        header.append(f"🤖 DEX Agentic Bot v{version}\n", style="bold cyan")
        header.append("Blockchain-agnostic token & pool info across DEXs", style="dim")

        # Commands table
        cmd_table = Table(show_header=False, box=None, padding=(0, 2))
        cmd_table.add_column("Command", style="green")
        cmd_table.add_column("Description")

        cmd_table.add_row("", "")
        cmd_table.add_row("[bold]General[/bold]", "")
        cmd_table.add_row("  /quit, /q", "Exit the CLI")
        cmd_table.add_row("  /clear", "Clear conversation context")
        cmd_table.add_row("  /context", "Show recent tokens in context")
        cmd_table.add_row("", "")
        cmd_table.add_row("[bold]Watchlist[/bold]", "")
        cmd_table.add_row("  /watch <token> [chain]", "Add token to watchlist")
        cmd_table.add_row("  /unwatch <token>", "Remove from watchlist")
        cmd_table.add_row("  /watchlist", "Show watched tokens with prices")
        cmd_table.add_row("  /clearwatchlist", "Clear entire watchlist")
        cmd_table.add_row("  /alert <token> above|below <price>", "Set price alert")
        cmd_table.add_row("  /alerts", "Show triggered alerts")
        cmd_table.add_row("  /alerts clear", "Acknowledge all alerts")
        cmd_table.add_row("  /alerts history", "Show alert history")
        cmd_table.add_row("  /fix-addresses", "Fix lowercase Solana addresses")
        cmd_table.add_row("", "")
        cmd_table.add_row("  /help", "Show this help")

        # Examples
        examples = Text()
        examples.append("\n📝 Examples:\n", style="bold")
        examples.append("  > search for PEPE on ethereum\n", style="dim italic")
        examples.append("  > /watch PEPE ethereum\n", style="dim italic")
        examples.append("  > /alert PEPE above 0.00002", style="dim italic")

        # Combine into panel
        content = Group(header, cmd_table, examples)
        panel = Panel(content, border_style="cyan", padding=(1, 2))
        self._console.print(panel)

    @staticmethod
    def _format_price(price: float) -> str:
        """Format price with appropriate precision."""
        if price >= 1:
            return f"${price:,.4f}"
        elif price >= 0.0001:
            return f"${price:.6f}"
        else:
            return f"${price:.10f}"
