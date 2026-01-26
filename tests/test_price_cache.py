"""Tests for price cache."""

import asyncio
from datetime import datetime, timedelta

import pytest
import pytest_asyncio

from app.price_cache import PriceCache, CachedPrice


@pytest_asyncio.fixture
async def cache():
    """Create a price cache with short TTL for testing."""
    return PriceCache(ttl_seconds=1)


class TestPriceCache:
    """Tests for PriceCache."""

    @pytest.mark.asyncio
    async def test_set_and_get(self, cache):
        """Test basic set and get operations."""
        await cache.set("ethereum", "0x123", {"price": 100})
        result = await cache.get("ethereum", "0x123")
        
        assert result is not None
        assert result["price"] == 100

    @pytest.mark.asyncio
    async def test_cache_miss(self, cache):
        """Test cache miss returns None."""
        result = await cache.get("ethereum", "0x999")
        assert result is None

    @pytest.mark.asyncio
    async def test_case_insensitive_keys(self, cache):
        """Test that keys are case-insensitive."""
        await cache.set("Ethereum", "0xABC", {"price": 50})
        
        # Should find with different case
        result = await cache.get("ethereum", "0xabc")
        assert result is not None
        assert result["price"] == 50

    @pytest.mark.asyncio
    async def test_ttl_expiration(self, cache):
        """Test that entries expire after TTL."""
        await cache.set("ethereum", "0x123", {"price": 100})
        
        # Should be available immediately
        result = await cache.get("ethereum", "0x123")
        assert result is not None
        
        # Wait for TTL to expire
        await asyncio.sleep(1.1)
        
        # Should be expired now
        result = await cache.get("ethereum", "0x123")
        assert result is None

    @pytest.mark.asyncio
    async def test_update_existing_entry(self, cache):
        """Test updating an existing cache entry."""
        await cache.set("ethereum", "0x123", {"price": 100})
        await cache.set("ethereum", "0x123", {"price": 200})
        
        result = await cache.get("ethereum", "0x123")
        assert result["price"] == 200

    @pytest.mark.asyncio
    async def test_clear(self, cache):
        """Test clearing all cache entries."""
        await cache.set("ethereum", "0x111", {"price": 1})
        await cache.set("ethereum", "0x222", {"price": 2})
        await cache.set("solana", "0x333", {"price": 3})
        
        count = await cache.clear()
        assert count == 3
        
        # All should be gone
        assert await cache.get("ethereum", "0x111") is None
        assert await cache.get("ethereum", "0x222") is None
        assert await cache.get("solana", "0x333") is None

    @pytest.mark.asyncio
    async def test_cleanup_expired(self, cache):
        """Test cleanup of expired entries."""
        await cache.set("ethereum", "0x111", {"price": 1})
        await asyncio.sleep(1.1)  # Let it expire
        
        # Add a fresh entry
        await cache.set("ethereum", "0x222", {"price": 2})
        
        # Cleanup should remove only the expired one
        removed = await cache.cleanup_expired()
        assert removed == 1
        
        # Fresh entry should still be there
        result = await cache.get("ethereum", "0x222")
        assert result is not None

    @pytest.mark.asyncio
    async def test_stats(self, cache):
        """Test cache statistics."""
        # Initial state
        stats = cache.stats
        assert stats["size"] == 0
        assert stats["hits"] == 0
        assert stats["misses"] == 0
        
        # Miss
        await cache.get("ethereum", "0x123")
        assert cache.stats["misses"] == 1
        
        # Set and hit
        await cache.set("ethereum", "0x123", {"price": 100})
        await cache.get("ethereum", "0x123")
        assert cache.stats["hits"] == 1
        assert cache.stats["size"] == 1

    @pytest.mark.asyncio
    async def test_concurrent_access(self, cache):
        """Test thread-safety with concurrent access."""
        async def writer(i):
            await cache.set("ethereum", f"0x{i:03d}", {"price": i})
        
        async def reader(i):
            return await cache.get("ethereum", f"0x{i:03d}")
        
        # Concurrent writes
        await asyncio.gather(*[writer(i) for i in range(10)])
        
        # Concurrent reads
        results = await asyncio.gather(*[reader(i) for i in range(10)])
        
        # All should be found
        for i, result in enumerate(results):
            assert result is not None
            assert result["price"] == i

    @pytest.mark.asyncio
    async def test_different_chains_same_address(self, cache):
        """Test that same address on different chains are cached separately."""
        await cache.set("ethereum", "0x123", {"price": 100})
        await cache.set("solana", "0x123", {"price": 200})
        
        eth_result = await cache.get("ethereum", "0x123")
        sol_result = await cache.get("solana", "0x123")
        
        assert eth_result["price"] == 100
        assert sol_result["price"] == 200


class TestCachedPrice:
    """Tests for CachedPrice dataclass."""

    def test_cached_price_creation(self):
        """Test creating a CachedPrice."""
        now = datetime.utcnow()
        cached = CachedPrice(data={"price": 100}, cached_at=now)
        
        assert cached.data == {"price": 100}
        assert cached.cached_at == now
