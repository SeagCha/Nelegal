import os
import telebot
import json
import time
import re
from telebot import types
from openai import OpenAI  # Убедитесь, что у вас установлен пакет OpenAI
import logging

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

os.environ['HTTP_PROXY'] = "http://127.0.0.1:2080"
os.environ['HTTPS_PROXY'] = "http://127.0.0.1:2080"

# Функция загрузки конфигурации из config.json
def load_config():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(script_dir, "config.json")
    logger.info(f"Пытаюсь загрузить конфигурацию из: {config_path}")
    if os.path.exists(config_path):
        with open(config_path, "r", encoding='utf-8') as config_file:
            try:
                config = json.load(config_file)
                logger.info("Конфигурация успешно загружена.")
                return config
            except json.JSONDecodeError as e:
                logger.error(f"Ошибка при разборе config.json: {e}")
                return {}
    else:
        logger.error("Файл config.json не найден.")
        return {}

# Функция сохранения конфигурации в config.json
def save_config(config):
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(script_dir, "config.json")
    with open(config_path, "w", encoding='utf-8') as config_file:
        json.dump(config, config_file, indent=4, ensure_ascii=False)
    logger.info("Конфигурация сохранена.")

# Загрузка конфигурации
config = load_config()

# Проверка наличия необходимых ключей в конфигурации
missing_keys = []
if "TELEGRAM_BOT_TOKEN" not in config:
    missing_keys.append("TELEGRAM_BOT_TOKEN")
if "OPENAI_API_KEY" not in config:
    missing_keys.append("OPENAI_API_KEY")

if missing_keys:
    raise KeyError(f"Отсутствуют ключи в config.json: {', '.join(missing_keys)}")

# Извлечение токенов из конфигурации
TELEGRAM_BOT_TOKEN = config["TELEGRAM_BOT_TOKEN"]
OPENAI_API_KEY = config["OPENAI_API_KEY"]

# Настройка OpenAI API ключа
client = OpenAI(api_key=OPENAI_API_KEY)

# Инициализация бота
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

# Хранение данных для каждого пользователя
user_data = {}

# Регулярные выражения для поиска ссылок
URL_REGEX = re.compile(r'(https?://[^\s]+)')
POST_LINK_REGEX = re.compile(r'(https?://tg\.c/[^\s]+)')

# Класс для хранения состояния пользователя
class User:
    def __init__(self, user_id):
        self.user_id = user_id
        self.mode = 'main'
        self.history_for_gpt_mode = []
        self.info_message = []
        self.current_info_message = {
            'user_text': None,
            'forwarded_text': 'Неизвестно',
            'link': 'Неизвестно',
            'timestamp': None
        }

    def to_dict(self):
        return {
            'user_id': self.user_id,
            'mode': self.mode,
            'info_message': self.info_message
        }

    @staticmethod
    def from_dict(data):
        user = User(data['user_id'])
        user.mode = data.get('mode', 'main')
        user.info_message = data.get('info_message', [])
        return user

# Функция загрузки данных пользователей из конфигурации
def load_user_data():
    if "user_data" in config:
        for user_id_str, user_info in config["user_data"].items():
            try:
                user_id = int(user_id_str)
                user = User.from_dict(user_info)
                user_data[user_id] = user
                logger.info(f"Загружены данные для пользователя ID: {user_id}")
            except ValueError:
                logger.error(f"Неверный user_id: {user_id_str}")
    else:
        config["user_data"] = {}
    logger.info("Данные пользователей загружены.")

# Функция сохранения данных пользователей в конфигурацию
def save_user_data():
    config["user_data"] = {str(user_id): user.to_dict() for user_id, user in user_data.items()}
    save_config(config)
    logger.info("Данные пользователей сохранены.")

load_user_data()

# Создание клавиатуры главного меню
main_menu_keyboard = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
button_add_info = types.KeyboardButton('Add Info')
button_ask_gpt = types.KeyboardButton('Ask GPT')
main_menu_keyboard.add(button_add_info, button_ask_gpt)

# Создание клавиатуры для выхода в главное меню
exit_keyboard = types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=True)
exit_button = types.KeyboardButton('Exit to main menu')
exit_keyboard.add(exit_button)

# Создание клавиатуры для режима сбора информации
info_keyboard = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
show_info_button = types.KeyboardButton('Show Info')
clear_info_button = types.KeyboardButton('Clear Info')  # Добавлена кнопка 'Clear Info'
info_keyboard.add(show_info_button, clear_info_button, exit_button)


# Функции для извлечения ссылок
def extract_links(text):
    return URL_REGEX.findall(text)

def extract_post_links(text):
    return POST_LINK_REGEX.findall(text)

# Функция для конвертации гиперссылок в формат 'слово (ссылка)'
def convert_links(text):
    text = re.sub(r'<a href="([^"]+)">([^<]+)</a>', r'\2 (\1)', text)
    return text

# Функция для структурирования сохранённой информации
def structuring_function(info):
    structured_text = ""
    for entry in info:
        if isinstance(entry, dict):
            user_text = entry.get('user_text', 'Неизвестно')
            forwarded_text = entry.get('forwarded_text', 'Неизвестно')
            link = entry.get('link', 'Неизвестно')
        elif isinstance(entry, (list, tuple)):
            try:
                user_text, forwarded_text, link = entry
            except ValueError:
                user_text, forwarded_text, link = "Неизвестно", "Неизвестно", "Неизвестно"
        else:
            user_text, forwarded_text, link = "Неизвестно", "Неизвестно", "Неизвестно"
        
        structured_text += f"1. Мой текст:\n{user_text}\n"
        structured_text += f"2. Текст пересылаемого поста:\n{forwarded_text}\n"
        structured_text += f"3. Ссылка на пост:\n{link}\n\n"
    
    return structured_text.strip()

# Функция для запроса в режиме 'info'
def request_info_mode(user, user_message):
    history_openai_format = [
        {
            "role": "user",
            "content": "Привет, твоя задача помогать мне структурировать, анализировать и форматировать получаемую информацию. Я буду присылать тебе инофрмацию запросов вот такого формата:  1. Мой текст: Текст моего сообщения, проанализировав который ты поймешь, что нужно с ним сделать. Добавить в Календарь, Напоминание, Заметки, другую информацию или что-то еще. 2. Текст пересылаемого поста: Неизвестно  ( это означает что поста нет ) 3. Ссылка на пост: Неизвестно  ( так как поста нет, то нет и ссылки ) 1. Мой текст: Мое сообщение связанное с потом, например я хочу вечером поставить эти видео себе на обои. 2. Текст пересылаемого поста: (если текста нет, то просто ссылка) 3. Ссылка на пост: https://t.me/c/2107490410/2732 1. Мой текст: Неизвестно  ( моего сообщений нет, значит я хочу просто получить информацию о посте. попробуй кратко изложить о чем этот пост, а также понять для чего я тебе его прислал) 2. Текст пересылаемого поста: Apple AirPods 2 13900 > от 7800 https://fas.st/3BG5c4?erid=25H8d7vbP8SRTvJ4Q27doN 3. Ссылка на пост: https://t.me/c/1785748423/2313  Формать вывода должен быть следующим:  ❗️ Важное ❗️ ➕ текст важных сообщений, которые я помечаю словом важно 🔔 Напоминания ➕Дата, время, действие ➖ ( отмена напоминания)  событие 📅 Календарь ➕ Дата 07:00, 21 числа, тип календаря [Учеба,Рабочий,Домашние дела,Ученики,События,Зал,Дела], у Яузы с кентами ( можно перефразировать) ➕ 17:00, завтра ( дату напиши) , Рабочий , совещание 🗒 Заметки ➕ папка [[Заметки,Документы,Проекты,Жизнь,Работа] если пишется проекто то я указываю название и ты тоже указывай в красивом формате, тоже самое с Работой. - заметка фильмы: «Атака Титанов: Последняя Атака» выйдет в российских кинотеатрах, мировая премьера 8 ноября. ➕ Проекты nelegal - хочу изменить бота, информация по ссылке: [нет доступа к материалу] ( ссылку все равно присылай даже если нет доступа к ней) (нужен смайлик) Другая информация ➕ Я хочу почитать книгу вечером ➕ нужно начать ходит в зал 📎 Информация связанная с постами --- (сний кружок смайлик) Мое сообщение на тему поста (смайлик связанный с темой поста) Заголовок поста, который ты сам напишешь проанализируя пост Ссылка: (https://t.me/c/1103688715/20974) --- (сний кружок смайлик) Мое сообщение на тему поста (Нет текста или нельзя проанализировать пост, просто ссылка идет) (красный кружок смайлик)Ссылка: (https://t.me/c/2192407202/2794) --- (Нет текста или нельзя проанализировать пост, просто ссылка идет) (красный кружок смайлик)Ссылка: (https://t.me/c/2192407202/2794)  Разделители и оформление можешь придумать сам. После того как ты получишь это сообщение, ответь лишь Готов к работе. и тогда я начну присылать информацию, на которую ты всегда отвечаешь на основе полученной информации. Если информация связанная с потом идет в заметки, указывай сразу в заметках пост, а снизу не указывай."
        }
    ]
    
    history_openai_format.append({"role": "assistant", "content": "Готов к работе."})
    history_openai_format.append({"role": "user", "content": user_message})
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=history_openai_format,
        temperature=0.8
    )
    return response.choices[0].message.content

# Функция для запроса в режиме 'gpt'
def request_gpt_mode(user, user_message):
    history_openai_format = user.history_for_gpt_mode.copy()
    history_openai_format.append({"role": "user", "content": user_message})

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=history_openai_format,
        temperature=0.8
    )
    assistant_message = response.choices[0].message.content

    history_openai_format.append({"role": "assistant", "content": assistant_message})
    user.history_for_gpt_mode = history_openai_format

    return assistant_message

# Обновлённая функция для генерации ссылки на исходное сообщение
def get_message_link(message):
    """
    Генерирует ссылку на исходное сообщение из канала или группы, откуда было переслано сообщение.
    Использует forward_from_chat.id (начинается с -100) и forward_from_message_id.
    Предполагается, что все сообщения, обрабатываемые в режиме 'info', являются пересланными из каналов или групп.
    """
    if not message.forward_from_chat or not message.forward_from_message_id:
        # Сообщение не переслано из канала или группы
        logger.info("Сообщение не переслано из канала или группы.")
        return None

    original_chat_id = message.forward_from_chat.id
    original_message_id = message.forward_from_message_id

    # Удаляем префикс '-100' из chat_id
    chat_id_str = str(original_chat_id)[4:]

    # Генерируем ссылку
    return f"https://t.me/c/{chat_id_str}/{original_message_id}"

# Обработчик команды /start
@bot.message_handler(commands=["start"])
def start(message):
    user_id = message.from_user.id
    if user_id not in user_data:
        user_data[user_id] = User(user_id)
        logger.info(f"Создан новый пользователь с ID: {user_id}")
    bot.send_message(
        message.chat.id,
        text=f"Привет, {message.from_user.first_name}! Меня зовут nelegal.",
        reply_markup=main_menu_keyboard
    )

# Основной обработчик сообщений
@bot.message_handler(func=lambda message: True, content_types=['text', 'photo', 'video', 'document', 'audio', 'voice', 'sticker', 'video_note'])
def handle_messages(message):
    user_id = message.from_user.id

    # Инициализируем данные пользователя, если их нет
    if user_id not in user_data:
        user_data[user_id] = User(user_id)
        logger.info(f"Создан новый пользователь с ID: {user_id}")

    user = user_data[user_id]

    logger.info(f"Текущее info_message: {user.info_message}") 
    logger.info(f"Текущее current_info_message: {user.current_info_message}")

    # Режим работы бота
    if user.mode == 'main':
        if message.text == 'Add Info':
            user.mode = 'info'
            bot.send_message(
                message.chat.id,
                text="Вы вошли в режим добавления информации. Все ваши сообщения будут сохранены.",
                reply_markup=exit_keyboard
            )
            logger.info(f"Пользователь {user_id} переключился в режим 'info'")
        elif message.text == 'Ask GPT':
            user.mode = 'gpt'
            user.history_for_gpt_mode = []  # Инициализируем историю GPT
            bot.send_message(
                message.chat.id,
                text="Вы вошли в режим общения с GPT. Задавайте свои вопросы.",
                reply_markup=exit_keyboard
            )
            logger.info(f"Пользователь {user_id} переключился в режим 'gpt'")
        else:
            bot.send_message(
                message.chat.id,
                text="Пожалуйста, выберите одну из опций меню.",
                reply_markup=main_menu_keyboard
            )
            logger.info(f"Пользователь {user_id} выбрал неизвестную опцию в режиме 'main'")

    elif user.mode == 'info':
        now = time.time()
        time_window = 1  # Время в секундах для сброса текущего сообщения

        # Проверка временного окна для группировки сообщений
        if user.current_info_message['timestamp'] is not None and (now - user.current_info_message['timestamp'] > time_window):
            if user.current_info_message['user_text'] or user.current_info_message['forwarded_text'] != 'Неизвестно':
                user.info_message.append({
                    'user_text': user.current_info_message['user_text'] if user.current_info_message['user_text'] else 'Неизвестно',
                    'forwarded_text': user.current_info_message['forwarded_text'],
                    'link': user.current_info_message['link']
                })
                logger.info(f"Добавлена запись в info_message для пользователя {user_id}")
            user.current_info_message = {
                'user_text': None,
                'forwarded_text': 'Неизвестно',
                'link': 'Неизвестно',
                'timestamp': None
            }

        user.current_info_message['timestamp'] = now

        if message.text == 'Exit to main menu':
            # Обработка выхода в главное меню
            if user.current_info_message['user_text'] or user.current_info_message['forwarded_text'] != 'Неизвестно':
                user.info_message.append({
                    'user_text': user.current_info_message['user_text'] if user.current_info_message['user_text'] else 'Неизвестно',
                    'forwarded_text': user.current_info_message['forwarded_text'],
                    'link': user.current_info_message['link']
                })
                logger.info(f"Добавлена запись в info_message при выходе для пользователя {user_id}")
            user.current_info_message = {
                'user_text': None,
                'forwarded_text': 'Неизвестно',
                'link': 'Неизвестно',
                'timestamp': None
            }

            user.mode = 'main'
            bot.send_message(
                message.chat.id,
                text="Вы вышли из режима добавления информации.",
                reply_markup=main_menu_keyboard
            )
            logger.info(f"Пользователь {user_id} вышел из режима 'info' в 'main'")
            # Сохраняем данные пользователя в config.json
            save_user_data()

        elif message.text == 'Show Info':
            # Обработка команды 'Show Info'
            if user.current_info_message['user_text'] or user.current_info_message['forwarded_text'] != 'Неизвестно':
                user.info_message.append({
                    'user_text': user.current_info_message['user_text'] if user.current_info_message['user_text'] else 'Неизвестно',
                    'forwarded_text': user.current_info_message['forwarded_text'],
                    'link': user.current_info_message['link']
                })
                logger.info(f"Добавлена запись в info_message перед показом информации для пользователя {user_id}")
            user.current_info_message = {
                'user_text': None,
                'forwarded_text': 'Неизвестно',
                'link': 'Неизвестно',
                'timestamp': None
            }

            if not user.info_message:
                bot.send_message(
                    message.chat.id,
                    text="Нет сохраненной информации.",
                    reply_markup=info_keyboard
                )
                logger.info(f"Пользователь {user_id} запросил показ информации, но список пуст.")
            else:
                info = structuring_function(user.info_message)
                # Преобразуем гиперссылки в формат 'слово (ссылка)'
                info_with_links = convert_links(info)
                answer = request_info_mode(user, info_with_links)
                bot.send_message(message.chat.id, answer)
                # Оставляем info_message после показа
                bot.send_message(
                    message.chat.id,
                    text="Информация показана. Введите следующее сообщение или нажмите 'Exit to main menu'.",
                    reply_markup=info_keyboard
                )
                logger.info(f"Пользователь {user_id} запросил показ информации.")
            # Сохраняем данные пользователя в config.json
            save_user_data()

        elif message.text == 'Clear Info':
            # Обработка команды 'Clear Info'
            if user.info_message:
                user.info_message = user.info_message[:1]  # Оставляем только первое сообщение
                # Сохраняем данные пользователя в config.json
                save_user_data()
                bot.send_message(
                    message.chat.id,
                    text="История очищена, оставлено только первое сообщение.",
                    reply_markup=info_keyboard
                )
                logger.info(f"Пользователь {user_id} очистил историю 'info_message', оставив только первое сообщение.")
            else:
                bot.send_message(
                    message.chat.id,
                    text="Нет сохраненной информации для очистки.",
                    reply_markup=info_keyboard
                )
                logger.info(f"Пользователь {user_id} попытался очистить историю, но 'info_message' пуст.")

        else:
            # Обрабатываем сообщения и группируем их
            if message.content_type == 'text':
                if message.forward_from or message.forward_from_chat:
                    # Обработка пересланного сообщения
                    forwarded_text = message.text.strip()
                    user.current_info_message['forwarded_text'] = forwarded_text
                    logger.info(f"Сохранён текст пересланного сообщения для пользователя {user_id}: {forwarded_text}")
                    # Генерация ссылки
                    link = get_message_link(message)
                    if link:
                        user.current_info_message['link'] = link
                        logger.info(f"Сгенерирована ссылка: {link} для пользователя {user_id}")
                    else:
                        user.current_info_message['link'] = "Неизвестно"
                        logger.info(f"Не удалось сгенерировать ссылку для пересланного сообщения у пользователя {user_id}")
                else:
                    # Обработка собственного сообщения пользователя
                    text = message.text.strip()
                    user.current_info_message['user_text'] = text
                    logger.info(f"Сохранён мой текст для пользователя {user_id}: {text}")

                bot.send_message(
                    message.chat.id,
                    text="Информация сохранена. Введите следующее сообщение или нажмите 'Exit to main menu'.",
                    reply_markup=info_keyboard
                )

            elif message.content_type in ['photo', 'video', 'document', 'audio', 'voice', 'video_note', 'sticker']:
                if message.forward_from or message.forward_from_chat:
                    # Обработка пересланного медиа сообщения
                    forwarded_text = message.caption.strip() if message.caption else ''
                    user.current_info_message['forwarded_text'] = forwarded_text
                    logger.info(f"Сохранён текст пересланного медиа для пользователя {user_id}: {forwarded_text}")
                    # Генерация ссылки
                    link = get_message_link(message)
                    if link:
                        user.current_info_message['link'] = link
                        logger.info(f"Сгенерирована ссылка: {link} для пользователя {user_id}")
                    else:
                        user.current_info_message['link'] = "Неизвестно"
                        logger.info(f"Не удалось сгенерировать ссылку для пересланного медиа сообщения у пользователя {user_id}")
                else:
                    # Обработка собственного медиа сообщения пользователя
                    text = message.caption.strip() if message.caption else ''
                    user.current_info_message['user_text'] = text
                    logger.info(f"Сохранён мой текст из подписи для пользователя {user_id}: {text}")

                bot.send_message(
                    message.chat.id,
                    text="Информация сохранена. Введите следующее сообщение или нажмите 'Exit to main menu'.",
                    reply_markup=info_keyboard
                )

            else:
                bot.send_message(
                    message.chat.id,
                    text="Тип сообщения не поддерживается в этом режиме. Отправьте текст или медиа.",
                    reply_markup=info_keyboard
                )
                logger.warning(f"Пользователь {user_id} отправил неподдерживаемый тип сообщения.")

    elif user.mode == 'gpt':
        if message.content_type == "text":
            if message.text == "Exit to main menu":
                user.mode = "main"
                user.history_for_gpt_mode = []  # Очищаем историю GPT
                bot.send_message(
                    message.chat.id,
                    text="До свидания.",
                    reply_markup=main_menu_keyboard
                )
                logger.info(f"Пользователь {user_id} вышел из режима 'gpt' в 'main'")
            else:
                user_message = message.text
                logger.info(f"Пользователь {user_id} отправил сообщение для GPT: {user_message}")
                response_text = request_gpt_mode(user, user_message)
                bot.send_message(
                    message.chat.id,
                    text=response_text,
                    reply_markup=exit_keyboard
                )
                logger.info(f"Ответ GPT для пользователя {user_id} отправлен.")
        else:
            bot.send_message(
                message.chat.id,
                text="В режиме общения с GPT поддерживается только текстовые сообщения.",
                reply_markup=exit_keyboard
            )
            logger.warning(f"Пользователь {user_id} попытался отправить неподдерживаемый тип сообщения в режиме 'gpt'.")

    else:
        user.mode = 'main'
        bot.send_message(
            message.chat.id,
            text="Произошла ошибка. Вы были возвращены в главное меню.",
            reply_markup=main_menu_keyboard
        )
        logger.error(f"Пользователь {user_id} был возвращён в главное меню из неизвестного режима.")
    
    # Сохраняем данные пользователя после обработки сообщения
    save_user_data()
# Запуск бота
bot.polling()
