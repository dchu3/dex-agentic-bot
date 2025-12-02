"""CLI output formatting with table support."""

from __future__ import annotations

import json
import re
import sys
from enum import Enum
from typing import Any, Dict, List, Optional

from app.types import PlannerResult


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
        headers = [h.strip() for h in header_line.split("|") if h.strip()]

        if not headers:
            return None

        table = Table(show_header=True, header_style="bold cyan", expand=False)
        for header in headers:
            table.add_column(header, overflow="fold")

        # Parse data rows (skip separator line)
        for line in lines[2:]:
            if "---" in line:
                continue
            cells = [c.strip() for c in line.split("|") if c.strip()]
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
