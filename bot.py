import logging
import re
from typing import Dict, Set
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from config import BOT_TOKEN, CHECK_INTERVAL, MAX_ITEMS_PER_USER, PRICE_CHANGE_THRESHOLD
from avito_api import AvitoAPI

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Хранилище данных пользователей
user_items: Dict[int, Dict[str, float]] = {}  # user_id -> {item_id: last_price}
user_searches: Dict[int, Set[str]] = {}  # user_id -> set of search queries

class AvitoBot:
    def __init__(self):
        self.api = AvitoAPI()

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Обработчик команды /start"""
        await update.message.reply_text(
            "Привет! Я бот для мониторинга объявлений на Авито.\n"
            "Вы можете отправить мне:\n"
            "1. ID объявления для отслеживания цены\n"
            "2. Поисковый запрос для мониторинга новых объявлений\n\n"
            "Доступные команды:\n"
            "/search - поиск объявлений\n"
            "/list - показать отслеживаемые объявления\n"
            "/remove - удалить объявление из отслеживания\n"
            "/help - показать справку"
        )

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Обработчик команды /help"""
        await update.message.reply_text(
            "Как использовать бота:\n"
            "1. Отправьте ID объявления для отслеживания цены\n"
            "2. Используйте /search для поиска объявлений\n"
            "3. Бот будет уведомлять вас об изменениях\n\n"
            "Команды:\n"
            "/search - поиск объявлений\n"
            "/list - показать отслеживаемые объявления\n"
            "/remove - удалить объявление из отслеживания\n"
            "/help - показать это сообщение"
        )

    async def search_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Обработчик команды /search"""
        await update.message.reply_text(
            "Введите параметры поиска в формате:\n"
            "Запрос | Категория | Город | Цена от | Цена до\n\n"
            "Например: iPhone 13 | Электроника | Москва | 50000 | 80000\n"
            "Или просто введите поисковый запрос"
        )

    async def list_items(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Показать список отслеживаемых объявлений"""
        user_id = update.effective_user.id
        if user_id not in user_items or not user_items[user_id]:
            await update.message.reply_text("У вас нет отслеживаемых объявлений.")
            return

        message = "Ваши отслеживаемые объявления:\n\n"
        for i, (item_id, price) in enumerate(user_items[user_id].items(), 1):
            message += f"{i}. ID: {item_id} - Последняя цена: {price:,.2f} ₽\n"
        
        await update.message.reply_text(message)

    async def remove_item(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Удалить объявление из отслеживания"""
        user_id = update.effective_user.id
        if user_id not in user_items or not user_items[user_id]:
            await update.message.reply_text("У вас нет отслеживаемых объявлений.")
            return

        items = list(user_items[user_id].items())
        message = "Выберите номер объявления для удаления:\n\n"
        for i, (item_id, price) in enumerate(items, 1):
            message += f"{i}. ID: {item_id} - Цена: {price:,.2f} ₽\n"
        
        await update.message.reply_text(message)

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Обработчик входящих сообщений"""
        text = update.message.text
        user_id = update.effective_user.id

        # Обработка номера для удаления объявления
        if text.isdigit():
            if user_id in user_items and user_items[user_id]:
                try:
                    index = int(text) - 1
                    items = list(user_items[user_id].items())
                    if 0 <= index < len(items):
                        item_id, _ = items[index]
                        del user_items[user_id][item_id]
                        await update.message.reply_text(f"Объявление {item_id} удалено из отслеживания")
                    else:
                        await update.message.reply_text("Неверный номер объявления")
                except Exception as e:
                    logger.error(f"Error removing item: {e}")
                    await update.message.reply_text("Произошла ошибка при удалении объявления")
            return

        # Обработка поискового запроса
        if "|" in text:
            # Парсинг параметров поиска
            params = [p.strip() for p in text.split("|")]
            try:
                search_query = params[0]
                category = params[1] if len(params) > 1 else None
                location = params[2] if len(params) > 2 else None
                price_from = int(params[3]) if len(params) > 3 else None
                price_to = int(params[4]) if len(params) > 4 else None

                await self.search_items(update, search_query, category, location, price_from, price_to)
            except Exception as e:
                logger.error(f"Error processing search query: {e}")
                await update.message.reply_text(
                    "Ошибка при обработке запроса. Убедитесь, что формат корректен:\n"
                    "Запрос | Категория | Город | Цена от | Цена до"
                )
            return

        # Обработка ID объявления
        if re.match(r'^\d+$', text):
            if user_id not in user_items:
                user_items[user_id] = {}

            if len(user_items[user_id]) >= MAX_ITEMS_PER_USER:
                await update.message.reply_text(
                    f"Достигнут лимит отслеживаемых объявлений ({MAX_ITEMS_PER_USER}). "
                    "Удалите некоторые объявления с помощью команды /remove"
                )
                return

            try:
                item_details = await self.api.get_item_details(text)
                if item_details:
                    price = float(item_details.get('price', 0))
                    user_items[user_id][text] = price
                    await update.message.reply_text(
                        f"✅ Объявление добавлено в отслеживание!\n"
                        f"💰 Текущая цена: {price:,.2f} ₽\n"
                        f"🔄 Проверка цены каждые {CHECK_INTERVAL // 60} минут"
                    )
                else:
                    await update.message.reply_text("Объявление не найдено")
            except Exception as e:
                logger.error(f"Error adding item: {e}")
                await update.message.reply_text("Ошибка при добавлении объявления")

    async def search_items(
        self,
        update: Update,
        query: str,
        category: Optional[str] = None,
        location: Optional[str] = None,
        price_from: Optional[int] = None,
        price_to: Optional[int] = None
    ) -> None:
        """Поиск объявлений по параметрам"""
        try:
            # Получаем ID категории если указана
            category_id = None
            if category:
                categories = await self.api.get_categories()
                category_id = next(
                    (cat['id'] for cat in categories if cat['name'].lower() == category.lower()),
                    None
                )

            # Получаем ID локации если указана
            location_id = None
            if location:
                locations = await self.api.get_locations(location)
                location_id = next(
                    (loc['id'] for loc in locations if loc['name'].lower() == location.lower()),
                    None
                )

            # Выполняем поиск
            results = await self.api.search_items(
                category_id=category_id,
                location_id=location_id,
                search_query=query,
                price_from=price_from,
                price_to=price_to
            )

            if not results.get('items'):
                await update.message.reply_text("По вашему запросу ничего не найдено")
                return

            # Формируем сообщение с результатами
            message = "Результаты поиска:\n\n"
            for item in results['items'][:5]:  # Показываем только первые 5 результатов
                price = float(item.get('price', 0))
                message += (
                    f"📌 {item['title']}\n"
                    f"💰 Цена: {price:,.2f} ₽\n"
                    f"📍 {item.get('location', 'Не указано')}\n"
                    f"🔗 ID: {item['id']}\n\n"
                )

            message += "\nЧтобы отслеживать объявление, отправьте его ID"
            await update.message.reply_text(message)

        except Exception as e:
            logger.error(f"Error searching items: {e}")
            await update.message.reply_text("Произошла ошибка при поиске объявлений")

    async def check_prices(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Периодическая проверка цен объявлений"""
        for user_id, items in user_items.items():
            for item_id, last_price in items.copy().items():
                try:
                    item_details = await self.api.get_item_details(item_id)
                    if not item_details:
                        # Объявление больше не доступно
                        del items[item_id]
                        await context.bot.send_message(
                            chat_id=user_id,
                            text=f"❌ Объявление {item_id} больше не доступно и удалено из отслеживания"
                        )
                        continue

                    current_price = float(item_details.get('price', 0))
                    if current_price != last_price:
                        price_change = ((current_price - last_price) / last_price) * 100
                        if abs(price_change) >= PRICE_CHANGE_THRESHOLD:
                            direction = "выросла" if current_price > last_price else "снизилась"
                            await context.bot.send_message(
                                chat_id=user_id,
                                text=(
                                    f"🚨 Изменение цены в объявлении!\n"
                                    f"ID: {item_id}\n"
                                    f"Название: {item_details.get('title', 'Не указано')}\n"
                                    f"Цена {direction} на {abs(price_change):.2f}%\n"
                                    f"С {last_price:,.2f} ₽ до {current_price:,.2f} ₽"
                                )
                            )
                            items[item_id] = current_price

                except Exception as e:
                    logger.error(f"Error checking price for item {item_id}: {e}")

def main() -> None:
    """Запуск бота"""
    avito_bot = AvitoBot()
    application = Application.builder().token(BOT_TOKEN).build()

    # Добавляем обработчики команд
    application.add_handler(CommandHandler("start", avito_bot.start))
    application.add_handler(CommandHandler("help", avito_bot.help_command))
    application.add_handler(CommandHandler("search", avito_bot.search_command))
    application.add_handler(CommandHandler("list", avito_bot.list_items))
    application.add_handler(CommandHandler("remove", avito_bot.remove_item))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, avito_bot.handle_message))

    # Добавляем периодическую задачу проверки цен
    job_queue = application.job_queue
    job_queue.run_repeating(avito_bot.check_prices, interval=CHECK_INTERVAL, first=10)

    # Запускаем бота
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main() 