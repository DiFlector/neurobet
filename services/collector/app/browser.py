"""Browser lifecycle manager using Playwright Chromium with Russian localization."""

import asyncio
import logging
from typing import Optional
from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from .config import settings

logger = logging.getLogger("collector.browser")


class BrowserLifecycleManager:
    """Manages long-lived Chromium instance with Russian locale and controlled page lifecycle."""

    def __init__(self):
        self.playwright = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        """Initialize Playwright and launch Chromium with Russian settings."""
        async with self._lock:
            if self.browser is not None:
                return

            logger.info("Initializing Playwright and launching Chromium (headless=%s)...", settings.headless)
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.launch(
                headless=settings.headless,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--no-first-run",
                    "--no-zygote",
                    "--single-process",
                    "--disable-background-networking",
                ],
            )
            # Create long-lived Russian localized browser context
            self.context = await self.browser.new_context(
                locale=settings.locale,
                timezone_id=settings.timezone_id,
                user_agent=(
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
                extra_http_headers={
                    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
                },
                viewport={"width": 1920, "height": 1080},
            )
            self._page = await self.context.new_page()
            logger.info("Browser initialized successfully with Russian locale (%s).", settings.locale)

    async def get_page(self) -> Page:
        """Get the reusable browser page, creating a new one if crashed/closed."""
        if self._page is None or self._page.is_closed():
            if self.context is None:
                await self.start()
            self._page = await self.context.new_page()
        return self._page

    async def fetch_page_content(self, url: str, timeout_ms: int = 15000) -> str:
        """Navigate to URL and return rendered HTML content."""
        page = await self.get_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        return await page.content()

    async def close(self) -> None:
        """Clean up page, context, browser, and playwright processes."""
        async with self._lock:
            logger.info("Closing browser lifecycle manager...")
            try:
                if self._page and not self._page.is_closed():
                    await self._page.close()
                if self.context:
                    await self.context.close()
                if self.browser:
                    await self.browser.close()
                if self.playwright:
                    await self.playwright.stop()
            except Exception as e:
                logger.warning("Error during browser teardown: %s", e)
            finally:
                self._page = None
                self.context = None
                self.browser = None
                self.playwright = None
            logger.info("Browser teardown complete.")
