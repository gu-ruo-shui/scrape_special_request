import asyncio
import json
from contextlib import asynccontextmanager

from fastapi import (  # Query is used for more detailed parameter definition
    FastAPI,
    HTTPException,
    Query,
)
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

# --- Configuration ---
# Default values are removed as we'll require them from query parameters for the /scrape endpoint
# DEFAULT_PAGE_URL = "https://example.com/your-webpage"
# DEFAULT_TARGET_POST_URL_CONTAINS = "/api/your-data-endpoint"

# --- FastAPI App Setup ---
playwright_instance = None
browser_instance = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global playwright_instance, browser_instance
    print("Starting up Playwright...")
    playwright_instance = await async_playwright().start()
    browser_instance = await playwright_instance.chromium.launch(
        headless=True,
        args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
    )
    print("Playwright and browser started.")
    yield
    print("Shutting down Playwright...")
    if browser_instance:
        await browser_instance.close()
    if playwright_instance:
        await playwright_instance.stop()
    print("Playwright shut down.")


app = FastAPI(lifespan=lifespan)


async def scrape_data(page_url: str, target_post_url_contains: str):
    """
    Core scraping logic.
    """
    if not browser_instance:
        raise RuntimeError("Browser instance not initialized. Check lifespan.")

    captured_post_data = None
    capture_event = asyncio.Event()

    async def handle_response(response):
        nonlocal captured_post_data
        is_target_post = (
            target_post_url_contains in response.url
            and response.request.method == "POST"
        )

        if is_target_post:
            print(f"[*] Intercepted POST response from: {response.url}")
            try:
                data = await response.json()
                captured_post_data = data
                print(
                    f"[*] Data captured (JSON): {json.dumps(data, indent=2, ensure_ascii=False)}"
                )
            except Exception:
                try:
                    text_data = await response.text()
                    captured_post_data = {"raw_text": text_data}
                    print(f"[*] Data captured (Text): {text_data[:200]}...")
                except Exception as e_text:
                    print(
                        f"[!] Error parsing response data from {response.url}: {e_text}"
                    )
                    captured_post_data = {"error": str(e_text), "url": response.url}
            finally:
                if not capture_event.is_set():
                    capture_event.set()

    context = await browser_instance.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/98.0.4758.102 Safari/537.36"
    )
    page = await context.new_page()
    page.on("response", handle_response)

    try:
        print(f"[*] Navigating to {page_url}...")
        await page.goto(page_url, wait_until="networkidle", timeout=60000)
        print("[*] Page loaded, waiting for target POST request...")

        try:
            await asyncio.wait_for(capture_event.wait(), timeout=30.0)
        except asyncio.TimeoutError:
            print("[!] Timed out waiting for the target POST request to be captured.")
            if captured_post_data:
                print("[!] Data was captured before timeout, proceeding.")

    except PlaywrightTimeoutError:
        print(f"[!] Page navigation or networkidle timeout for {page_url}.")
    except Exception as e:
        print(f"[!] Error during scraping process for {page_url}: {e}")
        if captured_post_data is None:
            captured_post_data = {"error": f"Scraping lifecycle error: {str(e)}"}
    finally:
        if "page" in locals() and not page.is_closed():
            await page.close()
        if "context" in locals():
            await context.close()

    return captured_post_data


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
    """
    Endpoint to trigger the scraping process.
    Requires page_url and target_url_fragment as query parameters.
    Example: /scrape?page_url=https://example.com&target_url_fragment=/api/data
    """
    # The parameters page_url and target_url_fragment are now mandatory
    # because of Query(...)
    # FastAPI will automatically return a 422 error if they are not provided.

    print(
        f"Initiating scrape for: {page_url}, looking for POST containing: {target_url_fragment}"
    )
    data = await scrape_data(page_url, target_url_fragment)

    if data:
        return data
    else:
        print("Scraping failed, no data captured and no specific error message.")
        raise HTTPException(status_code=500, detail="Scraping failed to capture data.")


@app.get("/")
async def root():
    return {
        "message": "Scraper API is running. Use /scrape endpoint with page_url and target_url_fragment parameters."
    }


if __name__ == "__main__":
    import uvicorn

    # Example for local debugging:
    # uvicorn main:app --host 0.0.0.0 --port 8000 --reload
    # Then access: http://localhost:8000/scrape?page_url=YOUR_URL&target_url_fragment=YOUR_FRAGMENT
    uvicorn.run(app, host="0.0.0.0", port=8000)
