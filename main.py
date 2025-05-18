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

app = FastAPI()


# 用于存储捕获到的数据
captured_post_data = None
capture_event = asyncio.Event()  # 用于通知主协程数据已捕获


async def main_scraper(page_url, target_url):
    global captured_post_data, capture_event
    captured_post_data = None  # 重置
    capture_event.clear()  # 重置事件

    async def handle_response(response):
        global captured_post_data
        # 检查是否是我们感兴趣的 POST 请求
        # 你可以根据 URL、方法、甚至请求体来判断
        is_target_post = (
            target_url in response.url and response.request.method == "POST"
        )
        # is_target_post = (response.url == TARGET_POST_URL_EXACT and response.request.method == "POST")

        if is_target_post:
            print(f"[*] Intercepted POST response from: {response.url}")
            try:
                # 尝试解析为 JSON，如果不是 JSON，可以获取文本
                data = await response.json()
                # data = await response.text()  # 如果是文本
                captured_post_data = data
                print(
                    f"[*] Data captured: {json.dumps(data, indent=2, ensure_ascii=False)}"
                )
                capture_event.set()  # 通知主协程数据已捕获
            except Exception as e:
                print(f"[!] Error parsing response data from {response.url}: {e}")
                try:
                    text_data = await response.text()
                    print(
                        f"[!] Response text: {text_data[:500]}...  status: {response.status}"
                    )  # 打印部分原始文本帮助调试
                    captured_post_data = {"error": str(e), "raw_text": text_data}

                    # 获取并打印导致此错误响应的原始请求的详细信息
                    request = response.request  # 获取请求对象
                    print(f"    [DEBUG] Failed Request URL: {request.url}")
                    print(f"    [DEBUG] Failed Request Method: {request.method}")
                    # 打印请求头
                    request_headers_str = json.dumps(
                        await request.all_headers(), indent=2, ensure_ascii=False
                    )
                    print(f"    [DEBUG] Failed Request Headers: {request_headers_str}")
                except Exception as e_text:
                    print(f"[!] Error getting text response: {e_text}")
                    captured_post_data = {"error": str(e_text)}
                finally:
                    capture_event.set()

    async with async_playwright() as p:
        # browser = await p.chromium.launch(headless=True) # 生产环境用 True
        browser = await p.chromium.launch(
            # headless=True, slow_mo=500
        )  # 调试时用 False，slow_mo 减慢操作

        # 创建一个浏览器上下文，可以设置 user-agent 等
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/98.0.4758.102 Safari/537.36"
        )
        page = await context.new_page()

        # 注册响应拦截器，在导航之前设置
        page.on("response", handle_response)

        print(f"[*] Navigating to {page_url}...")
        try:
            # 打开页面，等待网络空闲，表明页面主要资源（包括JS触发的请求）已加载
            # 根据实际情况调整 timeout，如果页面加载慢或 POST 请求触发晚
            await page.goto(
                page_url, wait_until="networkidle", timeout=60000
            )  # 60 秒超时
            print("[*] Page loaded, waiting for target POST request...")
        except PlaywrightTimeoutError:
            print(
                "[!] Page navigation or networkidle timeout. The POST might have already occurred or not at all."
            )
        except Exception as e:
            print(f"[!] Error during page navigation: {e}")
            await browser.close()
            return None

        # 等待 handle_response 捕获到数据或超时
        try:
            await asyncio.wait_for(
                capture_event.wait(), timeout=30.0
            )  # 等待30秒让POST请求完成
        except asyncio.TimeoutError:
            print("[!] Timed out waiting for the target POST request to be captured.")
            # 即使超时，如果 captured_post_data 在此之前被设置了，也可能是有用的
            if captured_post_data:
                print("[!] Data was captured before timeout, proceeding.")
            else:
                print("[!] No data captured after timeout.")

        await browser.close()
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
    data = await main_scraper(page_url, target_url_fragment)

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
