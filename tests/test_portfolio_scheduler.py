"""Tests for portfolio scheduler: start/stop lifecycle, status reporting."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional
from unittest.mock import AsyncMock

import pytest

from app.portfolio_scheduler import PortfolioScheduler
from app.portfolio_strategy import (
    PortfolioDiscoveryCycleResult,
    PortfolioExitCycleResult,
)


class MockPortfolioEngine:
    """Mock engine that returns empty results."""

    class _Config:
        price_check_seconds: int = 60

    def __init__(self, price_check_seconds: int = 60) -> None:
        self.discovery_calls = 0
        self.exit_calls = 0
        self.config = MockPortfolioEngine._Config()
        self.config.price_check_seconds = price_check_seconds

    async def run_discovery_cycle(self) -> PortfolioDiscoveryCycleResult:
        self.discovery_calls += 1
        return PortfolioDiscoveryCycleResult(
            timestamp=datetime.now(timezone.utc),
            summary="mock discovery",
        )

    async def run_exit_checks(self) -> PortfolioExitCycleResult:
        self.exit_calls += 1
        return PortfolioExitCycleResult(
            timestamp=datetime.now(timezone.utc),
            summary="mock exit",
        )


class TestSchedulerLifecycle:
    @pytest.mark.asyncio
    async def test_start_stop(self):
        engine = MockPortfolioEngine()
        scheduler = PortfolioScheduler(
            engine=engine,
            discovery_interval_seconds=3600,
            exit_check_interval_seconds=60,
        )

        assert not scheduler.is_running

        await scheduler.start()
        assert scheduler.is_running

        await scheduler.stop()
        assert not scheduler.is_running

    @pytest.mark.asyncio
    async def test_double_start_noop(self):
        engine = MockPortfolioEngine()
        scheduler = PortfolioScheduler(
            engine=engine,
            discovery_interval_seconds=3600,
            exit_check_interval_seconds=60,
        )

        await scheduler.start()
        await scheduler.start()  # Should not create extra tasks
        assert scheduler.is_running

        await scheduler.stop()

    @pytest.mark.asyncio
    async def test_run_discovery_now(self):
        engine = MockPortfolioEngine()
        scheduler = PortfolioScheduler(
            engine=engine,
            discovery_interval_seconds=3600,
            exit_check_interval_seconds=60,
        )

        result = await scheduler.run_discovery_now()
        assert result.summary == "mock discovery"
        assert engine.discovery_calls == 1

    @pytest.mark.asyncio
    async def test_run_exit_check_now(self):
        engine = MockPortfolioEngine()
        scheduler = PortfolioScheduler(
            engine=engine,
            discovery_interval_seconds=3600,
            exit_check_interval_seconds=60,
        )

        result = await scheduler.run_exit_check_now()
        assert result.summary == "mock exit"
        assert engine.exit_calls == 1


class TestSchedulerStatus:
    def test_status_initial(self):
        engine = MockPortfolioEngine(price_check_seconds=30)
        scheduler = PortfolioScheduler(
            engine=engine,
            discovery_interval_seconds=1800,
            exit_check_interval_seconds=30,
        )

        status = scheduler.get_status()
        assert status["running"] is False
        assert status["discovery_interval_seconds"] == 1800
        assert status["exit_check_interval_seconds"] == 30
        assert status["discovery_cycles"] == 0
        assert status["exit_check_cycles"] == 0
        assert status["last_discovery"] is None
        assert status["last_exit_check"] is None

    def test_status_reflects_live_price_check_seconds(self):
        """get_status returns the live engine.config.price_check_seconds value."""
        engine = MockPortfolioEngine(price_check_seconds=60)
        scheduler = PortfolioScheduler(
            engine=engine,
            discovery_interval_seconds=3600,
            exit_check_interval_seconds=60,
        )

        assert scheduler.get_status()["exit_check_interval_seconds"] == 60

        # Simulate a runtime update via /portfolio set
        engine.config.price_check_seconds = 120
        assert scheduler.get_status()["exit_check_interval_seconds"] == 120

    def test_exit_check_interval_property_falls_back_to_constructor_arg(self):
        """exit_check_interval falls back to constructor arg when engine.config lacks the attribute."""
        class MinimalEngine:
            pass

        engine = MinimalEngine()
        scheduler = PortfolioScheduler(
            engine=engine,  # type: ignore[arg-type]
            discovery_interval_seconds=3600,
            exit_check_interval_seconds=45,
        )

        # No engine.config → should use the constructor arg as fallback
        assert scheduler.exit_check_interval == 45

    @pytest.mark.asyncio
    async def test_status_after_cycles(self):
        engine = MockPortfolioEngine()
        scheduler = PortfolioScheduler(
            engine=engine,
            discovery_interval_seconds=3600,
            exit_check_interval_seconds=60,
        )

        await scheduler.run_discovery_now()
        await scheduler.run_exit_check_now()

        status = scheduler.get_status()
        assert status["discovery_cycles"] == 1
        assert status["exit_check_cycles"] == 1
        assert status["last_discovery"] is not None
        assert status["last_exit_check"] is not None


class TestSchedulerLoops:
    @pytest.mark.asyncio
    async def test_loops_run_on_start(self):
        """Both loops should execute at least once on start."""
        engine = MockPortfolioEngine()
        scheduler = PortfolioScheduler(
            engine=engine,
            discovery_interval_seconds=3600,
            exit_check_interval_seconds=3600,
        )

        await scheduler.start()
        # Give loops time to run once
        await asyncio.sleep(0.1)
        await scheduler.stop()

        assert engine.discovery_calls >= 1
        assert engine.exit_calls >= 1
