"""
Calcula indicadores técnicos y fundamentales simples para cada acción
y decide si está en una posible "zona de compra" para perfil de largo plazo.

Filosofía (acordada con el usuario): ser transparente sobre la incertidumbre,
nunca dar una "predicción" como si fuera certeza. Esto es un indicador de apoyo,
no una recomendación de inversión.
"""

import logging
import numpy as np
import pandas as pd
import yfinance as yf

logger = logging.getLogger("signals")

# --- Parámetros del modelo (ajustables) -------------------------------------------------

RSI_PERIOD = 14
RSI_OVERSOLD = 35          # RSI por debajo de esto se considera "sobrevendida"
NEAR_52W_LOW_PCT = 0.15    # dentro de un 15% del mínimo de 52 semanas
SMA_TREND_PERIOD = 50      # media móvil para ver si la tendencia de fondo no está rota
MIN_SIGNALS_REQUIRED = 2   # cuántas señales (de 3) se necesitan para gatillar la alerta


def _compute_rsi(close: pd.Series, period: int = RSI_PERIOD) -> float:
    """RSI clásico de Wilder sobre la serie de cierres."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return float(rsi.iloc[-1])


def analyze_ticker(yahoo_symbol: str) -> dict | None:
    """
    Descarga ~1 año de precios + info fundamental para un ticker y devuelve
    un diccionario con los indicadores y si gatilla señal de zona de compra.
    Devuelve None si no hay datos suficientes (acción sin liquidez, ticker
    incorrecto, etc.) para no mandar alertas con información poco confiable.
    """
    try:
        tk = yf.Ticker(yahoo_symbol)
        hist = tk.history(period="1y", interval="1d", auto_adjust=True)
    except Exception as e:
        logger.warning("Error descargando %s: %s", yahoo_symbol, e)
        return None

    if hist is None or hist.empty or len(hist) < 60:
        logger.info("Datos insuficientes para %s, se omite", yahoo_symbol)
        return None

    close = hist["Close"].dropna()
    current_price = float(close.iloc[-1])

    # --- Señal 1: RSI sobrevendido ---
    try:
        rsi = _compute_rsi(close)
    except Exception:
        rsi = None
    signal_rsi = (rsi is not None) and (rsi <= RSI_OVERSOLD)

    # --- Señal 2: cerca del mínimo de 52 semanas ---
    low_52w = float(close.min())
    high_52w = float(close.max())
    range_52w = max(high_52w - low_52w, 1e-9)
    position_in_range = (current_price - low_52w) / range_52w  # 0 = en el mínimo, 1 = en el máximo
    signal_near_low = position_in_range <= NEAR_52W_LOW_PCT

    # --- Señal 3: dividend yield actual vs promedio histórico simple ---
    dividend_yield = None
    avg_dividend_yield = None
    signal_yield = False
    try:
        info = tk.info or {}
        dividend_yield = info.get("dividendYield")
        if dividend_yield:
            # yfinance a veces lo da como fracción (0.05) y a veces como % (5.0)
            if dividend_yield > 1:
                dividend_yield = dividend_yield / 100
        divs = tk.dividends
        if dividend_yield and divs is not None and len(divs) > 0:
            # yield histórico aproximado: dividendos pagados en los últimos 5 años / precio actual,
            # anualizado — es una aproximación simple, no un dato oficial.
            five_years_divs = divs[divs.index >= (divs.index.max() - pd.DateOffset(years=5))]
            if len(five_years_divs) > 0:
                years_span = max((five_years_divs.index.max() - five_years_divs.index.min()).days / 365, 1)
                avg_annual_div = five_years_divs.sum() / years_span
                avg_dividend_yield = float(avg_annual_div / current_price)
                signal_yield = dividend_yield >= avg_dividend_yield * 1.1  # 10% por sobre su propio promedio
    except Exception as e:
        logger.info("No se pudo calcular yield histórico de %s: %s", yahoo_symbol, e)

    # --- Tendencia de fondo (para no comprar "cuchillos cayendo") ---
    sma = close.rolling(SMA_TREND_PERIOD).mean()
    sma_now = float(sma.iloc[-1]) if not pd.isna(sma.iloc[-1]) else None
    sma_prev = float(sma.iloc[-10]) if len(sma) > 10 and not pd.isna(sma.iloc[-10]) else None
    if sma_now is not None and sma_prev is not None:
        if sma_now > sma_prev * 1.01:
            trend = "alcista"
        elif sma_now < sma_prev * 0.99:
            trend = "bajista"
        else:
            trend = "lateral"
    else:
        trend = "indeterminada"

    signals_count = sum([signal_rsi, signal_near_low, signal_yield])
    is_buy_zone = signals_count >= MIN_SIGNALS_REQUIRED

    return {
        "price": current_price,
        "rsi": rsi,
        "position_in_52w_range_pct": round(position_in_range * 100, 1),
        "low_52w": low_52w,
        "high_52w": high_52w,
        "dividend_yield": dividend_yield,
        "avg_dividend_yield_5y": avg_dividend_yield,
        "trend": trend,
        "signal_rsi": signal_rsi,
        "signal_near_low": signal_near_low,
        "signal_yield": signal_yield,
        "signals_count": signals_count,
        "is_buy_zone": is_buy_zone,
    }
