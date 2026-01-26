"""Background polling service for watchlist price alerts."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

from app.watchlist import WatchlistDB, WatchlistEntry, AlertRecord

if TYPE_CHECKING:
    from app.mcp_client import MCPManager
    from app.price_cache import PriceCache


@dataclass
class TriggeredAlert:
    """Represents a newly triggered alert for display."""

    symbol: str
    chain: str
    alert_type: str  # 'above' or 'below'
    threshold: float
    current_price: float
    token_address: str
    market_cap: Optional[float] = None
    liquidity: Optional[float] = None


@dataclass
class TokenPriceData:
    """Price and market cap data for a token."""

    price: float
    market_cap: Optional[float] = None
    liquidity: Optional[float] = None


# Type alias for alert callback
AlertCallback = Callable[[TriggeredAlert], None]


class WatchlistPoller:
    """Background service for monitoring watchlist prices and triggering alerts."""

    def __init__(
        self,
        db: WatchlistDB,
        mcp_manager: "MCPManager",
        poll_interval: int = 60,
        alert_callback: Optional[AlertCallback] = None,
        price_cache: Optional["PriceCache"] = None,
    ) -> None:
        self.db = db
        self.mcp_manager = mcp_manager
        self.poll_interval = poll_interval
        self.alert_callback = alert_callback
        self.price_cache = price_cache

        self._task: Optional[asyncio.Task[None]] = None
        self._running = False
        self._last_error: Optional[str] = None

    @property
    def is_running(self) -> bool:
        """Check if the poller is running."""
        return self._running and self._task is not None and not self._task.done()

    async def start(self) -> None:
        """Start the background polling task."""
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        """Stop the background polling task."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def check_now(self) -> List[TriggeredAlert]:
        """Manually trigger a price check and return any triggered alerts."""
        return await self._check_prices()

    async def _poll_loop(self) -> None:
        """Main polling loop."""
        while self._running:
            try:
                await self._check_prices()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._last_error = str(e)

            # Wait for next poll interval
            try:
                await asyncio.sleep(self.poll_interval)
            except asyncio.CancelledError:
                break

    async def _check_prices(self) -> List[TriggeredAlert]:
        """Check prices for all watchlist entries and trigger alerts."""
        entries = await self.db.list_entries()
        if not entries:
            return []

        # Group entries by chain for efficient batching
        by_chain: Dict[str, List[WatchlistEntry]] = {}
        for entry in entries:
            by_chain.setdefault(entry.chain, []).append(entry)

        triggered_alerts: List[TriggeredAlert] = []

        # Fetch prices for each chain
        for chain, chain_entries in by_chain.items():
            try:
                price_data_map = await self._fetch_prices(chain, chain_entries)
                
                for entry in chain_entries:
                    price_data = price_data_map.get(entry.token_address)
                    if price_data is None:
                        continue

                    # Update last price
                    await self.db.update_price(entry.id, price_data.price)

                    # Check thresholds
                    alerts = await self._check_thresholds(
                        entry, price_data.price, price_data.market_cap, price_data.liquidity
                    )
                    triggered_alerts.extend(alerts)

            except Exception:
                # Skip this chain on error, continue with others
                continue

        return triggered_alerts

    async def _fetch_prices(
        self, chain: str, entries: List[WatchlistEntry]
    ) -> Dict[str, TokenPriceData]:
        """Fetch current prices and market caps for a list of tokens on a chain.
        
        Uses price cache to avoid redundant API calls when data is still fresh.
        """
        prices: Dict[str, TokenPriceData] = {}

        # Try DexScreener first for price data
        dexscreener = self.mcp_manager.get_client("dexscreener")
        if dexscreener:
            for entry in entries:
                # Check cache first
                if self.price_cache:
                    cached = await self.price_cache.get(chain, entry.token_address)
                    if cached is not None:
                        price_data = self._extract_price_from_dexscreener(cached)
                        if price_data is not None:
                            prices[entry.token_address] = price_data
                            continue

                try:
                    result = await dexscreener.call_tool(
                        "getTokenPools",
                        {"chainId": chain, "tokenAddress": entry.token_address},
                    )
                    # Cache the result
                    if self.price_cache and result:
                        await self.price_cache.set(chain, entry.token_address, result)
                    
                    price_data = self._extract_price_from_dexscreener(result)
                    if price_data is not None:
                        prices[entry.token_address] = price_data
                except Exception:
                    continue

        # Fallback to DexPaprika for any missing prices
        dexpaprika = self.mcp_manager.get_client("dexpaprika")
        if dexpaprika:
            for entry in entries:
                if entry.token_address in prices:
                    continue
                try:
                    result = await dexpaprika.call_tool(
                        "getTokenDetails",
                        {"network": chain, "tokenAddress": entry.token_address},
                    )
                    price = self._extract_price_from_dexpaprika(result)
                    if price is not None:
                        prices[entry.token_address] = TokenPriceData(price=price)
                except Exception:
                    continue

        return prices

    def _extract_price_from_dexscreener(self, result: Any) -> Optional[TokenPriceData]:
        """Extract price and market cap from DexScreener response."""
        # Handle list response (direct array of pairs)
        if isinstance(result, list):
            pairs = result
        elif isinstance(result, dict):
            pairs = result.get("pairs", [])
        else:
            return None

        if not pairs:
            return None

        # Get price from first pair (highest liquidity usually first)
        first_pair = pairs[0]
        price_usd = first_pair.get("priceUsd")
        if not price_usd:
            return None

        try:
            price = float(price_usd)
        except (ValueError, TypeError):
            return None

        # Extract market cap (prefer marketCap, fallback to fdv)
        market_cap: Optional[float] = None
        mcap_value = first_pair.get("marketCap") or first_pair.get("fdv")
        if mcap_value:
            try:
                market_cap = float(mcap_value)
            except (ValueError, TypeError):
                pass

        # Extract liquidity
        liquidity: Optional[float] = None
        liq_data = first_pair.get("liquidity")
        if isinstance(liq_data, dict):
            liq_usd = liq_data.get("usd")
            if liq_usd:
                try:
                    liquidity = float(liq_usd)
                except (ValueError, TypeError):
                    pass

        return TokenPriceData(price=price, market_cap=market_cap, liquidity=liquidity)

    def _extract_price_from_dexpaprika(self, result: Any) -> Optional[float]:
        """Extract price from DexPaprika response."""
        if not isinstance(result, dict):
            return None

        price_usd = result.get("price_usd")
        if price_usd:
            try:
                return float(price_usd)
            except (ValueError, TypeError):
                pass

        return None

    async def _check_thresholds(
        self,
        entry: WatchlistEntry,
        current_price: float,
        market_cap: Optional[float] = None,
        liquidity: Optional[float] = None,
    ) -> List[TriggeredAlert]:
        """Check if price crossed any alert thresholds."""
        alerts: List[TriggeredAlert] = []

        # Check alert_above threshold
        if entry.alert_above is not None and current_price >= entry.alert_above:
            # Only trigger if this is a new crossing (last_price was below)
            if entry.last_price is None or entry.last_price < entry.alert_above:
                alert = TriggeredAlert(
                    symbol=entry.symbol,
                    chain=entry.chain,
                    alert_type="above",
                    threshold=entry.alert_above,
                    current_price=current_price,
                    token_address=entry.token_address,
                    market_cap=market_cap,
                    liquidity=liquidity,
                )
                alerts.append(alert)

                # Record in database
                await self.db.record_alert(
                    entry_id=entry.id,
                    alert_type="above",
                    threshold=entry.alert_above,
                    triggered_price=current_price,
                )

                # Notify via callback
                if self.alert_callback:
                    self.alert_callback(alert)

        # Check alert_below threshold
        if entry.alert_below is not None and current_price <= entry.alert_below:
            # Only trigger if this is a new crossing (last_price was above)
            if entry.last_price is None or entry.last_price > entry.alert_below:
                alert = TriggeredAlert(
                    symbol=entry.symbol,
                    chain=entry.chain,
                    alert_type="below",
                    threshold=entry.alert_below,
                    current_price=current_price,
                    token_address=entry.token_address,
                    market_cap=market_cap,
                    liquidity=liquidity,
                )
                alerts.append(alert)

                # Record in database
                await self.db.record_alert(
                    entry_id=entry.id,
                    alert_type="below",
                    threshold=entry.alert_below,
                    triggered_price=current_price,
                )

                # Notify via callback
                if self.alert_callback:
                    self.alert_callback(alert)

        return alerts
