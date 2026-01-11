import re
import json
import asyncio
import pandas as pd
from playwright.async_api import async_playwright

# фильтры поиска
CONFIG = {
    'page': 1, # Первоначальная страница
    'min_price': 0, # Минимальная цена из фильтров
    'max_price': 10000, # Максимальная цена из фильтров
    'search_product': 'пальто из натуральной шерсти', # Поисковый запрос
    'min_rating': 4.5, # Минимальный рейтинг товара
    'RUS': 'f14177451=15000203' # Код производителя Россия
}


def get_target_url(search_product, page, min_price, max_price, rus_param):
    """Получаем URL для поиска товаров."""
    search_product = re.sub(r'\s+', '+', search_product.strip())
    target_url = (
        f'https://www.wildberries.ru/catalog/0/search.aspx?page={page}&sort=popular&search={search_product}&priceU={int(min_price)}00%3B{int(max_price)}00&{rus_param}'
    )
    return target_url


async def create_browser(playwright_obj):
    """Создает и возвращает браузер, контекст и страницу."""
    browser = await playwright_obj.chromium.launch(
        # headless=False,
        args=[
            "--headless=new",
            "--disable-blink-features=AutomationControlled",
            "--start-maximized"
        ],
    )
    context = await browser.new_context(
        user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
        viewport={"width": 1920, "height": 1080},
    )
    page_obj = await context.new_page()
    return browser, context, page_obj


async def get_max_pages(page_obj, search_product, min_price, max_price, rus_param):
    """Получаем количество страниц в пагинации."""
    url = get_target_url(search_product, 1, min_price, max_price, rus_param)
    await page_obj.goto(url, wait_until='domcontentloaded', timeout=30000)
    await page_obj.wait_for_timeout(2000)

    pagination_items = page_obj.locator('.pagination-item.j-page')
    count = await pagination_items.count() # считаем количество элементов пагинации
    if count == 0:
        return 1
    last_page = int((await pagination_items.nth(count - 1).text_content()).strip())

    url = get_target_url(search_product, last_page, min_price, max_price, rus_param)
    await page_obj.goto(url, wait_until='domcontentloaded', timeout=30000)
    await page_obj.wait_for_timeout(2000)

    pagination_items = page_obj.locator('.pagination-item.j-page')
    count = await pagination_items.count() # считаем количество элементов пагинации уже на последней странице
    if count > 0:
        last_visible = await pagination_items.nth(count - 1).text_content()
        max_page = int(last_visible.strip())
        return max(max_page, last_page)
    return last_page


async def parse_products():
    """Парсим товары с Wildberries."""
    config = CONFIG
    async with async_playwright() as p:
        browser, context, page_obj = await create_browser(p)

        api_responses = []

        async def handle_route(route):
            """Обрабатываем маршруты для поиска товаров."""
            url = route.request.url
            if '/u-search/exactmatch/' in url and '/search?' in url:
                response = await route.fetch()
                if response.ok:
                    data = await response.json()
                    api_responses.append(data)
                    print(f'Сохранен ответ API, всего ответов: {len(api_responses)}')
            await route.continue_()

        await page_obj.route('**/*', handle_route)

        max_pages = await get_max_pages(page_obj, config['search_product'], 
                                       config['min_price'], config['max_price'], 
                                       config['RUS'])
        print(f'Найдено страниц: {max_pages}')

        for page_num in range(1, max_pages + 1):
            print(f'Обрабатываю страницу {page_num}/{max_pages}')
            url = get_target_url(config['search_product'], page_num, 
                               config['min_price'], config['max_price'], 
                               config['RUS'])
            try:
                await page_obj.goto(url, wait_until='domcontentloaded', timeout=30000)
                await page_obj.wait_for_timeout(2000)
            except Exception as e:
                print(f'Ошибка при загрузке страницы {page_num}: {e}')
                continue

        all_products = []
        for response in api_responses:
            products = response.get('products', [])
            if products:
                all_products.extend(products)
                print(f'Найдено товаров в ответе: {len(products)}')

        print(f'Всего товаров собрано: {len(all_products)}')

        filtered_products = []
        for product in all_products:
            if product.get('reviewRating', 0) >= config['min_rating']:
                filtered_products.append(product)
        print(f'Найдено товаров с рейтингом >= {config["min_rating"]}: {len(filtered_products)}')

        # filtered_products = filtered_products[:10]
        # print(f'Будет обработано товаров: {len(filtered_products)}')

        results = []

        card_responses = {}

        async def handle_card_route(route):
            """Перехватываем запросы к API карточки товара."""
            if '/info/ru/card.json' in route.request.url:
                response = await route.fetch()
                if response.ok:
                    try:
                        data = await response.json()
                        nm_id = data.get('nm_id')
                        if nm_id:
                            card_responses[nm_id] = data
                    except Exception as e:
                        print(f'Ошибка при обработке карточки: {e}')
            await route.continue_()

        await page_obj.route('**/info/ru/card.json', handle_card_route)

        print(f'Обрабатываю {len(filtered_products)} товаров')

        for idx, product in enumerate(filtered_products, 1):
            print(f'Обрабатываю товар {idx}/{len(filtered_products)}: {product.get("name", "N/A")}')

            product_id = product.get('id')
            if not product_id:
                continue

            product_url = f'https://www.wildberries.ru/catalog/{product_id}/detail.aspx'

            try:
                await page_obj.goto(product_url, wait_until='domcontentloaded', timeout=30000)
                await page_obj.wait_for_timeout(3000)

                card_data = card_responses.get(product_id, {})
                if not card_data:
                    await page_obj.wait_for_timeout(2000)
                    card_data = card_responses.get(product_id, {})

                description = card_data.get('description', '')
                options = card_data.get('options', [])

                images = []
                try:
                    img_elements = page_obj.locator('.swiper-slide.mainSlide--TIHn4 img')
                    img_count = await img_elements.count()
                    if img_count > 0:
                        for i in range(img_count):
                            src = await img_elements.nth(i).get_attribute('src')
                            if src:
                                if src.startswith('//'):
                                    src = 'https:' + src
                                elif src.startswith('/'):
                                    src = 'https://www.wildberries.ru' + src
                                if src not in images:
                                    images.append(src)
                        # print(f'Найдено {len(images)} изображений')
                except Exception as e:
                    print(f'Ошибка: {e}')
                
                if not images: # если изображениz не успели прогрузиться, пытаемся получить их еще раз через 2 секунды
                    await page_obj.wait_for_timeout(2000)
                    try:
                        img_elements = page_obj.locator('.swiper-slide.mainSlide--TIHn4 img')
                        img_count = await img_elements.count()
                        # print(f'Найдено {img_count} изображений')
                        if img_count > 0:
                            for i in range(img_count):
                                src = await img_elements.nth(i).get_attribute('src')
                                if src:
                                    if src.startswith('//'):
                                        src = 'https:' + src
                                    elif src.startswith('/'):
                                        src = 'https://www.wildberries.ru' + src
                                    if src not in images:
                                        images.append(src)
                            # if images:
                                # print(f'Найдено {len(images)} изображений (повторная попытка)')
                    except Exception as e:
                        print(f'Ошибка при повторной попытке: {e}')
            except Exception as e:
                print(f'Ошибка при обработке товара {product_id}: {e}')
                images = []
                description = ''
                options = []

            product_sizes = product.get('sizes', [])
            sizes = []
            for size in product_sizes:
                size_name = size.get('name', '')
                if size_name:
                    sizes.append(size_name)
            sizes_str = ', '.join(sizes)

            actual_price = 0
            if product_sizes:
                first_size = product_sizes[0]
                price_info = first_size.get('price', {})
                if price_info:
                    price_basic = price_info.get('basic', 0) / 100
                    price_product = price_info.get('product', 0) / 100
                    if price_product > 0 and price_product < price_basic:
                        actual_price = price_product
                    else:
                        actual_price = price_basic

            characteristics_json = json.dumps(options, ensure_ascii=False) if options else ''

            result = {
                'Ссылка на товар': product_url,
                'Артикул': product_id,
                'Название': product.get('name', ''),
                'Цена': actual_price,
                'Описание': description,
                'Ссылки на изображения': ', '.join(images),
                'Характеристики': characteristics_json,
                'Название селлера': product.get('supplier', ''),
                'Ссылка на селлера': f'https://www.wildberries.ru/seller/{product.get("supplierId", "")}',
                'Размеры': sizes_str,
                'Остатки': product.get('totalQuantity', 0),
                'Рейтинг': product.get('reviewRating', 0),
                'Количество отзывов': product.get('feedbacks', 0)
            }
            results.append(result)

        await browser.close()

        df = pd.DataFrame(results)
        df.to_excel('wildberries_products.xlsx', index=False)
        print(f'Сохранено {len(results)} товаров в wildberries_products.xlsx')


if __name__ == '__main__':
    asyncio.run(parse_products())
