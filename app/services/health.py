"""
Health check HTTP server.

Runs alongside the bot to provide health status for monitoring.
Useful for:
- Docker health checks
- Kubernetes liveness/readiness probes
- Uptime monitoring services
"""

import asyncio
from datetime import datetime
from typing import Optional

from aiohttp import web
import structlog
from sqlalchemy import text

from app.config import settings
from app.db.session import async_session_factory


logger = structlog.get_logger()


class HealthCheckServer:
    """Simple HTTP server for health checks."""

    def __init__(self, host: str = "0.0.0.0", port: int = 8080):
        self.host = host
        self.port = port
        self._app: Optional[web.Application] = None
        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.TCPSite] = None
        self._started_at: Optional[datetime] = None

    async def start(self):
        """Start the health check server."""
        self._app = web.Application()
        self._app.router.add_get("/health", self._handle_health)
        self._app.router.add_get("/ready", self._handle_ready)
        self._app.router.add_get("/", self._handle_root)

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()

        self._site = web.TCPSite(self._runner, self.host, self.port)
        await self._site.start()

        self._started_at = datetime.utcnow()

        logger.info(
            "Health check server started",
            host=self.host,
            port=self.port,
        )

    async def stop(self):
        """Stop the health check server."""
        if self._runner:
            await self._runner.cleanup()
            logger.info("Health check server stopped")

    async def _handle_root(self, request: web.Request) -> web.Response:
        """Root endpoint - basic info."""
        return web.json_response({
            "service": "ryadom-bot",
            "version": "1.0.0",
            "status": "running",
        })

    async def _handle_health(self, request: web.Request) -> web.Response:
        """
        Health check endpoint.

        Returns 200 if the service is running.
        This is for liveness probes - is the process alive?
        """
        uptime_seconds = 0
        if self._started_at:
            uptime_seconds = (datetime.utcnow() - self._started_at).total_seconds()

        return web.json_response({
            "status": "healthy",
            "uptime_seconds": int(uptime_seconds),
            "timestamp": datetime.utcnow().isoformat(),
        })

    async def _handle_ready(self, request: web.Request) -> web.Response:
        """
        Readiness check endpoint.

        Returns 200 if the service can accept traffic.
        This checks database connectivity.
        """
        # Check database
        db_ok = False
        try:
            async with async_session_factory() as session:
                await session.execute(text("SELECT 1"))
                db_ok = True
        except Exception as e:
            logger.warning("Database health check failed", error=str(e))

        if db_ok:
            return web.json_response({
                "status": "ready",
                "database": "connected",
                "timestamp": datetime.utcnow().isoformat(),
            })
        else:
            return web.json_response(
                {
                    "status": "not_ready",
                    "database": "disconnected",
                    "timestamp": datetime.utcnow().isoformat(),
                },
                status=503,
            )


# Global instance
_health_server: Optional[HealthCheckServer] = None


async def start_health_server(port: int = 8080) -> HealthCheckServer:
    """Start health check server."""
    global _health_server
    if _health_server is None:
        _health_server = HealthCheckServer(port=port)
        await _health_server.start()
    return _health_server


async def stop_health_server():
    """Stop health check server."""
    global _health_server
    if _health_server:
        await _health_server.stop()
        _health_server = None
