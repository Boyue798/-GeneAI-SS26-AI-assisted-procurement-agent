import json
import time
import os
import random
import logging
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys

# Configure logging for debug output
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class IdealoSeleniumScraper:
    """
    IdealoSeleniumScraper based on WLW architecture, using undetected_chromedriver.
    """

    def __init__(self, headless=False):
        logger.info(f"Initializing IdealoSeleniumScraper (headless={headless})")
        
        options = uc.ChromeOptions()
        if headless:
            options.add_argument("--headless")
        
        # Stability settings
        options.add_argument("--no-sandbox")
        options.add_argument("--window-size=1920,1080")
        
        try:
            # Using version_main=148 to match the system Chromium version 注意这个version每个网站or电脑不一样需要另行测试
            self.driver = uc.Chrome(options=options, version_main=148)
            self.wait = WebDriverWait(self.driver, 20)
            logger.info("Undetected ChromeDriver initialized successfully.")
        except Exception as e:
            logger.error(f"Failed to initialize UC: {e}")
            raise

    def handle_cookie_popup(self):

        try:
            time.sleep(5)

            iframes = self.driver.find_elements(By.TAG_NAME, "iframe")

            logger.info(f"Found {len(iframes)} iframes")

            for i, iframe in enumerate(iframes):

                logger.info(
                    f"iframe {i}: "
                    f"{iframe.get_attribute('outerHTML')[:300]}"
                )

                try:

                    self.driver.switch_to.frame(iframe)

                    buttons = self.driver.find_elements(
                        By.TAG_NAME,
                        "button"
                    )

                    logger.info(
                        f"iframe {i} contains {len(buttons)} buttons"
                    )

                    for btn in buttons:

                        try:
                            text = btn.text.strip()

                            logger.info(
                                f"iframe {i} button text: {text}"
                            )

                        except:
                            pass

                    self.driver.switch_to.default_content()

                except Exception as e:

                    logger.info(
                        f"Cannot switch iframe {i}: {e}"
                    )

                    self.driver.switch_to.default_content()

        except Exception as e:
            logger.info(e)

    def auto_scroll(self):
        """
        Simulates human-like scrolling.
        """
        logger.info("Auto-scrolling...")
        for _ in range(random.randint(2, 4)):
            scroll_amt = random.randint(400, 800)
            self.driver.execute_script(f"window.scrollBy(0, {scroll_amt});")
            time.sleep(random.uniform(0.5, 1.2))
        time.sleep(1)

    def scrape_search_results(self, search_term, limit=5):
        """
        1. Open Homepage
        2. Accept Cookies
        3. Input Search Term
        4. Extract URLs
        5. Visit Details
        """
        logger.info(f"Starting workflow for: '{search_term}'")
        
        try:
            # 1. Open Idealo Homepage
            logger.info("Navigating to Idealo homepage...")
            self.driver.get("https://www.idealo.de/")
            time.sleep(random.uniform(3, 5))

            """
            # 2. Handle Cookies #OPTIONAL
            self.handle_cookie_popup()
            """

            # 3. Simulate Search Input
            logger.info(f"Simulating search for: {search_term}")
            search_box_selectors = ["input#i-search-input", "input[name='q']", "input[type='search']"]
            search_input = None
            for sel in search_box_selectors:
                try:
                    search_input = self.wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, sel)))
                    if search_input: break
                except: continue
            
            if not search_input:
                raise Exception("Could not find search input field.")
            
            # Type term like a human
            for char in search_term:
                search_input.send_keys(char)
                time.sleep(random.uniform(0.1, 0.3))
            
            search_input.send_keys(Keys.ENTER)
            logger.info("Search submitted.")
            time.sleep(random.uniform(4, 6))
            
            # Save debug search page
            with open("debug_search_page.html", "w", encoding="utf-8") as f:
                f.write(self.driver.page_source)
            logger.info("Saved debug_search_page.html")

            # 4. Extract Product URLs
            self.auto_scroll()
            product_links = []
            selectors = [
                "a.productCard-link", 
                "div.offerList-item-header a", 
                "h3.productCard-title a",
                "a[href*='/OffersOfProduct/']"
            ]
            
            for selector in selectors:
                elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                for el in elements:
                    url = el.get_attribute("href")
                    if url and "OffersOfProduct" in url and url not in product_links:
                        product_links.append(url)
                    if len(product_links) >= limit: break
                if len(product_links) >= limit: break

            logger.info(f"Found {len(product_links)} products.")
            
            final_results = []
            for i, url in enumerate(product_links):
                logger.info(f"Scraping product {i+1}/{len(product_links)}: {url}")
                product_data = self.scrape_product_details(url)
                if product_data:
                    product_data["keyword"] = search_term
                    final_results.append(product_data)
                time.sleep(random.uniform(3, 6))

            # Save to JSON
            with open("results.json", "w", encoding="utf-8") as f:
                json.dump(final_results, f, indent=4, ensure_ascii=False)
            logger.info("Saved results.json")
            
            return final_results

        except Exception as e:
            logger.error(f"Error in scrape_search_results: {e}")
            return []

    def scrape_product_details(self, product_url):
        """
        Visits detail page and scrapes offers.
        """
        try:
            self.driver.get(product_url)
            time.sleep(random.uniform(3, 5))
            self.handle_cookie_popup()
            self.auto_scroll()

            if not os.path.exists("debug_product_page.html"):
                with open("debug_product_page.html", "w", encoding="utf-8") as f:
                    f.write(self.driver.page_source)
                logger.info("Saved debug_product_page.html")

            # Extract Name
            product_name = "N/A"
            for sel in ["h1.oopStage-title", "h1 span", "h1"]:
                try:
                    product_name = self.driver.find_element(By.CSS_SELECTOR, sel).text.strip()
                    if product_name: break
                except: continue
            
            logger.info(f"Product Name: {product_name}")

            # Extract Offers
            offers = []
            # Common offer row selectors
            row_selectors = [".productOffers-listItem", ".offerList-item", "div[data-offer-id]"]
            rows = []
            for sel in row_selectors:
                rows = self.driver.find_elements(By.CSS_SELECTOR, sel)
                if rows: break
            
            logger.info(f"Found {len(rows)} offer rows.")

            for row in rows:
                try:
                    # Shop
                    shop = "N/A"

                    try:
                        shop = row.find_element(
                            By.CSS_SELECTOR,
                            ".productOffers-listItemOfferShopV2LogoLink"
                        ).get_attribute("data-shop-name")

                        shop = shop.split(" - ")[0].strip()

                    except:
                        pass

                    # Price
                    price = "N/A"
                    try:
                        price = row.find_element(By.CSS_SELECTOR, ".productOffers-listItemOfferPrice, .price").text.strip()
                    except: pass

                    # Shipping
                    #shipping信息该网站没有 仅为保存字段
                    shipping = "N/A"

                    try:
                        shipping = row.find_element(
                            By.CSS_SELECTOR,
                            ".productOffers-listItemOfferShippingDetails"
                        ).get_attribute("title")

                    except:
                        pass
                    # Delivery
                    delivery = "N/A"

                    try:
                        delivery = row.find_element(
                            By.CSS_SELECTOR,
                            ".productOffers-listItemOfferDeliveryStatusDatesRange"
                        ).text.strip()

                    except:
                        pass

                    # Rating
                    rating = "N/A"

                    try:
                        rating = row.find_element(
                            By.CSS_SELECTOR,
                            ".productOffers-listItemOfferShopV2Stars b"
                        ).text.strip()

                    except:
                        pass

                    #review
                    reviews = "N/A"

                    try:
                        reviews = row.find_element(
                            By.CSS_SELECTOR,
                            ".productOffers-listItemOfferShopV2NORatings--numberOfRatings"
                        ).text.strip()

                    except:
                        pass

                    offers.append({
                        "shop": shop,
                        "price": price,
                        "shipping": shipping,
                        "delivery": delivery,
                        "rating": rating,
                        "reviews": reviews
                    })
                except:
                    continue

            return {
                "product_name": product_name,
                "product_url": product_url,
                "offers": offers
            }
        except Exception as e:
            logger.error(f"Error in scrape_product_details: {e}")
            return None

    def close(self):
        """
        Closes browser.
        """
        if hasattr(self, 'driver'):
            logger.info("Closing browser...")
            self.driver.quit()

if __name__ == "__main__":
    # Test execution
    scraper = IdealoSeleniumScraper(headless=False)
    try:
        results = scraper.scrape_search_results(
            search_term="Kopierpapier A4", #这是在网站搜索框输入的内容
            limit=3 #爬取数量
        )
        print(f"\nScraping finished. Total products: {len(results)}")
    finally:
        scraper.close()
