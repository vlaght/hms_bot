import hashlib
import logging
import math
import os
import subprocess
from functools import reduce, wraps
from urllib.parse import urlparse

import qbittorrent
import requests
from telegram import Update
from telegram import File
from telegram.ext import CallbackContext
from telegram.ext import CommandHandler
from telegram.ext import Filters
from telegram.ext import MessageHandler
from telegram.ext import  Updater

from cfg import ALLOWED_USERNAMES
from cfg import JSTEG_EXE_PATH
from cfg import QBIT_URL
from cfg import SAVE_PATH
from cfg import TMP_DIR
from cfg import TOKEN
from cfg import SEAFILE_URL
from cfg import SEAFILE_LOGIN
from cfg import SEAFILE_PASSWORD
from cfg import SEAFILE_REPO
from cfg import SEAFILE_STORABLE_EXTENSIONS


logger = logging.getLogger(__name__)
logging.basicConfig(
    format='%(asctime)s | %(name)s | %(levelname)s: %(message)s',
    level=logging.INFO,
)

updater = Updater(token=TOKEN, use_context=True)
dispatcher = updater.dispatcher
qbit_client = qbittorrent.Client(QBIT_URL)


def restricted_zone(f):
    @wraps(f)
    def wrapper(update: Update, context: CallbackContext):
        if update.effective_user.username not in ALLOWED_USERNAMES:
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
                text="❌ Что-то пошло не так: {}".format(exc.args[0]),
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
        progress = round(t['progress']*100, 2)
        report += (
            '⭕ {} - скачано {},  ~ ⏳ {} мин. \n'.format(
                t['name'],
                '{}%'.format(str(progress)),
                t['eta']//60,
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

ext_filter = Filters.document.file_extension

start_handler = CommandHandler('start', start)
magnet_handler = CommandHandler('magnet', add_torrent_by_magnet)
file_link_handler = CommandHandler('link', add_torrent_by_file_link)
stat_handler = CommandHandler('stat', stat)
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
]
for h in handlers:
    dispatcher.add_handler(h)

logger.info('Start polling...')
updater.start_polling()
