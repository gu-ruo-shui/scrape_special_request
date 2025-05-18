import asyncio
import json
from contextlib import asynccontextmanager

from fastapi import (  # Query is used for more detailed parameter definition
    FastAPI,
    HTTPException,
    Query,
)
from playwright.async_api import (  # For type hinting if needed
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

# Global instances for Playwright and Browser, managed by FastAPI lifespan
playwright_manager: Playwright | None = None
browser_instance: Browser | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global playwright_manager, browser_instance
    print("[Lifespan] Starting up Playwright...")
    playwright_manager = await async_playwright().start()
    browser_instance = await playwright_manager.chromium.launch(
        # headless=True, # Recommended for production
        # slow_mo=500 # Useful for debugging, slows down Playwright operations
    )  # Defaults to headless=False if not specified, matching original debug setup
    print("[Lifespan] Playwright browser launched.")
    yield
    print("[Lifespan] Shutting down Playwright...")
    if browser_instance:
        await browser_instance.close()
    if playwright_manager:
        await playwright_manager.stop()
    print("[Lifespan] Playwright shutdown complete.")


app = FastAPI(lifespan=lifespan)


# 用于存储捕获到的数据 (These globals are no longer used by the refactored main_scraper)
# captured_post_data = None
# capture_event = asyncio.Event()  # 用于通知主协程数据已捕获

# Original handle_response - kept for reference, but main_scraper now uses a local inner version
# to ensure concurrent request safety.
# async def handle_response(response):
#     global captured_post_data
#     # ... (original implementation)


async def main_scraper(page_url: str, target_url: str):
    global browser_instance  # Use the globally managed browser instance

    if not browser_instance:
        print(
            "[!] Global browser instance not initialized. Ensure FastAPI lifespan event ran."
        )
        raise HTTPException(status_code=503, detail="Browser service not ready")

    # Local data capture variables for this specific scraping task
    # Using a mutable dictionary to allow modification in the inner function
    _local_captured_post_data_holder = {"data": None}
    _local_capture_event = asyncio.Event()

    async def handle_response_inner(response):
        # This inner function is a closure, accessing _local_captured_post_data_holder
        # and _local_capture_event from the outer main_scraper scope.
        is_target_post = (
            target_url in response.url and response.request.method == "POST"
        )

        if is_target_post:
            print(f"[*] Intercepted POST response from: {response.url}")
            try:
                data = await response.json()
                _local_captured_post_data_holder["data"] = data
                print("[*] Data captured (local)")
                _local_capture_event.set()  # Notify main_scraper data is captured
            except Exception as e:
                print(f"[!] Error parsing response data from {response.url}: {e}")
                try:
                    text_data = await response.text()
                    print(
                        f"[!] Response text: {text_data[:500]}...  status: {response.status}"
                    )
                    _local_captured_post_data_holder["data"] = {
                        "error": str(e),
                        "raw_text": text_data,
                    }

                    request = response.request
                    print(f"    [DEBUG] Failed Request URL: {request.url}")
                    print(f"    [DEBUG] Failed Request Method: {request.method}")
                    request_headers_str = json.dumps(
                        await request.all_headers(), indent=2, ensure_ascii=False
                    )
                    print(f"    [DEBUG] Failed Request Headers: {request_headers_str}")
                except Exception as e_text:
                    print(f"[!] Error getting text response: {e_text}")
                    _local_captured_post_data_holder["data"] = {"error": str(e_text)}
                finally:
                    # Original code had a commented out capture_event.set() here.
                    # Current logic: event is set only on successful .json() parsing.
                    # If .json() fails, data is still stored in _local_captured_post_data_holder,
                    # and main_scraper will retrieve it after its own timeout.
                    # 这里有的网站会发 2 次请求, 第 1 次请求 405 因为此时 csrf-token 为 null
                    pass

    context: BrowserContext | None = None
    page: Page | None = None
    try:
        # Create a new context and page for each request for isolation
        context = await browser_instance.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        # Register response interceptor for this specific page
        page.on("response", handle_response_inner)

        print(f"[*] Navigating to {page_url}...")
        try:
            # Open page and wait for network activity to settle
            await page.goto(
                page_url, wait_until="networkidle", timeout=60000
            )  # 60-second timeout
            print("[*] Page loaded, waiting for target POST request...")
            # Small delay, as in original code, possibly to allow scripts to run
            await page.wait_for_timeout(1000)
        except PlaywrightTimeoutError:
            print(
                "[!] Page navigation or networkidle timeout. The POST might have already occurred or not at all."
            )
            # Proceed to wait for capture_event, as data might have been captured if POST occurred early
        except Exception as e:
            print(f"[!] Error during page navigation: {e}")
            # If navigation fails critically, unlikely to capture data.
            # The finally block will clean up page/context.
            return None

        # Wait for handle_response_inner to capture data or timeout
        try:
            # Using the local event for this specific scrape task
            await asyncio.wait_for(_local_capture_event.wait(), timeout=10.0)
        except asyncio.TimeoutError:
            print("[!] Timed out waiting for the target POST request to be captured.")
            # Even on timeout, check if data was captured (e.g., if .json() failed but error data was stored)
            if _local_captured_post_data_holder["data"]:
                print(
                    "[!] Data was captured (or error info stored) before timeout, proceeding."
                )
            else:
                print("[!] No data captured after timeout.")

        return _local_captured_post_data_holder["data"]

    except Exception as e_outer:
        # Catch any other unexpected errors during the main_scraper process
        print(f"[!!] An unexpected error occurred in main_scraper: {e_outer}")
        # Consider logging traceback here: import traceback; traceback.print_exc()
        return None  # Or raise HTTPException(status_code=500, detail=f"Scraping error: {e_outer}")
    finally:
        # Ensure page and context are closed for this specific request
        if page and not page.is_closed():
            try:
                await page.close()
            except Exception as e_page_close:
                print(f"[!] Error closing page: {e_page_close}")
        if context:
            try:
                await context.close()
            except Exception as e_context_close:
                print(f"[!] Error closing context: {e_context_close}")
        # The global browser_instance remains open, managed by lifespan.


@app.get("/scrape")
async def trigger_scrape_endpoint(
    page_url: str = Query(
        ..., title="Page URL", description="The full URL of the page to scrape."
    ),
    target_url_fragment: str = Query(
        ...,
        title="Target URL Fragment",
        description="A unique fragment of the target POST request URL.",
    ),
):
    print(
        f"Initiating scrape for: {page_url}, looking for POST containing: {target_url_fragment}"
    )
    data = await main_scraper(page_url, target_url_fragment)

    if data:
        return data
    else:
        # This part of the original code is slightly ambiguous.
        # If main_scraper returns None due to an internal error, it prints a message.
        # If it returns None because no specific data was found but no error,
        # it might also lead here. The detail message is generic.
        print(
            "Scraping process completed. Data may or may not have been captured. Check logs."
        )
        # Changed the HTTPException detail to be more informative if data is None.
        # If data is None because the target POST wasn't found (but no error),
        # this might still be a "success" in terms of process execution.
        # The current logic will raise 500 if data is None for any reason.
        raise HTTPException(
            status_code=404,  # Or 500 if None always means critical failure
            detail="Scraping did not yield data. Target POST may not have occurred or no data was captured.",
        )


@app.get("/")
async def root():
    return {
        "message": "Scraper API is running. Use /scrape endpoint with page_url and target_url_fragment parameters."
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
    # Example for local debugging:
    # uvicorn your_module_name:app --host 0.0.0.0 --port 8000 --reload
    # Then access: http://localhost:8000/scrape?page_url=YOUR_URL&target_url_fragment=YOUR_FRAGMENT
