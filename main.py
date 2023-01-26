import hashlib
import logging
import os
import subprocess
from datetime import datetime
from functools import reduce, wraps

import requests

import qbittorrent
import rutracker_api.exceptions
from cfg import (
    ALLOWED_USER_IDS,
    JSTEG_EXE_PATH,
    QBIT_URL,
    RUTRACKER_PASS,
    RUTRACKER_USER,
    SAVE_PATH,
    SEAFILE_LOGIN,
    SEAFILE_PASSWORD,
    SEAFILE_REPO,
    SEAFILE_STORABLE_EXTENSIONS,
    SEAFILE_URL,
    SOCKS_PROXY,
    TMP_DIR,
    TOKEN,
)
from rutracker_api import RutrackerApi as RutrackerApiProvider
from telegram import File, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackContext,
    CallbackQueryHandler,
    CommandHandler,
    Filters,
    MessageHandler,
    Updater,
)

logger = logging.getLogger(__name__)
logging.basicConfig(
    format='%(asctime)s | %(name)s | %(levelname)s: %(message)s',
    level=logging.INFO,
)

updater = Updater(token=TOKEN, use_context=True)
dispatcher = updater.dispatcher
qbit_client = qbittorrent.Client(QBIT_URL)

Session = requests.Session()
Session.proxies['all'] = SOCKS_PROXY
RutrackerApi = RutrackerApiProvider(session=Session)
try:
    RutrackerApi.login(RUTRACKER_USER, RUTRACKER_PASS)
except rutracker_api.exceptions.AuthorizationException:
    logger.error('failed to login to rutracker. I wonder why...')


def restricted_zone(f):
    @wraps(f)
    def wrapper(update: Update, context: CallbackContext):
        if update.effective_user.id not in ALLOWED_USER_IDS:
            context.bot.send_message(
                chat_id=update.effective_chat.id,
                text='А я тобi не слушаю ;P'
            )
            return
        return f(update, context)
    return wrapper


def with_logging_exceptions(f):
    @wraps(f)
    def wrapper(update: Update, context: CallbackContext):
        try:
            return f(update, context)
        except Exception as exc:
            logger.error('woopsie!')
            context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f'❌ Что-то пошло не так({exc}): {exc.args}',
            )
            raise exc
    return wrapper


@with_logging_exceptions
def start(update: Update, context: CallbackContext):
    context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=(
            "Halo, hooman!🦈 \n"
            "Можешь слать мне торрент-файлы прям так, поставлю на закачку\n"
            "Или через /magnet <ссылка>\n"
            "Или через /link <ссылка-на-торрент-файл>\n"
            "Посмотреть активные - /stat\n"
            "Поискать на рутрекере - /search <запрос>\n"
            "Документы следующих разрешений сохраню в SeaFile"
            " (имя можно переопределить в подписи): "
            f"{', '.join(SEAFILE_STORABLE_EXTENSIONS)}"
        ),
    )


@with_logging_exceptions
@restricted_zone
def add_torrent_by_magnet(update: Update, context: CallbackContext):
    magnet_url = update.effective_message.text.replace('/magnet ', '')
    qbit_client.download_from_link(magnet_url)
    context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="🤖 Понял-принял",
    )


@with_logging_exceptions
@restricted_zone
def stat(update: Update, context: CallbackContext):
    torrents = qbit_client.torrents(filter='downloading')
    if not torrents:
        context.bot.send_message(
            chat_id=update.effective_chat.id,
            text='Сейчас ничего не скачивается 💤'
        )
        return
    report = ''
    for t in torrents:
        progress = round(t['progress'] * 100, 2)
        report += (
            '⭕ {} - скачано {},  ~ ⏳ {} мин. \n'.format(
                t['name'],
                '{}%'.format(str(progress)),
                t['eta'] // 60,
            )
        )
    context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=report,
    )


@with_logging_exceptions
@restricted_zone
def add_torrent_by_file(update: Update, context: CallbackContext):
    file_ = context.bot.get_file(update.message.document)
    file_name = os.path.basename(file_.file_path)
    if file_name in os.listdir(SAVE_PATH):
        context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="⚠ Этот торрент уже есть",
        )
        return
    with open(os.path.join(SAVE_PATH, file_name), 'wb') as outfile:
        file_.download(out=outfile)
    context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="✅ Закачка скоро начнется"
    )


@with_logging_exceptions
@restricted_zone
def add_torrent_by_file_link(update: Update, context: CallbackContext):
    file_url = update.effective_message.text.replace('/link ', '')
    response = requests.get(file_url, stream=True)
    if response.status_code != 200:
        raise Exception('Вернулся код {}'.format(response.status_code))
    hasher = hashlib.sha256()
    hasher.update(file_url.encode())
    out_path = os.path.join(
        SAVE_PATH,
        '{}.torrent'.format(hasher.hexdigest()),
    )
    with open(out_path, 'wb') as f:
        for chunk in response:
            f.write(chunk)

    context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="✅ Закачка скоро начнется"
    )


def get_auth_token_from_seafile():
    api_auth_path = f'{SEAFILE_URL}/api2/auth-token/'
    response = requests.post(
        api_auth_path,
        data={'username': SEAFILE_LOGIN, 'password': SEAFILE_PASSWORD},
    )
    if response.status_code != 200:
        raise Exception(f'Got HTTP {response.status_code}: {response.text}')

    token = response.json()['token']
    return token


def save_file(file_: File, file_name: str, randomize: bool = False) -> str:
    if not os.path.exists(TMP_DIR):
        os.mkdir(TMP_DIR)
    if randomize:
        hasher = hashlib.sha256()
        hasher.update(file_name.encode())
        filename_for_saving = hasher.hexdigest()
    else:
        filename_for_saving = file_name
    temp_file_path = os.path.join(TMP_DIR, filename_for_saving)
    with open(temp_file_path, 'wb') as outfile:
        file_.download(out=outfile)
    return temp_file_path


@with_logging_exceptions
@restricted_zone
def upload_doc_to_seafile(update: Update, context: CallbackContext):
    MEGABYTE = 1024 * 1024
    LIMIT = 20
    file_ = context.bot.get_file(update.message.document)
    ext = file_.file_path.rsplit('.', 1)[-1]
    if update.effective_message.caption:
        file_name = f'{update.effective_message.caption}.{ext}'
    else:
        file_name = update.effective_message.effective_attachment.file_name

    if file_.file_size > MEGABYTE * LIMIT:
        context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"❌ Файл слишком большой, лимит в {LIMIT}МБ"
        )
        return

    temp_file_path = save_file(file_, file_name)

    token = get_auth_token_from_seafile()
    get_upload_link_path = (
        f'{SEAFILE_URL}/api2/repos/{SEAFILE_REPO}/upload-link/'
    )
    uplink_req = requests.get(
        get_upload_link_path,
        headers={'Authorization': f'Token {token}'},
    )
    if uplink_req.status_code != 200:
        raise Exception(
            f'Got HTTP {uplink_req.status_code}: {uplink_req.text}',
        )
    upload_link = uplink_req.json()

    with open(temp_file_path, 'rb') as temp_file:
        r = requests.post(
            upload_link,
            files={'file': temp_file},
            data={
                'parent_dir': '/',
                'replace': 0,
            },
            headers={
                'Authorization': f'Token {token}',
            },
        )

    if r.status_code == 200:
        response_text = "✅ Файл сохранен"
    else:
        response_text = f"❌ HTTP {r.status_code}: {r.text[:100]}..."

    context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=response_text,
    )


@with_logging_exceptions
@restricted_zone
def check_photo(update: Update, context: CallbackContext):
    file_ = context.bot.get_file(update.message.document)
    file_name = os.path.basename(file_.file_path)
    if not os.path.exists(TMP_DIR):
        os.mkdir(TMP_DIR)
    photo_path = save_file(file_, file_name)
    cmd = [JSTEG_EXE_PATH, 'reveal', photo_path]
    with subprocess.Popen(cmd, stdout=subprocess.PIPE) as proc:
        res = proc.stdout.read().decode()
    os.remove(photo_path)
    context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=res or 'Ничего не обнаружено',
    )


def chunks(iterable, n):
    """Yield n number of sequential chunks from iterable."""
    d, r = divmod(len(iterable), n)
    for i in range(n):
        si = (d + 1) * (i if i < r else r) + d * (0 if i < r else i - r)
        yield iterable[si:si + (d + 1 if i < r else d)]


@with_logging_exceptions
@restricted_zone
def CallbackHandler(update: Update, context: CallbackContext):
    if 'get-torrent-by-topic' in update.callback_query.data:
        topic_id = update.callback_query.data.replace(
            'get-torrent-by-topic', '',
        ).strip()
        out_path = os.path.join(
            SAVE_PATH,
            f'rutracker-{topic_id}-{datetime.utcnow().timestamp()}.torrent',
        )
        with open(out_path, 'wb') as outfile:
            outfile.write(
                RutrackerApi.download(topic_id)
            )
        context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="✅ Закачка скоро начнется",
        )


@with_logging_exceptions
@restricted_zone
def find_on_torrents(update: Update, context: CallbackContext):
    RESULTS_TO_SHOW = 10
    BUTTONS_ON_ROW = 5
    ROWS = int(RESULTS_TO_SHOW / BUTTONS_ON_ROW)
    search_query = update.effective_message.text.replace('/search ', '')
    search = RutrackerApi.search(search_query)
    results = search['result'][:RESULTS_TO_SHOW]

    if results:
        message = (
            f'🔎 Результатов на первой странице: {len(search["result"])},'
            f' покажу максимум {RESULTS_TO_SHOW}:\n'
        )
        formatted_results = '\n'.join(
            f'👉 {idx}. [{t.category}] ({t.formatted_size()}) {t.title}'
            for idx, t in enumerate(results, 1)
        )
        message = message + formatted_results
        buttons = [
            InlineKeyboardButton(
                text=f'⬇ {idx}',
                callback_data=f'get-torrent-by-topic {r.topic_id}'
            ) for idx, r in enumerate(results, 1)
        ]
        reply_markup = InlineKeyboardMarkup(
            inline_keyboard=list(chunks(buttons, ROWS)),
        )
    else:
        message = 'Ничего не найдено по запросу "{search_query}" 🤔'
        reply_markup = None

    context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=message,
        reply_markup=reply_markup,
    )


@with_logging_exceptions
@restricted_zone
def get_ip(update: Update, context: CallbackContext):
    ip = requests.get('https://api.ipify.org').content.decode('utf8')
    context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f'Current external IP: {ip}',
    )


@with_logging_exceptions
@restricted_zone
def exec_cmd(update: Update, context: CallbackContext):
    cmd = update.effective_message.text.replace('/exec ', '', 1)
    if not cmd:
        context.bot.send_message(
            chat_id=update.effective_chat.id,
            text='Got empty command, not doing a shit!',
        )
        return

    code, output = subprocess.getstatusoutput(cmd)
    context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f'code: {code}\nOutput:\n{output}',
    )


ext_filter = Filters.document.file_extension

start_handler = CommandHandler('start', start)
magnet_handler = CommandHandler('magnet', add_torrent_by_magnet)
file_link_handler = CommandHandler('link', add_torrent_by_file_link)
stat_handler = CommandHandler('stat', stat)
rutracker_topic_handler = CallbackQueryHandler(CallbackHandler)
torrents_search_handler = CommandHandler('search', find_on_torrents)
get_ip_handler = CommandHandler('ip', get_ip)
exec_handler = CommandHandler('exec', exec_cmd)

download_handler = MessageHandler(
    ext_filter('torrent'),
    add_torrent_by_file,
)
photo_check_handler = MessageHandler(
    Filters.document.jpg,
    check_photo
)
seafile_handler = MessageHandler(
    reduce(
        lambda merged_filter, ext2: merged_filter | ext_filter(ext2),
        SEAFILE_STORABLE_EXTENSIONS,
        ext_filter(SEAFILE_STORABLE_EXTENSIONS[0]),
    ),
    upload_doc_to_seafile,
)

handlers = [
    start_handler,
    stat_handler,
    magnet_handler,
    download_handler,
    file_link_handler,
    photo_check_handler,
    seafile_handler,
    rutracker_topic_handler,
    torrents_search_handler,
    get_ip_handler,
    exec_handler,
]
for h in handlers:
    dispatcher.add_handler(h)

logger.info('Start polling...')
updater.start_polling()
