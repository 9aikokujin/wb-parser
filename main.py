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

            product_url_raw = f'https://www.wildberries.ru/catalog/{product_id}/detail.aspx'

            try:
                await page_obj.goto(product_url_raw, wait_until='domcontentloaded', timeout=30000)
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

            description_value = (
                description.strip() if isinstance(description, str) else ''
            )
            description_value = description_value if description_value else 'Описание отсутствует'

            images_str = ', '.join(images) if images else 'Изображения отсутствуют'

            characteristics_json = (
                json.dumps(options, ensure_ascii=False) if options else ''
            )
            characteristics_json = (
                characteristics_json if characteristics_json else 'Характеристики отсутствуют'
            )

            seller_name_raw = product.get('supplier', '')
            if isinstance(seller_name_raw, str):
                seller_name_raw = seller_name_raw.strip()
            seller_name = seller_name_raw if seller_name_raw else 'Название селлера отсутствует'

            supplier_id = product.get('supplierId')
            seller_link_raw = f'https://www.wildberries.ru/seller/{supplier_id}' if supplier_id else ''
            seller_link = seller_link_raw if seller_link_raw else 'Ссылка на селлера отсутствует'

            sizes_str = ', '.join(sizes) if sizes else 'Размеры отсутствуют'

            total_quantity_raw = product.get('totalQuantity')
            total_quantity = (
                total_quantity_raw if total_quantity_raw not in (None, '') else 'Остатки отсутствуют'
            )

            rating_raw = product.get('reviewRating')
            rating = rating_raw if rating_raw not in (None, '') else 'Рейтинг отсутствует'

            feedbacks_raw = product.get('feedbacks')
            feedbacks = feedbacks_raw if feedbacks_raw not in (None, '') else 'Количество отзывов отсутствует'

            product_url = product_url_raw if product_url_raw else 'Ссылка на товар отсутствует'
            product_id_value = product_id if product_id else 'Артикул отсутствует'

            product_name_raw = product.get('name', '')
            if isinstance(product_name_raw, str):
                product_name_raw = product_name_raw.strip()
            product_name = product_name_raw if product_name_raw else 'Название отсутствует'

            actual_price_value = actual_price if actual_price > 0 else 'Цена отсутствует'

            result = {
                'Ссылка на товар': product_url,
                'Артикул': product_id_value,
                'Название': product_name,
                'Цена': actual_price_value,
                'Описание': description_value,
                'Ссылки на изображения': images_str,
                'Характеристики': characteristics_json,
                'Название селлера': seller_name,
                'Ссылка на селлера': seller_link,
                'Размеры': sizes_str,
                'Остатки': total_quantity,
                'Рейтинг': rating,
                'Количество отзывов': feedbacks
            }
            results.append(result)

        await browser.close()

        df = pd.DataFrame(results)
        df.to_excel('wildberries_products.xlsx', index=False)
        print(f'Сохранено {len(results)} товаров в wildberries_products.xlsx')


if __name__ == '__main__':
    asyncio.run(parse_products())
