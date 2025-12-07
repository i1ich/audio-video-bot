# requirements.txt
# python-telegram-bot>=20.0
# moviepy>=1.0.3

import os
import uuid
import logging
from pathlib import Path

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

import moviepy.editor as mpy

# --------------------- Конфигурация ---------------------
TOKEN = "8459591176:AAFILVoiI_EzJJvUEeGWtM17AriUL-eqKps"  # ← Замените на токен вашего бота
TEMP_DIR = Path("temp")
TEMP_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# --------------------- Хранилище состояний ---------------------
# Пользователь → ожидаемое следующее сообщение (None / "waiting_for_audio")
user_state = {}


# --------------------- Утилиты ---------------------
def clear_user_state(user_id: int):
    user_state.pop(user_id, None)


async def download_file(bot, file_obj, destination: Path):
    file_path = await file_obj.download_to_drive(custom_path=str(destination))
    return Path(file_path)


def replace_audio_in_video(video_path: Path, audio_path: Path, output_path: Path):
    """
    Заменяет аудиодорожку в видео на новую.
    """
    video = mpy.VideoFileClip(str(video_path))
    new_audio = mpy.AudioFileClip(str(audio_path))

    # Обрезаем аудио по длительности видео (или видео по аудио — выбирайте)
    if new_audio.duration > video.duration:
        new_audio = new_audio.subclip(0, video.duration)
    else:
        # Если аудио короче — зацикливаем или оставляем тишину в конце
        # Здесь просто обрезаем видео под длину аудио (можно поменять логику)
        video = video.subclip(0, new_audio.duration)

    final_video = video.set_audio(new_audio)
    final_video.write_videofile(
        str(output_path),
        codec="libx264",
        audio_codec="aac",
        temp_audiofile=str(output_path.with_suffix(".tmp.aac")),
        remove_temp=True,
        fps=30,
        preset="medium",
        threads=4,
        logger=None,  # отключаем прогресс-бар moviepy
    )
    video.close()
    new_audio.close()
    final_video.close()


# --------------------- Обработчики ---------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Отправь мне сначала видео (или видеосообщение), "
        "а затем аудио (голосовое сообщение или аудиофайл). "
        "Я заменю звук в видео и отправлю результат."
    )
    clear_user_state(update.effective_user.id)


async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if user_id in user_state and user_state[user_id] == "waiting_for_audio":
        await update.message.reply_text("Ты уже прислал видео. Теперь пришли аудио.")
        return

    video_file = update.message.video or update.message.video_note
    if not video_file:
        return

    await update.message.reply_text("Видео получено. Теперь пришли аудио (голосовое или файл).")

    file_id = video_file.file_id
    file_obj = await context.bot.get_file(file_id)

    unique_id = uuid.uuid4().hex
    video_path = TEMP_DIR / f"{user_id}_{unique_id}_video.mp4"
    await download_file(context.bot, file_obj, video_path)

    # Сохраняем путь к видео в job_queue (чтобы потом достать)
    context.user_data["last_video_path"] = str(video_path)
    user_state[user_id] = "waiting_for_audio"


async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if user_state.get(user_id) != "waiting_for_audio":
        await update.message.reply_text("Сначала пришли видео.")
        return

    audio_file = update.message.audio or update.message.voice or update.message.document
    if not audio_file:
        await update.message.reply_text("Это не аудио. Пришли аудиофайл или голосовое сообщение.")
        return

    status_msg = await update.message.reply_text("Скачиваю файлы и обрабатываю… Это может занять 10–60 секунд.")

    # Скачиваем аудио
    file_id = (audio_file.file_id if hasattr(audio_file, "file_id") else
               audio_file.file_id if update.message.voice else
               audio_file.file_id)
    file_obj = await context.bot.get_file(file_id)

    unique_id = uuid.uuid4().hex
    audio_ext = ".ogg" if update.message.voice else ".mp3"
    if update.message.document and audio_file.mime_type:
        audio_ext = "." + audio_file.mime_type.split("/")[-1]
    audio_path = TEMP_DIR / f"{user_id}_{unique_id}_audio{audio_ext}"
    await download_file(context.bot, file_obj, audio_path)

    # Конвертируем голосовое .ogg в .mp3 (moviepy лучше работает с mp3/wav)
    if audio_path.suffix == ".ogg":
        temp_audio = mpy.AudioFileClip(str(audio_path))
        mp3_path = audio_path.with_suffix(".mp3")
        temp_audio.write_audiofile(str(mp3_path), logger=None)
        temp_audio.close()
        audio_path.unlink()  # удаляем ogg
        audio_path = mp3_path

    video_path_str = context.user_data.get("last_video_path")
    if not video_path_str or not Path(video_path_str).exists():
        await status_msg.edit_text("Ошибка: видео не найдено. Начни сначала командой /start")
        clear_user_state(user_id)
        return

    video_path = Path(video_path_str)
    output_path = TEMP_DIR / f"{user_id}_{unique_id}_result.mp4"

    await status_msg.edit_text("Меняю аудиодорожку…")

    try:
        replace_audio_in_video(video_path, audio_path, output_path)
    except Exception as e:
        logger.exception("Ошибка при обработке видео")
        await status_msg.edit_text(f"Произошла ошибка при обработке: {str(e)}")
        clear_user_state(user_id)
        return

    await status_msg.edit_text("Отправляю готовое видео…")

    with open(output_path, "rb") as f:
        await update.message.reply_video(
            video=f,
            caption="Готово! Аудиодорожка заменена.",
            supports_streaming=True,
        )

    await status_msg.delete()

    # Очистка
    for p in (video_path, audio_path, output_path):
        try:
            p.unlink()
        except:
            pass

    context.user_data.pop("last_video_path", None)
    clear_user_state(user_id)


# --------------------- Основной запуск ---------------------
def main():
    app = Application.builder().token(TOKEN).concurrent_updates(True).build()

    app.add_handler(CommandHandler("start", start))

    # Видео и видеосообщения
    app.add_handler(MessageHandler(filters.VIDEO | filters.VIDEO_NOTE, handle_video))

    # Аудио, голосовые сообщения, документы (аудио)
    audio_filters = (
            filters.AUDIO |
            filters.VOICE |
            filters.Document.AUDIO |
            filters.Document.MimeType("audio/.*")
    )
    app.add_handler(MessageHandler(audio_filters, handle_audio))

    # Запуск
    print("Бот запущен...")
    app.run_polling()


if __name__ == "__main__":
    main()
