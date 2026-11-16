"""
Backtest del bot de "zonas de compra" (Bolsa de Santiago)
-----------------------------------------------------------
Descarga ~5 años de historial de cada acción de tickers.py y prueba
distintas combinaciones de parámetros (RSI, cercanía al mínimo de 52
semanas, cuántas señales exigir) para ver cuál habría dado mejores
resultados "si hubieras comprado cuando el bot avisó".

Cómo correrlo:
    pip install -r requirements.txt
    python backtest.py

Tarda varios minutos (descarga datos de 45 acciones). Al terminar,
deja un archivo backtest_resultados.csv con el detalle de cada
combinación probada, ordenado de mejor a peor.

IMPORTANTE - limitaciones de este backtest, para leerlas con ojo crítico:
- Es un backtest simple de "qué pasó con el precio X días después de la
  señal", no considera comisiones, impuestos ni el momento exacto de
  entrada/salida real.
- El "retorno promedio" puede estar distorsionado por pocas señales en
  acciones poco líquidas - revisa también la columna n_señales.
- Rendimiento pasado no garantiza nada hacia adelante. Esto sirve para
  comparar parámetros entre sí, no para predecir el futuro.
"""

import itertools
import logging
import sys
import warnings
from datetime import datetime

import numpy as np
import pandas as pd
import yfinance as yf

from tickers import TICKERS, to_yahoo_symbol

warnings.filterwarnings("ignore")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("backtest")

# --- Grilla de parámetros a probar --------------------------------------------------
RSI_PERIOD = 14
RSI_THRESHOLDS = [25, 30, 35, 40]          # qué tan "sobrevendida" exige el RSI
NEAR_LOW_THRESHOLDS = [0.10, 0.15, 0.20, 0.25]  # qué tan cerca del mínimo de 52 semanas
MIN_SIGNALS_OPTIONS = [1, 2]                # cuántas de las 2 señales (RSI + cercanía al mínimo) exigir
REQUIRE_NON_BEARISH = [False, True]         # si además exige que la tendencia SMA50 no sea bajista
FORWARD_WINDOWS = [30, 60, 90, 180]         # a cuántos días mirar el retorno después de la señal

HISTORY_PERIOD = "5y"
SMA_TREND_PERIOD = 50


def compute_rsi(close: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def download_history(ticker: str) -> pd.DataFrame | None:
    yahoo_symbol = to_yahoo_symbol(ticker)
    try:
        hist = yf.Ticker(yahoo_symbol).history(period=HISTORY_PERIOD, interval="1d", auto_adjust=True)
    except Exception as e:
        logger.warning("No se pudo descargar %s: %s", yahoo_symbol, e)
        return None
    if hist is None or hist.empty or len(hist) < 260:  # necesita al menos ~1 año para el rolling de 52 sem
        logger.info("Datos insuficientes para %s, se omite del backtest", ticker)
        return None
    return hist


def build_indicator_series(hist: pd.DataFrame) -> pd.DataFrame:
    """Calcula, para cada día del historial, los indicadores que el bot usaría EN ESE MOMENTO
    (rolling de 252 ruedas para simular 52 semanas, sin mirar al futuro)."""
    close = hist["Close"].dropna()
    df = pd.DataFrame(index=close.index)
    df["close"] = close
    df["rsi"] = compute_rsi(close)

    roll_min = close.rolling(252, min_periods=120).min()
    roll_max = close.rolling(252, min_periods=120).max()
    range_52w = (roll_max - roll_min).replace(0, np.nan)
    df["position_in_range"] = (close - roll_min) / range_52w

    sma = close.rolling(SMA_TREND_PERIOD, min_periods=SMA_TREND_PERIOD).mean()
    sma_prev = sma.shift(10)
    df["trend_bearish"] = sma < (sma_prev * 0.99)

    return df.dropna(subset=["rsi", "position_in_range"])


def forward_return(close: pd.Series, idx_pos: int, days: int) -> float | None:
    if idx_pos + days >= len(close):
        return None
    entry = close.iloc[idx_pos]
    exit_ = close.iloc[idx_pos + days]
    if entry <= 0:
        return None
    return (exit_ / entry) - 1


def run_backtest() -> pd.DataFrame:
    all_series = {}
    logger.info("Descargando historial de %d acciones (%s)...", len(TICKERS), HISTORY_PERIOD)
    for i, ticker in enumerate(TICKERS, 1):
        hist = download_history(ticker)
        if hist is None:
            continue
        series = build_indicator_series(hist)
        if len(series) < 50:
            continue
        all_series[ticker] = series
        logger.info("[%d/%d] %s: %d días de datos usables", i, len(TICKERS), ticker, len(series))

    logger.info("Acciones usables para el backtest: %d de %d", len(all_series), len(TICKERS))

    combos = list(itertools.product(
        RSI_THRESHOLDS, NEAR_LOW_THRESHOLDS, MIN_SIGNALS_OPTIONS, REQUIRE_NON_BEARISH
    ))
    logger.info("Probando %d combinaciones de parámetros...", len(combos))

    rows = []
    for rsi_th, near_low_th, min_signals, require_non_bearish in combos:
        for window in FORWARD_WINDOWS:
            returns = []
            n_signals = 0
            for ticker, series in all_series.items():
                close = series["close"]
                sig_rsi = series["rsi"] <= rsi_th
                sig_near_low = series["position_in_range"] <= near_low_th
                signals_count = sig_rsi.astype(int) + sig_near_low.astype(int)
                is_buy_zone = signals_count >= min_signals
                if require_non_bearish:
                    is_buy_zone = is_buy_zone & (~series["trend_bearish"])

                signal_dates = series.index[is_buy_zone]
                # para no sobre-contar: solo cuenta como "nueva señal" el primer día de cada racha
                prev_was_signal = False
                for date in series.index:
                    currently_signal = bool(is_buy_zone.loc[date])
                    if currently_signal and not prev_was_signal:
                        idx_pos = series.index.get_loc(date)
                        ret = forward_return(close, idx_pos, window)
                        if ret is not None:
                            returns.append(ret)
                            n_signals += 1
                    prev_was_signal = currently_signal

            if n_signals == 0:
                continue

            returns_arr = np.array(returns)
            rows.append({
                "rsi_umbral": rsi_th,
                "cercania_min_52w": near_low_th,
                "min_senales_requeridas": min_signals,
                "exige_tendencia_no_bajista": require_non_bearish,
                "ventana_dias": window,
                "n_senales": n_signals,
                "retorno_promedio_%": round(float(returns_arr.mean()) * 100, 2),
                "retorno_mediana_%": round(float(np.median(returns_arr)) * 100, 2),
                "tasa_ganadoras_%": round(float((returns_arr > 0).mean()) * 100, 1),
                "peor_caso_%": round(float(returns_arr.min()) * 100, 2),
                "mejor_caso_%": round(float(returns_arr.max()) * 100, 2),
            })

    results = pd.DataFrame(rows)
    results = results.sort_values(["ventana_dias", "retorno_promedio_%"], ascending=[True, False])
    return results


if __name__ == "__main__":
    start = datetime.now()
    results = run_backtest()
    if results.empty:
        logger.error("No se generaron resultados - revisa la conexión a internet / Yahoo Finance.")
        sys.exit(1)

    out_path = "backtest_resultados.csv"
    results.to_csv(out_path, index=False)
    logger.info("Listo en %s. Resultados guardados en %s", datetime.now() - start, out_path)

    print("\n=== TOP 10 combinaciones por ventana de 90 días (ordenadas por retorno promedio) ===")
    top90 = results[results["ventana_dias"] == 90].head(10)
    print(top90.to_string(index=False))

    print("\n=== TOP 10 combinaciones por ventana de 180 días ===")
    top180 = results[results["ventana_dias"] == 180].head(10)
    print(top180.to_string(index=False))

    print(f"\nRevisa el archivo completo: {out_path}")
