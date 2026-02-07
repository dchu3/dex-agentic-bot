"""Watchlist and alerts persistence with SQLite."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional

import aiosqlite


def _normalize_symbol(symbol: str) -> str:
    """Strip emoji/special character prefixes from symbols."""
    return re.sub(r'^[^\w]+', '', symbol).upper()

DEFAULT_DB_PATH = Path.home() / ".dex-bot" / "watchlist.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS watchlist (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_address TEXT NOT NULL,
    symbol TEXT NOT NULL,
    chain TEXT NOT NULL,
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    alert_above REAL,
    alert_below REAL,
    last_price REAL,
    last_checked TIMESTAMP,
    autonomous_managed INTEGER DEFAULT 0,
    momentum_score REAL,
    last_reviewed TIMESTAMP,
    review_notes TEXT,
    UNIQUE(token_address, chain)
);

CREATE TABLE IF NOT EXISTS alert_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id INTEGER NOT NULL,
    alert_type TEXT NOT NULL,
    threshold REAL NOT NULL,
    triggered_price REAL NOT NULL,
    triggered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    acknowledged INTEGER DEFAULT 0,
    FOREIGN KEY (entry_id) REFERENCES watchlist(id) ON DELETE CASCADE
);
"""

MIGRATION_V2 = """
ALTER TABLE watchlist ADD COLUMN autonomous_managed INTEGER DEFAULT 0;
ALTER TABLE watchlist ADD COLUMN momentum_score REAL;
ALTER TABLE watchlist ADD COLUMN last_reviewed TIMESTAMP;
ALTER TABLE watchlist ADD COLUMN review_notes TEXT;
"""


@dataclass
class WatchlistEntry:
    """Represents a token in the watchlist."""

    id: int
    token_address: str
    symbol: str
    chain: str
    added_at: datetime
    alert_above: Optional[float] = None
    alert_below: Optional[float] = None
    last_price: Optional[float] = None
    last_checked: Optional[datetime] = None
    autonomous_managed: bool = False
    momentum_score: Optional[float] = None
    last_reviewed: Optional[datetime] = None
    review_notes: Optional[str] = None


@dataclass
class AlertRecord:
    """Represents a triggered alert."""

    id: int
    entry_id: int
    alert_type: str  # 'above' or 'below'
    threshold: float
    triggered_price: float
    triggered_at: datetime
    acknowledged: bool = False
    # Joined fields
    symbol: Optional[str] = None
    chain: Optional[str] = None


class WatchlistDB:
    """Async SQLite manager for watchlist and alerts."""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = db_path or DEFAULT_DB_PATH
        self._connection: Optional[aiosqlite.Connection] = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        """Initialize database connection and schema."""
        # Ensure directory exists
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._connection = await aiosqlite.connect(self.db_path)
        self._connection.row_factory = aiosqlite.Row

        # Enable foreign keys
        await self._connection.execute("PRAGMA foreign_keys = ON")

        # Create tables
        await self._connection.executescript(SCHEMA)
        await self._connection.commit()
        
        # Run migrations for existing databases
        await self._run_migrations()

    async def close(self) -> None:
        """Close database connection."""
        if self._connection:
            await self._connection.close()
            self._connection = None

    async def _run_migrations(self) -> None:
        """Run database migrations for schema updates."""
        conn = await self._ensure_connected()
        
        # Check if autonomous_managed column exists
        cursor = await conn.execute("PRAGMA table_info(watchlist)")
        columns = {row[1] for row in await cursor.fetchall()}
        
        if "autonomous_managed" not in columns:
            # Run migration V2
            for statement in MIGRATION_V2.strip().split(";"):
                statement = statement.strip()
                if statement:
                    try:
                        await conn.execute(statement)
                    except Exception:
                        pass  # Column may already exist
            await conn.commit()

    async def _ensure_connected(self) -> aiosqlite.Connection:
        """Ensure database is connected."""
        if not self._connection:
            await self.connect()
        return self._connection  # type: ignore

    # --- Watchlist Operations ---

    async def add_entry(
        self,
        token_address: str,
        symbol: str,
        chain: str,
        alert_above: Optional[float] = None,
        alert_below: Optional[float] = None,
    ) -> WatchlistEntry:
        """Add a token to the watchlist.
        
        Token addresses are stored with original case (important for Solana base58 addresses).
        Lookups use case-insensitive comparison.
        """
        conn = await self._ensure_connected()
        chain_lower = chain.lower()
        async with self._lock:
            # Check if entry exists (case-insensitive)
            cursor = await conn.execute(
                "SELECT id FROM watchlist WHERE LOWER(token_address) = LOWER(?) AND chain = ?",
                (token_address, chain_lower),
            )
            existing = await cursor.fetchone()
            
            if existing:
                # Update existing entry
                cursor = await conn.execute(
                    """
                    UPDATE watchlist SET
                        symbol = ?,
                        alert_above = COALESCE(?, alert_above),
                        alert_below = COALESCE(?, alert_below)
                    WHERE id = ?
                    RETURNING *
                    """,
                    (_normalize_symbol(symbol), alert_above, alert_below, existing[0]),
                )
            else:
                # Insert new entry with original case
                cursor = await conn.execute(
                    """
                    INSERT INTO watchlist (token_address, symbol, chain, alert_above, alert_below)
                    VALUES (?, ?, ?, ?, ?)
                    RETURNING *
                    """,
                    (token_address, _normalize_symbol(symbol), chain_lower, alert_above, alert_below),
                )
            row = await cursor.fetchone()
            await conn.commit()
            return self._row_to_entry(row)

    async def remove_entry(self, token_address: str, chain: Optional[str] = None) -> bool:
        """Remove a token from the watchlist (case-insensitive lookup)."""
        conn = await self._ensure_connected()
        async with self._lock:
            if chain:
                cursor = await conn.execute(
                    "DELETE FROM watchlist WHERE LOWER(token_address) = LOWER(?) AND chain = ?",
                    (token_address, chain.lower()),
                )
            else:
                cursor = await conn.execute(
                    "DELETE FROM watchlist WHERE LOWER(token_address) = LOWER(?)",
                    (token_address,),
                )
            await conn.commit()
            return cursor.rowcount > 0

    async def remove_entry_by_symbol(self, symbol: str, chain: Optional[str] = None) -> bool:
        """Remove a token from the watchlist by symbol."""
        conn = await self._ensure_connected()
        normalized = _normalize_symbol(symbol)
        async with self._lock:
            if chain:
                cursor = await conn.execute(
                    "DELETE FROM watchlist WHERE symbol = ? AND chain = ?",
                    (normalized, chain.lower()),
                )
            else:
                cursor = await conn.execute(
                    "DELETE FROM watchlist WHERE symbol = ?",
                    (normalized,),
                )
            await conn.commit()
            return cursor.rowcount > 0

    async def get_entry(
        self, token_address: Optional[str] = None, symbol: Optional[str] = None, chain: Optional[str] = None
    ) -> Optional[WatchlistEntry]:
        """Get a single watchlist entry by address or symbol (case-insensitive lookup)."""
        conn = await self._ensure_connected()

        if token_address:
            if chain:
                cursor = await conn.execute(
                    "SELECT * FROM watchlist WHERE LOWER(token_address) = LOWER(?) AND chain = ?",
                    (token_address, chain.lower()),
                )
            else:
                cursor = await conn.execute(
                    "SELECT * FROM watchlist WHERE LOWER(token_address) = LOWER(?)",
                    (token_address,),
                )
        elif symbol:
            normalized = _normalize_symbol(symbol)
            if chain:
                cursor = await conn.execute(
                    "SELECT * FROM watchlist WHERE symbol = ? AND chain = ?",
                    (normalized, chain.lower()),
                )
            else:
                cursor = await conn.execute(
                    "SELECT * FROM watchlist WHERE symbol = ?",
                    (normalized,),
                )
        else:
            return None

        row = await cursor.fetchone()
        return self._row_to_entry(row) if row else None

    async def list_entries(self) -> List[WatchlistEntry]:
        """Get all watchlist entries."""
        conn = await self._ensure_connected()
        cursor = await conn.execute("SELECT * FROM watchlist ORDER BY added_at DESC")
        rows = await cursor.fetchall()
        return [self._row_to_entry(row) for row in rows]

    async def update_alert(
        self,
        entry_id: int,
        alert_above: Optional[float] = None,
        alert_below: Optional[float] = None,
        clear_above: bool = False,
        clear_below: bool = False,
    ) -> bool:
        """Update alert thresholds for a watchlist entry."""
        conn = await self._ensure_connected()
        async with self._lock:
            updates = []
            params: List[Any] = []

            if alert_above is not None:
                updates.append("alert_above = ?")
                params.append(alert_above)
            elif clear_above:
                updates.append("alert_above = NULL")

            if alert_below is not None:
                updates.append("alert_below = ?")
                params.append(alert_below)
            elif clear_below:
                updates.append("alert_below = NULL")

            if not updates:
                return False

            params.append(entry_id)
            cursor = await conn.execute(
                f"UPDATE watchlist SET {', '.join(updates)} WHERE id = ?",
                params,
            )
            await conn.commit()
            return cursor.rowcount > 0

    async def update_price(
        self, entry_id: int, price: float, checked_at: Optional[datetime] = None
    ) -> None:
        """Update the last known price for a watchlist entry."""
        conn = await self._ensure_connected()
        checked_at = checked_at or datetime.now(timezone.utc)
        async with self._lock:
            await conn.execute(
                "UPDATE watchlist SET last_price = ?, last_checked = ? WHERE id = ?",
                (price, checked_at, entry_id),
            )
            await conn.commit()

    async def update_token_address(self, entry_id: int, new_address: str) -> bool:
        """Update the token address for an entry (e.g., to fix case).
        
        Args:
            entry_id: The ID of the entry to update
            new_address: The new address with correct case
            
        Returns:
            True if the update was successful
        """
        conn = await self._ensure_connected()
        async with self._lock:
            cursor = await conn.execute(
                "UPDATE watchlist SET token_address = ? WHERE id = ?",
                (new_address, entry_id),
            )
            await conn.commit()
            return cursor.rowcount > 0

    async def clear_watchlist(self) -> int:
        """Remove all entries from the watchlist."""
        conn = await self._ensure_connected()
        async with self._lock:
            cursor = await conn.execute("DELETE FROM watchlist")
            await conn.commit()
            return cursor.rowcount

    # --- Autonomous Management Operations ---

    async def add_autonomous_entry(
        self,
        token_address: str,
        symbol: str,
        chain: str,
        alert_above: Optional[float] = None,
        alert_below: Optional[float] = None,
        momentum_score: Optional[float] = None,
        review_notes: Optional[str] = None,
    ) -> WatchlistEntry:
        """Add a token to the watchlist as autonomously managed.
        
        Token addresses are stored with original case (important for Solana base58 addresses).
        Lookups use case-insensitive comparison.
        """
        conn = await self._ensure_connected()
        now = datetime.now(timezone.utc)
        chain_lower = chain.lower()
        async with self._lock:
            # Check if entry exists (case-insensitive)
            cursor = await conn.execute(
                "SELECT id FROM watchlist WHERE LOWER(token_address) = LOWER(?) AND chain = ?",
                (token_address, chain_lower),
            )
            existing = await cursor.fetchone()
            
            if existing:
                # Update existing entry
                cursor = await conn.execute(
                    """
                    UPDATE watchlist SET
                        symbol = ?,
                        alert_above = COALESCE(?, alert_above),
                        alert_below = COALESCE(?, alert_below),
                        autonomous_managed = 1,
                        momentum_score = ?,
                        last_reviewed = ?,
                        review_notes = ?
                    WHERE id = ?
                    RETURNING *
                    """,
                    (
                        _normalize_symbol(symbol),
                        alert_above,
                        alert_below,
                        momentum_score,
                        now,
                        review_notes,
                        existing[0],
                    ),
                )
            else:
                # Insert new entry with original case
                cursor = await conn.execute(
                    """
                    INSERT INTO watchlist (
                        token_address, symbol, chain, alert_above, alert_below,
                        autonomous_managed, momentum_score, last_reviewed, review_notes
                    )
                    VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
                    RETURNING *
                    """,
                    (
                        token_address,
                        _normalize_symbol(symbol),
                        chain_lower,
                        alert_above,
                        alert_below,
                        momentum_score,
                        now,
                        review_notes,
                    ),
                )
            row = await cursor.fetchone()
            await conn.commit()
            return self._row_to_entry(row)

    async def list_autonomous_entries(self) -> List[WatchlistEntry]:
        """Get all autonomously managed watchlist entries."""
        conn = await self._ensure_connected()
        cursor = await conn.execute(
            "SELECT * FROM watchlist WHERE autonomous_managed = 1 ORDER BY momentum_score DESC"
        )
        rows = await cursor.fetchall()
        return [self._row_to_entry(row) for row in rows]

    async def count_autonomous_entries(self) -> int:
        """Count autonomously managed entries."""
        conn = await self._ensure_connected()
        cursor = await conn.execute(
            "SELECT COUNT(*) FROM watchlist WHERE autonomous_managed = 1"
        )
        row = await cursor.fetchone()
        return row[0] if row else 0

    async def remove_autonomous_entry(self, token_address: str, chain: str) -> bool:
        """Remove an autonomously managed token from the watchlist (case-insensitive lookup)."""
        conn = await self._ensure_connected()
        async with self._lock:
            cursor = await conn.execute(
                "DELETE FROM watchlist WHERE LOWER(token_address) = LOWER(?) AND chain = ? AND autonomous_managed = 1",
                (token_address, chain.lower()),
            )
            await conn.commit()
            return cursor.rowcount > 0

    async def update_autonomous_entry(
        self,
        entry_id: int,
        alert_above: Optional[float] = None,
        alert_below: Optional[float] = None,
        momentum_score: Optional[float] = None,
        review_notes: Optional[str] = None,
    ) -> bool:
        """Update an autonomously managed entry with new data."""
        conn = await self._ensure_connected()
        now = datetime.now(timezone.utc)
        async with self._lock:
            updates = ["last_reviewed = ?"]
            params: List[Any] = [now]

            if alert_above is not None:
                updates.append("alert_above = ?")
                params.append(alert_above)
            if alert_below is not None:
                updates.append("alert_below = ?")
                params.append(alert_below)
            if momentum_score is not None:
                updates.append("momentum_score = ?")
                params.append(momentum_score)
            if review_notes is not None:
                updates.append("review_notes = ?")
                params.append(review_notes)

            params.append(entry_id)
            cursor = await conn.execute(
                f"UPDATE watchlist SET {', '.join(updates)} WHERE id = ? AND autonomous_managed = 1",
                params,
            )
            await conn.commit()
            return cursor.rowcount > 0

    async def clear_autonomous_watchlist(self) -> int:
        """Remove all autonomously managed entries from the watchlist."""
        conn = await self._ensure_connected()
        async with self._lock:
            cursor = await conn.execute(
                "DELETE FROM watchlist WHERE autonomous_managed = 1"
            )
            await conn.commit()
            return cursor.rowcount

    # --- Alert History Operations ---

    async def record_alert(
        self,
        entry_id: int,
        alert_type: str,
        threshold: float,
        triggered_price: float,
    ) -> AlertRecord:
        """Record a triggered alert."""
        conn = await self._ensure_connected()
        async with self._lock:
            cursor = await conn.execute(
                """
                INSERT INTO alert_history (entry_id, alert_type, threshold, triggered_price)
                VALUES (?, ?, ?, ?)
                RETURNING *
                """,
                (entry_id, alert_type, threshold, triggered_price),
            )
            row = await cursor.fetchone()
            await conn.commit()
            return self._row_to_alert(row)

    async def get_unacknowledged_alerts(self) -> List[AlertRecord]:
        """Get all unacknowledged alerts with token info."""
        conn = await self._ensure_connected()
        cursor = await conn.execute(
            """
            SELECT ah.*, w.symbol, w.chain
            FROM alert_history ah
            JOIN watchlist w ON ah.entry_id = w.id
            WHERE ah.acknowledged = 0
            ORDER BY ah.triggered_at DESC
            """
        )
        rows = await cursor.fetchall()
        return [self._row_to_alert(row) for row in rows]

    async def get_alert_history(self, limit: int = 50) -> List[AlertRecord]:
        """Get recent alert history with token info."""
        conn = await self._ensure_connected()
        cursor = await conn.execute(
            """
            SELECT ah.*, w.symbol, w.chain
            FROM alert_history ah
            JOIN watchlist w ON ah.entry_id = w.id
            ORDER BY ah.triggered_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = await cursor.fetchall()
        return [self._row_to_alert(row) for row in rows]

    async def acknowledge_alerts(self, alert_ids: Optional[List[int]] = None) -> int:
        """Mark alerts as acknowledged."""
        conn = await self._ensure_connected()
        async with self._lock:
            if alert_ids:
                placeholders = ",".join("?" * len(alert_ids))
                cursor = await conn.execute(
                    f"UPDATE alert_history SET acknowledged = 1 WHERE id IN ({placeholders})",
                    alert_ids,
                )
            else:
                cursor = await conn.execute(
                    "UPDATE alert_history SET acknowledged = 1 WHERE acknowledged = 0"
                )
            await conn.commit()
            return cursor.rowcount

    async def clear_alert_history(self) -> int:
        """Clear all alert history."""
        conn = await self._ensure_connected()
        async with self._lock:
            cursor = await conn.execute("DELETE FROM alert_history")
            await conn.commit()
            return cursor.rowcount

    # --- Helper Methods ---

    @staticmethod
    def _row_to_entry(row: aiosqlite.Row) -> WatchlistEntry:
        """Convert a database row to WatchlistEntry."""
        row_keys = row.keys()
        return WatchlistEntry(
            id=row["id"],
            token_address=row["token_address"],
            symbol=row["symbol"],
            chain=row["chain"],
            added_at=datetime.fromisoformat(row["added_at"]) if row["added_at"] else datetime.now(timezone.utc),
            alert_above=row["alert_above"],
            alert_below=row["alert_below"],
            last_price=row["last_price"],
            last_checked=datetime.fromisoformat(row["last_checked"]) if row["last_checked"] else None,
            autonomous_managed=bool(row["autonomous_managed"]) if "autonomous_managed" in row_keys else False,
            momentum_score=row["momentum_score"] if "momentum_score" in row_keys else None,
            last_reviewed=datetime.fromisoformat(row["last_reviewed"]) if "last_reviewed" in row_keys and row["last_reviewed"] else None,
            review_notes=row["review_notes"] if "review_notes" in row_keys else None,
        )

    @staticmethod
    def _row_to_alert(row: aiosqlite.Row) -> AlertRecord:
        """Convert a database row to AlertRecord."""
        return AlertRecord(
            id=row["id"],
            entry_id=row["entry_id"],
            alert_type=row["alert_type"],
            threshold=row["threshold"],
            triggered_price=row["triggered_price"],
            triggered_at=datetime.fromisoformat(row["triggered_at"]) if row["triggered_at"] else datetime.now(timezone.utc),
            acknowledged=bool(row["acknowledged"]),
            symbol=row["symbol"] if "symbol" in row.keys() else None,
            chain=row["chain"] if "chain" in row.keys() else None,
        )
