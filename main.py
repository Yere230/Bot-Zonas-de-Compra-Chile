"""
Bot de Telegram - Zonas de compra (Bolsa de Santiago)
------------------------------------------------------
Revisa periódicamente un listado de acciones chilenas y envía una alerta
a Telegram cuando detecta que una acción entra en una posible "zona de
compra" para perfil de largo plazo (RSI bajo + cerca de mínimos de 52
semanas + dividend yield sobre su propio promedio histórico).

Pensado para desplegarse gratis en Render como "Web Service": Render
necesita que el proceso abra un puerto HTTP, por eso corre un mini
servidor Flask en paralelo al scheduler que hace el trabajo real.

Variables de entorno necesarias:
  TELEGRAM_BOT_TOKEN  -> token que te da @BotFather al crear el bot
  TELEGRAM_CHAT_ID    -> tu chat_id (ver README.md para obtenerlo)

Opcionales:
  CHECK_INTERVAL_MINUTES  -> cada cuántos minutos revisa (default 30)
  PORT                    -> puerto HTTP (Render lo inyecta solo)
"""

import logging
import os
import threading
from datetime import datetime, date
from zoneinfo import ZoneInfo

import requests
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from flask import Flask

from tickers import TICKERS, to_yahoo_symbol
from signals import analyze_ticker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("main")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
CHECK_INTERVAL_MINUTES = int(os.environ.get("CHECK_INTERVAL_MINUTES", "30"))
CHILE_TZ = ZoneInfo("America/Santiago")

if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
    logger.warning(
        "Faltan TELEGRAM_BOT_TOKEN y/o TELEGRAM_CHAT_ID. "
        "El bot va a correr pero no podrá enviar mensajes hasta que los configures."
    )

TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

# Memoria simple en RAM para no repetir la misma alerta todos los días.
# Se reinicia si el servicio se reinicia (en el free tier de Render puede pasar
# ocasionalmente) — en el peor caso, recibirías una alerta repetida, no se pierde nada.
_last_alert_date: dict[str, date] = {}


def send_telegram_message(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.error("No se puede enviar mensaje: falta configurar el bot de Telegram.")
        return
    try:
        resp = requests.post(
            TELEGRAM_API_URL,
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
        if resp.status_code != 200:
            logger.error("Telegram respondió %s: %s", resp.status_code, resp.text)
    except Exception as e:
        logger.error("Error enviando mensaje a Telegram: %s", e)


def format_alert(ticker: str, data: dict) -> str:
    yield_line = ""
    if data["dividend_yield"] is not None:
        yield_line = f"\nDividend yield: {data['dividend_yield']*100:.1f}%"
        if data["avg_dividend_yield_5y"] is not None:
            yield_line += f" (promedio 5a: {data['avg_dividend_yield_5y']*100:.1f}%)"

    rsi_txt = f"{data['rsi']:.0f}" if data["rsi"] is not None else "N/D"

    return (
        f"📊 <b>ZONA DE COMPRA — {ticker}</b>\n"
        f"Precio actual: ${data['price']:.2f}\n"
        f"RSI: {rsi_txt}\n"
        f"Posición en rango 52 sem.: {data['position_in_52w_range_pct']}% "
        f"(mín ${data['low_52w']:.2f} / máx ${data['high_52w']:.2f})\n"
        f"Tendencia de fondo (SMA{50}): {data['trend']}"
        f"{yield_line}\n\n"
        f"⚠️ Señal informativa basada en indicadores técnicos y fundamentales simples. "
        f"No es una recomendación de inversión — evalúa siempre por tu cuenta."
    )


def check_all_signals() -> None:
    logger.info("Iniciando revisión de %d acciones...", len(TICKERS))
    today = datetime.now(CHILE_TZ).date()
    alerts_sent = 0

    for ticker in TICKERS:
        yahoo_symbol = to_yahoo_symbol(ticker)
        data = analyze_ticker(yahoo_symbol)
        if data is None:
            continue

        if data["is_buy_zone"]:
            if _last_alert_date.get(ticker) == today:
                continue  # ya se avisó hoy, no repetir
            send_telegram_message(format_alert(ticker, data))
            _last_alert_date[ticker] = today
            alerts_sent += 1
        else:
            # si ya no cumple la condición, se libera para poder volver a avisar
            # si vuelve a entrar en zona de compra más adelante
            _last_alert_date.pop(ticker, None)

    logger.info("Revisión terminada. Alertas enviadas: %d", alerts_sent)


def is_market_hours(now: datetime) -> bool:
    """Bolsa de Santiago: lunes a viernes, ~9:30 a 16:00 hora de Chile."""
    if now.weekday() >= 5:  # sábado=5, domingo=6
        return False
    start = now.replace(hour=9, minute=30, second=0, microsecond=0)
    end = now.replace(hour=16, minute=0, second=0, microsecond=0)
    return start <= now <= end


def scheduled_job() -> None:
    now = datetime.now(CHILE_TZ)
    if not is_market_hours(now):
        logger.info("Fuera de horario bursátil (%s), se omite esta revisión.", now)
        return
    check_all_signals()


def start_scheduler() -> None:
    scheduler = BackgroundScheduler(timezone=CHILE_TZ)
    scheduler.add_job(
        scheduled_job,
        trigger=CronTrigger(minute=f"*/{CHECK_INTERVAL_MINUTES}"),
        id="check_signals",
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    logger.info("Scheduler iniciado: revisa cada %d minutos en horario bursátil.", CHECK_INTERVAL_MINUTES)


# --- Mini servidor web, solo para que Render mantenga el servicio despierto ---
app = Flask(__name__)


@app.route("/")
def health_check():
    return {
        "status": "ok",
        "bot": "zonas-compra-bolsa-chile",
        "acciones_monitoreadas": len(TICKERS),
        "hora_chile": datetime.now(CHILE_TZ).isoformat(),
    }


@app.route("/revisar-ahora")
def manual_check():
    """Endpoint manual para forzar una revisión (útil para probar que todo funciona)."""
    threading.Thread(target=check_all_signals, daemon=True).start()
    return {"status": "revisión iniciada, revisa tu Telegram en unos segundos"}


if __name__ == "__main__":
    start_scheduler()
    send_telegram_message("✅ Bot de zonas de compra conectado y monitoreando la Bolsa de Santiago.")
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
