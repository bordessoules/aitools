"""
Docker Playwright support with pre-authenticated sessions.

This module provides:
- Headless/headed browser support in Docker
- Pre-authenticated session loading
- Cookie popup handling
- Ad blocking via request routing
"""

import asyncio
import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.async_api import Playwright

try:
    from playwright.async_api import async_playwright, Browser, BrowserContext, Page
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    Browser = BrowserContext = Page = None  # type: ignore[assignment,misc]

from . import config
from .logger import get_logger

log = get_logger("playwright")

# Common ad/tracking domains to block
AD_DOMAINS = {
    'googleadservices.com', 'googlesyndication.com', 'google-analytics.com',
    'doubleclick.net', 'facebook.com/tr', 'googleads.g.doubleclick.net',
    'adsystem.amazon.com', 'advertising.amazon.com', 'analytics.google.com',
    'connect.facebook.net', 'platform.twitter.com', 'ads.twitter.com',
    'ads.linkedin.com', 'analytics.twitter.com', 'ads.pinterest.com',
    'outbrain.com', 'taboola.com', 'scorecardresearch.com', 'quantserve.com',
    'googletagmanager.com', 'hotjar.com', 'optimizely.com', 'bounceexchange.com',
}

# Common cookie consent button selectors
COOKIE_SELECTORS = [
    'button:has-text("Accept")',
    'button:has-text("Accept all")',
    'button:has-text("I accept")',
    'button:has-text("Allow")',
    'button:has-text("Allow all")',
    'button:has-text("Essential only")',
    'button:has-text("Reject")',
    'button:has-text("Reject all")',
    'button:has-text("No, thanks")',
    'button:has-text("GOT IT")',
    'button:has-text("Continue")',
    '[aria-label*="cookie" i]',
    '[aria-label*="consent" i]',
    '[id*="cookie" i] button',
    '[class*="cookie" i] button',
    '[class*="consent" i] button',
    '[class*="gdpr" i] button',
    '#onetrust-accept-btn-handler',
    '#truste-consent-button',
    '.fc-button-label',
    '.cc-allow',
    '.cc-accept',
    '.cc-dismiss',
    '.cookie-banner button',
    '.cookie-consent button',
]


async def is_available() -> bool:
    """Check if Playwright is available in Docker."""
    if not PLAYWRIGHT_AVAILABLE:
        return False
    
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            await browser.close()
            return True
    except Exception as e:
        error_msg = str(e).encode('ascii', 'ignore').decode('ascii')
        log.info("Not available: %s", error_msg[:100])
        return False


async def create_browser_context(playwright: "Playwright", headed: bool | None = None) -> tuple[Browser, BrowserContext]:
    """
    Create browser with optional pre-authenticated session.
    
    Args:
        headed: Force headed/headless mode (None = use config)
    
    Returns:
        Tuple of (browser, context)
    """
    use_headed = headed if headed is not None else config.PLAYWRIGHT_DOCKER_HEADED
    
    launch_args = [
        '--no-sandbox',
        '--disable-dev-shm-usage',
        '--disable-gpu',
        '--disable-web-security',
        '--disable-features=IsolateOrigins,site-per-process',
    ]
    
    # Launch browser
    browser = await playwright.chromium.launch(
        headless=not use_headed,
        args=launch_args
    )
    
    # Context options
    context_options = {
        'viewport': {'width': 1920, 'height': 1080},
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.0',
    }
    
    # Load pre-authenticated session if available
    auth_state_path = config.PLAYWRIGHT_AUTH_STATE
    if auth_state_path.exists():
        log.info("Loading auth state from %s", auth_state_path)
        context_options['storage_state'] = str(auth_state_path)
    else:
        log.debug("No auth state found at %s", auth_state_path)
    
    context = await browser.new_context(**context_options)
    
    return browser, context


async def setup_ad_blocking(context: BrowserContext):
    """Setup request routing to block ads and trackers."""
    
    async def route_handler(route):
        url = route.request.url
        try:
            from urllib.parse import urlparse
            hostname = urlparse(url).hostname or ""
        except Exception:
            hostname = ""

        # Block ad/tracking domains
        if hostname and any(ad_domain in hostname for ad_domain in AD_DOMAINS):
            await route.abort()
            return
        
        # Block common tracking patterns
        if any(pattern in url.lower() for pattern in [
            '/analytics', '/tracking', '/metrics', '/pixel',
            'google-analytics', 'facebook.com/tr', 'gtm.js',
        ]):
            await route.abort()
            return
        
        await route.fallback()
    
    await context.route("**/*", route_handler)


async def handle_cookie_popup(page: Page, timeout: int = 3000) -> bool:
    """
    Auto-click cookie consent popups.
    
    Returns:
        True if popup was handled, False otherwise
    """
    for selector in COOKIE_SELECTORS:
        try:
            button = page.locator(selector).first
            if await button.is_visible(timeout=500):
                await button.click()
                log.debug("Cookie popup handled: %s", selector)
                await asyncio.sleep(0.5)
                return True
        except Exception as e:
            log.debug("Cookie selector %s failed: %s", selector, e)
            continue
    
    return False


async def extract_webpage(url: str, wait_for_js: bool = True) -> str:
    """
    Extract webpage content using Docker Playwright.
    
    Features:
    - Pre-authenticated sessions
    - Ad blocking
    - Cookie popup handling
    - JavaScript execution
    
    Args:
        url: URL to extract
        wait_for_js: Wait for JavaScript to load
    
    Returns:
        Extracted HTML content
    """
    if not PLAYWRIGHT_AVAILABLE:
        raise RuntimeError("Playwright not installed. Run: pip install playwright")
    
    async with async_playwright() as p:
        browser, context = await create_browser_context(p)
        
        try:
            # Setup ad blocking
            await setup_ad_blocking(context)
            
            # Create new page
            page = await context.new_page()
            
            # Navigate
            log.info("Navigating to %s...", url[:60])
            await page.goto(url, wait_until='networkidle' if wait_for_js else 'domcontentloaded')
            
            # Handle cookie popup
            await handle_cookie_popup(page)
            
            # Wait a bit for any delayed content
            if wait_for_js:
                await asyncio.sleep(2)
                
                # Try to scroll to trigger lazy loading
                try:
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
                    await asyncio.sleep(1)
                except Exception as e:
                    log.debug("Scroll failed: %s", e)

            # Get content
            content = await page.content()
            
            log.info("Extracted %d chars", len(content))
            return content
            
        finally:
            await browser.close()


async def save_auth_state(email: str, password: str, url: str = "https://accounts.google.com"):
    """
    Create and save authenticated session.
    
    Use this locally to create auth state, then copy to Docker.
    
    Args:
        email: Login email
        password: Login password
        url: Login URL
    """
    if not PLAYWRIGHT_AVAILABLE:
        raise RuntimeError("Playwright not installed")
    
    log.info("Creating authenticated session...")
    log.info("This will open a browser window for you to complete login")
    
    async with async_playwright() as p:
        # Launch headed browser for manual interaction if needed
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
        
        try:
            # Navigate to login
            await page.goto(url)
            
            # Wait for manual login completion
            log.info("Please complete login in the browser window...")
            log.info("Press Enter when done (or wait 60s timeout)...")
            
            # Wait for navigation to success page
            try:
                await page.wait_for_url("**/myaccount.google.com/**", timeout=60000)
                log.info("Login detected!")
            except Exception:
                input("[Auth Setup] Press Enter when login is complete...")
            
            # Save state
            auth_path = config.PLAYWRIGHT_AUTH_STATE
            auth_path.parent.mkdir(parents=True, exist_ok=True)
            
            await context.storage_state(path=str(auth_path))
            log.info("Auth state saved to %s", auth_path)
            
        finally:
            await browser.close()


# Xvfb helper for headed mode in Docker
def start_xvfb():
    """Start Xvfb virtual display for headed browser in Docker.

    Normally Xvfb is started by the Dockerfile CMD before Python runs,
    so this is only needed for one-off docker exec scripts.
    If DISPLAY is already set, this is a no-op.
    """
    if os.environ.get('DISPLAY'):
        return  # Xvfb already running (started by CMD or externally)

    try:
        import subprocess

        display = config.XVFB_DISPLAY
        screen = config.XVFB_SCREEN_SIZE

        # Start Xvfb (must be pre-installed in the Docker image)
        subprocess.Popen([
            'Xvfb', display, '-screen', '0', screen,
            '-ac', '+extension', 'RANDR', '-noreset'
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        os.environ['DISPLAY'] = display
        log.info("Xvfb started on display %s", display)

        import time
        time.sleep(1)

    except Exception as e:
        log.warning("Xvfb failed to start: %s", e)


# Initialize Xvfb if headed mode is enabled (fallback for docker exec)
if config.PLAYWRIGHT_DOCKER_HEADED:
    start_xvfb()
