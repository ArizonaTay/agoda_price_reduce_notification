import os
import re
import json
import html
import sys
import requests
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
PRICES_FILE = "prices.json"


def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"[Telegram preview]: {message}")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        resp = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }, timeout=10)
        if not resp.ok:
            print(f"Telegram error: {resp.status_code} {resp.text}")
    except Exception as e:
        print(f"Telegram request failed: {e}")


MIN_PRICE = 10.0
MIN_PRICE_FALLBACK = 30.0


def _debug_page(page, tag):
    safe = tag.replace(" ", "_").replace("/", "_")
    page.screenshot(path=f"debug_{safe}.png")
    h = page.content()
    with open(f"debug_{safe}.html", "w") as f:
        f.write(h)
    print(f"  Debug files saved: debug_{safe}.png / .html")


def scrape_hotel_price(hotel):
    url = hotel["url"]
    room_name = hotel["room_name"]
    print(f"Scraping {hotel['name']} — looking for room: {room_name}")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
            ]
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            extra_http_headers={
                "Accept-Language": "en-SG,en;q=0.9",
            },
            viewport={"width": 1920, "height": 1080},
        )
        page = context.new_page()

        page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        """)

        page.goto(url, wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(8000)

        try:
            page.wait_for_selector('[data-selenium], [class*="room"], [class*="Room"], [class*="price"], [class*="Price"]', timeout=20000)
        except:
            pass
        page.wait_for_timeout(5000)

        current_url = page.url
        if "signin" in current_url.lower() or "login" in current_url.lower() or "captcha" in current_url.lower():
            _debug_page(page, "login_redirect")
            raise Exception("Redirected to login — anti-bot triggered")

        target_price = None
        currency = ""

        # Strategy 1: data-selenium attribute based extraction
        if target_price is None:
            print("  Trying strategy 1: data-selenium selectors")
            try:
                result = page.evaluate("""(data) => {
                    const {roomName, minPrice} = data;
                    const lowerName = roomName.toLowerCase();

                    const currencyMap = {
                        '$': 'USD', 'S$': 'SGD', 'HK$': 'HKD', 'NT$': 'TWD',
                        '\\u00a5': 'JPY', '\\u20ac': 'EUR', '\\u00a3': 'GBP',
                        'A$': 'AUD', 'C$': 'CAD', '\\u20b9': 'INR',
                        'RM': 'MYR', '\\u20ab': 'VND', '\\u0e3f': 'THB',
                        '\\u20a9': 'KRW',
                    };

                    const normalizeCurrency = (sym) => {
                        if (!sym) return 'SGD';
                        if (sym in currencyMap) return currencyMap[sym];
                        const upper = sym.toUpperCase();
                        if (upper.startsWith('S$')) return 'SGD';
                        if (upper.startsWith('HK$')) return 'HKD';
                        if (upper.startsWith('NT$')) return 'TWD';
                        if (upper.startsWith('A$')) return 'AUD';
                        if (upper.startsWith('C$')) return 'CAD';
                        if (upper.endsWith('$')) return 'USD';
                        if (['SGD', 'USD', 'EUR', 'GBP', 'JPY', 'KRW', 'MYR', 'THB', 'IDR', 'VND', 'CNY', 'TWD', 'HKD', 'AUD', 'CAD', 'INR'].includes(upper)) return upper;
                        return 'SGD';
                    };

                    const parsePriceAny = (text) => {
                        if (!text) return null;
                        const matches = [...text.matchAll(/(?:S\\s*\\$|HK\\s*\\$|NT\\s*\\$|A\\s*\\$|C\\s*\\$|[\\u00a5\\u20ac\\u00a3\\u20b9]|\\$|[A-Z]{2,3})\\s*[\\d,]{2,}\\.?\\d*/g)];
                        const results = [];
                        const seen = new Set();
                        for (const m of matches) {
                            const raw = m[0].trim();
                            const sym = raw.match(/(S\\s*\\$|HK\\s*\\$|NT\\s*\\$|A\\s*\\$|C\\s*\\$|[\\u00a5\\u20ac\\u00a3\\u20b9]|\\$|[A-Z]{2,3})/)?.[0] || '';
                            const numStr = raw.replace(sym, '').trim().replace(/,/g, '');
                            const num = parseFloat(numStr);
                            if (num >= minPrice && !seen.has(num)) {
                                seen.add(num);
                                results.push({ price: num, currency: normalizeCurrency(sym.trim()) });
                            }
                        }
                        return results.length > 0 ? results : null;
                    };

                    const excludeKw = ['breakfast', 'optional', 'add-on', 'add on', 'supplement', 'extra', 'tax', 'fee', 'save'];

                    // Find room name elements via data-selenium or text content
                    let nameElements = [];

                    const seleniumRoomSelectors = [
                        '[data-selenium="hotel-room-name"]',
                        '[data-selenium="MasterRoom-headerTitle"]',
                        '[data-selenium*="headerTitle"]',
                        '[data-selenium*="room-name"]',
                        '[data-selenium*="roomName"]',
                        '[data-selenium*="room_name"]',
                        '[data-selenium*="roomtitle"]',
                        '[data-selenium*="room-title"]',
                    ];

                    for (const sel of seleniumRoomSelectors) {
                        const els = document.querySelectorAll(sel);
                        for (const el of els) {
                            const t = (el.textContent || '').trim();
                            if (t.toLowerCase().includes(lowerName)) {
                                nameElements.push(el);
                            }
                        }
                        if (nameElements.length > 0) break;
                    }

                    // Fallback: find by text content if selenium attributes didn't match
                    if (nameElements.length === 0) {
                        const all = document.querySelectorAll('*');
                        for (const el of all) {
                            if (el.children.length > 0) continue;
                            if (!el.textContent) continue;
                            const t = el.textContent.trim();
                            if (t.length > 0 && t.length < 200 && t.toLowerCase().includes(lowerName)) {
                                nameElements.push(el);
                            }
                        }
                        if (nameElements.length === 0) {
                            for (const el of all) {
                                if (el.children.length > 3) continue;
                                if (!el.textContent) continue;
                                const t = el.textContent.trim();
                                if (t.length > 0 && t.length < 200 && t.toLowerCase().includes(lowerName)) {
                                    nameElements.push(el);
                                }
                            }
                        }
                        // Sort deepest first
                        nameElements.sort((a, b) => {
                            let da = 0, db = 0, ca = a, cb = b;
                            while (ca) { ca = ca.parentElement; da++; }
                            while (cb) { cb = cb.parentElement; db++; }
                            return db - da;
                        });
                    }

                    if (nameElements.length === 0) return null;

                    // For each found name element, walk up to find the price
                    for (const el of nameElements) {
                        // Walk up to find room card or container
                        let card = el.parentElement;
                        let safety = 0;
                        while (card && safety < 15) {
                            const cls = (card.className || '').toLowerCase();
                            const ds = (card.getAttribute('data-selenium') || '').toLowerCase();
                            if (cls.includes('roomcard') || cls.includes('room-card') ||
                                cls.includes('roomlist') || cls.includes('room-list') ||
                                ds.includes('roomcard') || ds.includes('room-card') ||
                                ds.includes('room-list') || ds.includes('roomlist') ||
                                ds.includes('masterroom')) break;
                            card = card.parentElement;
                            safety++;
                        }
                        if (!card || safety >= 15) {
                            card = el;
                            for (let i = 0; i < 6; i++) {
                                if (card.parentElement) card = card.parentElement;
                            }
                        }

                        // Find price elements within the card
                        const priceResults = [];
                        const priceSelectors = [
                            '[data-selenium="PriceDisplay"]',
                            '[data-selenium="room-price"]',
                            '[data-selenium*="price" i]',
                            '[data-selenium*="total" i]',
                            '[data-selenium="display-price"]',
                            '[class*="finalPrice"]',
                            '[class*="roomPrice"]',
                            '[class*="totalPrice"]',
                            '[class*="RoomPrice"]',
                            '[class*="price"]',
                            '[class*="Price"]',
                        ];

                        for (const ps of priceSelectors) {
                            try {
                                const priceEls = card.querySelectorAll(ps);
                                for (const pEl of priceEls) {
                                    const t = (pEl.textContent || '').trim();
                                    if (t.length > 0 && t.length < 80) {
                                        const hasKw = excludeKw.some(kw => t.toLowerCase().includes(kw));
                                        if (!hasKw) {
                                            const prices = parsePriceAny(t);
                                            if (prices) {
                                                for (const p of prices) {
                                                    priceResults.push(p);
                                                }
                                            }
                                        }
                                    }
                                }
                            } catch(e) {}
                            if (priceResults.length > 0) break;
                        }

                        // If no price found via selectors, walk the DOM
                        if (priceResults.length === 0) {
                            const walkCollect = (el, depth) => {
                                if (!el || depth > 10) return;
                                const t = (el.textContent || '').trim();
                                if (t.length > 0 && t.length < 100) {
                                    const prices = parsePriceAny(t);
                                    if (prices) {
                                        const hasKw = excludeKw.some(kw => t.toLowerCase().includes(kw));
                                        if (!hasKw) {
                                            for (const p of prices) {
                                                priceResults.push(p);
                                            }
                                        }
                                    }
                                }
                                if (depth < 8) {
                                    for (const c of el.children) {
                                        walkCollect(c, depth + 1);
                                    }
                                }
                            };
                            walkCollect(card, 0);
                        }

                        if (priceResults.length > 0) {
                            priceResults.sort((a, b) => a.price - b.price);
                            return priceResults[0];
                        }
                    }

                    return null;
                }""", {"roomName": room_name, "minPrice": MIN_PRICE})

                if result:
                    target_price = result["price"]
                    currency = result["currency"]
                    print(f"  Found by strategy 1: {currency}{target_price}")
            except Exception as e:
                print(f"  Strategy 1 error: {e}")

        # Strategy 2: CSS card selectors
        if target_price is None:
            print("  Strategy 1 failed, trying strategy 2: CSS card selectors")
            page.wait_for_timeout(3000)

            try:
                result = page.evaluate("""(data) => {
                    const {roomName, minPrice} = data;
                    const lowerName = roomName.toLowerCase();

                    const currencyMap = {
                        '$': 'USD', 'S$': 'SGD', 'HK$': 'HKD', 'NT$': 'TWD',
                        '\\u00a5': 'JPY', '\\u20ac': 'EUR', '\\u00a3': 'GBP',
                        'A$': 'AUD', 'C$': 'CAD', '\\u20b9': 'INR',
                        'RM': 'MYR', '\\u20ab': 'VND', '\\u0e3f': 'THB', '\\u20a9': 'KRW',
                    };

                    const normalizeCurrency = (sym) => {
                        if (!sym) return 'SGD';
                        if (sym in currencyMap) return currencyMap[sym];
                        const upper = sym.toUpperCase();
                        if (upper.startsWith('S$')) return 'SGD';
                        if (upper.startsWith('HK$')) return 'HKD';
                        if (upper.startsWith('NT$')) return 'TWD';
                        if (upper.startsWith('A$')) return 'AUD';
                        if (upper.startsWith('C$')) return 'CAD';
                        if (upper.endsWith('$')) return 'USD';
                        if (['SGD', 'USD', 'EUR', 'GBP', 'JPY', 'KRW', 'MYR', 'THB', 'IDR', 'VND', 'CNY', 'TWD', 'HKD', 'AUD', 'CAD', 'INR'].includes(upper)) return upper;
                        return 'SGD';
                    };

                    const parsePriceAny = (text) => {
                        if (!text) return null;
                        const matches = [...text.matchAll(/(?:S\\s*\\$|HK\\s*\\$|NT\\s*\\$|A\\s*\\$|C\\s*\\$|[\\u00a5\\u20ac\\u00a3\\u20b9]|\\$|[A-Z]{2,3})\\s*[\\d,]{2,}\\.?\\d*/g)];
                        const results = [];
                        const seen = new Set();
                        for (const m of matches) {
                            const raw = m[0].trim();
                            const sym = raw.match(/(S\\s*\\$|HK\\s*\\$|NT\\s*\\$|A\\s*\\$|C\\s*\\$|[\\u00a5\\u20ac\\u00a3\\u20b9]|\\$|[A-Z]{2,3})/)?.[0] || '';
                            const numStr = raw.replace(sym, '').trim().replace(/,/g, '');
                            const num = parseFloat(numStr);
                            if (num >= minPrice && !seen.has(num)) {
                                seen.add(num);
                                results.push({ price: num, currency: normalizeCurrency(sym.trim()) });
                            }
                        }
                        return results.length > 0 ? results : null;
                    };

                    const excludeKw = ['breakfast', 'optional', 'add-on', 'add on', 'supplement', 'extra', 'tax', 'fee', 'save'];

                    // Find all room-like cards/containers
                    const cardSelectors = [
                        '[data-selenium="MasterRoom"]',
                        '[class*="RoomList"]', '[class*="roomlist"]',
                        '[class*="room-list"]', '[class*="RatePlan"]',
                        '[data-selenium*="room-list"]', '[data-selenium*="roomlist"]',
                        '[data-selenium*="rateplan"]', '[data-selenium*="rate-plan"]',
                        '[class*="HotelRoom"]', '[class*="hotel-room"]',
                        '[class*="RoomCard"]', '[class*="room-card"]',
                        '[class*="roomcard"]',
                    ];

                    // Try to find cards, then extract price
                    for (const cardSel of cardSelectors) {
                        const cards = document.querySelectorAll(cardSel);
                        if (!cards || cards.length === 0) continue;

                        for (const card of cards) {
                            const cardText = (card.textContent || '').toLowerCase();
                            if (!cardText.includes(lowerName)) continue;

                            // Found the card with our room name, look for price
                            const priceSelectors = [
                                '[data-selenium="PriceDisplay"]',
                                '[data-selenium="room-price"]',
                                '[data-selenium*="price" i]',
                                '[data-selenium="display-price"]',
                                '[class*="finalPrice"]',
                                '[class*="roomPrice"]',
                                '[class*="totalPrice"]',
                                '[class*="RoomPrice"]',
                                '[class*="price"]',
                                '[class*="Price"]',
                            ];

                            for (const ps of priceSelectors) {
                                const priceEls = card.querySelectorAll(ps);
                                for (const pEl of priceEls) {
                                    const t = (pEl.textContent || '').trim();
                                    if (t.length > 0 && t.length < 80) {
                                        const hasKw = excludeKw.some(kw => t.toLowerCase().includes(kw));
                                        if (!hasKw) {
                                            const prices = parsePriceAny(t);
                                            if (prices) {
                                                prices.sort((a, b) => a.price - b.price);
                                                return prices[0];
                                            }
                                        }
                                    }
                                }
                            }

                            // Fallback: walk DOM within card
                            const walkPrices = [];
                            const walkCollect = (el, depth) => {
                                if (!el || depth > 8) return;
                                const t = (el.textContent || '').trim();
                                if (t.length > 0 && t.length < 100) {
                                    const prices = parsePriceAny(t);
                                    if (prices) {
                                        const hasKw = excludeKw.some(kw => t.toLowerCase().includes(kw));
                                        if (!hasKw) {
                                            for (const p of prices) {
                                                walkPrices.push(p);
                                            }
                                        }
                                    }
                                }
                                for (const c of el.children) {
                                    walkCollect(c, depth + 1);
                                }
                            };
                            walkCollect(card, 0);
                            if (walkPrices.length > 0) {
                                walkPrices.sort((a, b) => a.price - b.price);
                                return walkPrices[0];
                            }
                        }
                    }
                    return null;
                }""", {"roomName": room_name, "minPrice": MIN_PRICE})

                if result:
                    target_price = result["price"]
                    currency = result["currency"]
                    print(f"  Found by strategy 2: {currency}{target_price}")
            except Exception as e:
                print(f"  Strategy 2 error: {e}")

        # Strategy 3: fallback broad scrape
        if target_price is None:
            _debug_page(page, f"room_not_found_{hotel['name']}")
            print(f"  Room not found, scraping all visible prices >= ${MIN_PRICE_FALLBACK}")

            try:
                result = page.evaluate("""(data) => {
                    const {minPrice} = data;

                    const currencyMap = {
                        '$': 'USD', 'S$': 'SGD', 'HK$': 'HKD', 'NT$': 'TWD',
                        '\\u00a5': 'JPY', '\\u20ac': 'EUR', '\\u00a3': 'GBP',
                        'A$': 'AUD', 'C$': 'CAD', '\\u20b9': 'INR',
                        'RM': 'MYR', '\\u20ab': 'VND', '\\u0e3f': 'THB', '\\u20a9': 'KRW',
                    };

                    const normalizeCurrency = (sym) => {
                        if (!sym) return 'SGD';
                        if (sym in currencyMap) return currencyMap[sym];
                        const upper = sym.toUpperCase();
                        if (upper.startsWith('S$')) return 'SGD';
                        if (upper.startsWith('HK$')) return 'HKD';
                        if (upper.startsWith('NT$')) return 'TWD';
                        if (upper.startsWith('A$')) return 'AUD';
                        if (upper.startsWith('C$')) return 'CAD';
                        if (upper.endsWith('$')) return 'USD';
                        if (['SGD', 'USD', 'EUR', 'GBP', 'JPY', 'KRW', 'MYR', 'THB', 'IDR', 'VND', 'CNY', 'TWD', 'HKD', 'AUD', 'CAD', 'INR'].includes(upper)) return upper;
                        return 'SGD';
                    };

                    const parsePriceAny = (text) => {
                        if (!text) return null;
                        const matches = [...text.matchAll(/(?:S\\s*\\$|HK\\s*\\$|NT\\s*\\$|A\\s*\\$|C\\s*\\$|[\\u00a5\\u20ac\\u00a3\\u20b9]|\\$|[A-Z]{2,3})\\s*[\\d,]{2,}\\.?\\d*/g)];
                        const results = [];
                        const seen = new Set();
                        for (const m of matches) {
                            const raw = m[0].trim();
                            const sym = raw.match(/(S\\s*\\$|HK\\s*\\$|NT\\s*\\$|A\\s*\\$|C\\s*\\$|[\\u00a5\\u20ac\\u00a3\\u20b9]|\\$|[A-Z]{2,3})/)?.[0] || '';
                            const numStr = raw.replace(sym, '').trim().replace(/,/g, '');
                            const num = parseFloat(numStr);
                            if (num >= minPrice && !seen.has(num)) {
                                seen.add(num);
                                results.push({ price: num, currency: normalizeCurrency(sym.trim()) });
                            }
                        }
                        return results.length > 0 ? results : null;
                    };

                    const excludeKw = ['breakfast', 'optional', 'add-on', 'add on', 'supplement', 'extra', 'tax', 'fee', 'save'];

                    // Broad price element selectors
                    const priceSelectors = [
                        '[data-selenium="PriceDisplay"]',
                        '[data-selenium="room-price"]',
                        '[data-selenium*="price" i]',
                        '[data-selenium*="total" i]',
                        '[data-selenium="display-price"]',
                        '[class*="finalPrice"]',
                        '[class*="roomPrice"]',
                        '[class*="totalPrice"]',
                        '[class*="RoomPrice"]',
                        '[class*="price"]',
                        '[class*="Price"]',
                    ];

                    const candidates = [];
                    const seenPrices = new Set();

                    for (const ps of priceSelectors) {
                        const els = document.querySelectorAll(ps);
                        for (const el of els) {
                            const t = (el.textContent || '').trim();
                            if (t.length === 0 || t.length > 100) continue;
                            const hasKw = excludeKw.some(kw => t.toLowerCase().includes(kw));
                            if (hasKw) continue;
                            const prices = parsePriceAny(t);
                            if (prices) {
                                for (const p of prices) {
                                    if (!seenPrices.has(p.price)) {
                                        seenPrices.add(p.price);
                                        candidates.push(p);
                                    }
                                }
                            }
                        }
                    }

                    // Also walk all leaf elements
                    if (candidates.length === 0) {
                        const all = document.querySelectorAll('*');
                        for (const el of all) {
                            if (el.children.length > 0) continue;
                            const t = (el.textContent || '').trim();
                            if (t.length === 0 || t.length > 80) continue;
                            const hasKw = excludeKw.some(kw => t.toLowerCase().includes(kw));
                            if (hasKw) continue;
                            const prices = parsePriceAny(t);
                            if (prices) {
                                for (const p of prices) {
                                    if (!seenPrices.has(p.price)) {
                                        seenPrices.add(p.price);
                                        candidates.push(p);
                                    }
                                }
                            }
                        }
                    }

                    if (candidates.length === 0) return null;
                    candidates.sort((a, b) => a.price - b.price);
                    return candidates[0];
                }""", {"minPrice": MIN_PRICE_FALLBACK})

                if result:
                    target_price = result["price"]
                    currency = result["currency"]
                    print(f"  Fallback: found {currency}{target_price}")
            except Exception as e:
                print(f"  Strategy 3 error: {e}")

        browser.close()

        if target_price is None:
            _debug_page(page, f"no_price_{hotel['name']}")
            raise Exception(f"Could not find any price >= ${MIN_PRICE_FALLBACK}")

        return target_price, currency


def main():
    if not os.path.exists(PRICES_FILE):
        print(f"Error: {PRICES_FILE} not found")
        sys.exit(1)

    with open(PRICES_FILE) as f:
        hotels = json.load(f)

    changed = False
    for hotel in hotels:
        try:
            new_price, currency = scrape_hotel_price(hotel)
        except Exception as e:
            msg = f"⚠️ Error: {hotel['name']} — {html.escape(str(e))}"
            print(msg)
            send_telegram(msg)
            continue

        old_price = hotel["last_price"]
        hotel["currency"] = currency or "SGD"

        old_str = f"{old_price:.2f}" if old_price is not None else "N/A"
        print(f"  Result: {hotel['currency']}{new_price:.2f} (was {old_str})")

        if old_price is None or old_price <= 0:
            hotel["last_price"] = new_price
            changed = True
            send_telegram(
                f"🔍 <b>Price Baseline Set</b>\n"
                f"🏨 {hotel['name']}\n"
                f"🛏️ {hotel['room_name']}\n"
                f"💵 {hotel['currency']}{new_price:.2f}\n"
                f"🔗 <a href='{hotel['url']}'>View on Agoda</a>"
            )
            print(f"  Initial price set: {hotel['currency']}{new_price:.2f}")
        elif new_price < old_price:
            drop_pct = (old_price - new_price) / old_price * 100
            send_telegram(
                f"💰 <b>Price Drop!</b>\n"
                f"🏨 {hotel['name']}\n"
                f"🛏️ {hotel['room_name']}\n"
                f"📉 {hotel['currency']}{old_price:.2f} → {hotel['currency']}{new_price:.2f}\n"
                f"💵 Save: {hotel['currency']}{old_price - new_price:.2f} ({drop_pct:.1f}%)\n"
                f"🔗 <a href='{hotel['url']}'>View on Agoda</a>"
            )
            hotel["last_price"] = new_price
            changed = True
        elif new_price > old_price:
            print(f"  Price up to {hotel['currency']}{new_price:.2f}, ignoring (stored low: {hotel['currency']}{old_price:.2f})")
        else:
            print(f"  Price unchanged: {hotel['currency']}{new_price:.2f}")

    if changed:
        with open(PRICES_FILE, "w") as f:
            json.dump(hotels, f, indent=2)
        print("prices.json saved")
    else:
        print("No changes to save")


if __name__ == "__main__":
    main()
