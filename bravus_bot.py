import requests
import time
import os
from dotenv import load_dotenv

# ==============================
# CONFIG
# ==============================
load_dotenv()

TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

PAIR = "BTCUSD"
INTERVAL = 1

LEN_EMA1 = 30
LEN_EMA2 = 35
LEN_EMA3 = 40
LEN_EMA4 = 45
LEN_EMA5 = 50
LEN_EMA6 = 60

USE_SLOPE_FILTER = True
USE_SPREAD_FILTER = True
USE_PERSISTENCE_FILTER = True

MIN_SPREAD_PERC = 0.0005
BARS_FOR_TREND_HOLD = 2

USE_ATR_FILTER = True
ATR_LENGTH = 14
ATR_MIN_MULT = 0.8

USE_IMPULSE_FILTER = True
MIN_BODY_RATIO = 0.5

CHECK_EVERY_SECONDS = 60

# ==============================
# TELEGRAM
# ==============================
def enviar_mensaje(texto: str) -> None:
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    data = {
        "chat_id": CHAT_ID,
        "text": texto
    }

    try:
        response = requests.post(url, data=data, timeout=20)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print("Error enviando Telegram:", e)

# ==============================
# DATOS KRAKEN
# ==============================
def obtener_ohlc(pair: str = PAIR, interval: int = INTERVAL):
    url = f"https://api.kraken.com/0/public/OHLC?pair={pair}&interval={interval}"
    response = requests.get(url, timeout=15)
    data = response.json()

    if data.get("error"):
        raise ValueError(f"Error Kraken: {data['error']}")

    result = data["result"]
    pair_key = [k for k in result.keys() if k != "last"][0]
    candles = result[pair_key]

    return candles

# ==============================
# INDICADORES
# ==============================
def calcular_serie_ema(valores, periodo):
    if len(valores) < periodo:
        return []

    serie = []
    k = 2 / (periodo + 1)
    ema = sum(valores[:periodo]) / periodo
    serie.append(ema)

    for precio in valores[periodo:]:
        ema = precio * k + ema * (1 - k)
        serie.append(ema)

    return serie

def ultimos_dos(valores):
    if len(valores) < 2:
        return None, None
    return valores[-2], valores[-1]

def barras_desde_false(lista_booleana):
    contador = 0
    for valor in reversed(lista_booleana):
        if valor:
            contador += 1
        else:
            break
    return contador

# ==============================
# LÓGICA BRAVUS / CHAVINETA
# ==============================
def analizar_bravus():
    candles = obtener_ohlc()

    # Quitamos la última vela porque puede estar en formación
    candles = candles[:-1]

    closes = [float(c[4]) for c in candles]

    ema1_series = calcular_serie_ema(closes, LEN_EMA1)
    ema2_series = calcular_serie_ema(closes, LEN_EMA2)
    ema3_series = calcular_serie_ema(closes, LEN_EMA3)
    ema4_series = calcular_serie_ema(closes, LEN_EMA4)
    ema5_series = calcular_serie_ema(closes, LEN_EMA5)
    ema6_series = calcular_serie_ema(closes, LEN_EMA6)

    min_len = min(
        len(ema1_series), len(ema2_series), len(ema3_series),
        len(ema4_series), len(ema5_series), len(ema6_series)
    )

    if min_len < 3:
        raise ValueError("No hay suficientes datos para calcular la lógica avanzada de Bravus Bot.")

    ema1_series = ema1_series[-min_len:]
    ema2_series = ema2_series[-min_len:]
    ema3_series = ema3_series[-min_len:]
    ema4_series = ema4_series[-min_len:]
    ema5_series = ema5_series[-min_len:]
    ema6_series = ema6_series[-min_len:]

    close_aligned = closes[-min_len:]

    # ============================
    # ATR
    # ============================
    tr_list = []

    for i in range(1, len(candles)):
        high_price = float(candles[i][2])
        low_price = float(candles[i][3])
        prev_close_price = float(candles[i - 1][4])

        tr = max(
            high_price - low_price,
            abs(high_price - prev_close_price),
            abs(low_price - prev_close_price)
        )
        tr_list.append(tr)

    if len(tr_list) < ATR_LENGTH:
        atr = None
    else:
        atr = sum(tr_list[-ATR_LENGTH:]) / ATR_LENGTH

    e1_prev, e1_now = ultimos_dos(ema1_series)
    e2_prev, e2_now = ultimos_dos(ema2_series)
    e3_prev, e3_now = ultimos_dos(ema3_series)
    e4_prev, e4_now = ultimos_dos(ema4_series)
    e5_prev, e5_now = ultimos_dos(ema5_series)
    e6_prev, e6_now = ultimos_dos(ema6_series)

    close_prev, close_now = ultimos_dos(close_aligned)

    bull_raw_series = []
    bear_raw_series = []

    for i in range(min_len):
        bull_raw = (
            ema1_series[i] > ema2_series[i] > ema3_series[i] >
            ema4_series[i] > ema5_series[i] > ema6_series[i]
        )
        bear_raw = (
            ema1_series[i] < ema2_series[i] < ema3_series[i] <
            ema4_series[i] < ema5_series[i] < ema6_series[i]
        )
        bull_raw_series.append(bull_raw)
        bear_raw_series.append(bear_raw)

    bull_prev = bull_raw_series[-2]
    bear_prev = bear_raw_series[-2]
    bull_now = bull_raw_series[-1]
    bear_now = bear_raw_series[-1]

    # Pendiente actual
    bull_slope_ok = e1_now > e1_prev and e2_now > e2_prev and e6_now > e6_prev
    bear_slope_ok = e1_now < e1_prev and e2_now < e2_prev and e6_now < e6_prev

    # Pendiente previa
    bull_slope_prev_ok = (
        e1_prev > ema1_series[-3] and
        e2_prev > ema2_series[-3] and
        e6_prev > ema6_series[-3]
    )
    bear_slope_prev_ok = (
        e1_prev < ema1_series[-3] and
        e2_prev < ema2_series[-3] and
        e6_prev < ema6_series[-3]
    )

    # Spread actual
    spread_abs = abs(e1_now - e6_now)
    spread_rel = spread_abs / close_now if close_now != 0 else 0
    spread_ok = spread_rel >= MIN_SPREAD_PERC

    # Filtro ATR
    atr_ok = True
    if USE_ATR_FILTER and atr is not None:
        avg_price = sum(close_aligned[-ATR_LENGTH:]) / ATR_LENGTH
        atr_ratio = atr / avg_price
        atr_ok = atr_ratio > (0.0005 * ATR_MIN_MULT)

    # Spread previo
    spread_abs_prev = abs(e1_prev - e6_prev)
    spread_rel_prev = spread_abs_prev / close_prev if close_prev != 0 else 0
    spread_prev_ok = spread_rel_prev >= MIN_SPREAD_PERC

    # Persistencia
    bull_persist_bars = barras_desde_false(bull_raw_series)
    bear_persist_bars = barras_desde_false(bear_raw_series)

    bull_persist_ok = (not USE_PERSISTENCE_FILTER) or (bull_persist_bars > BARS_FOR_TREND_HOLD)
    bear_persist_ok = (not USE_PERSISTENCE_FILTER) or (bear_persist_bars > BARS_FOR_TREND_HOLD)

    # Tendencia fuerte actual
    bull_strong = bull_now
    bear_strong = bear_now

    if USE_SLOPE_FILTER:
        bull_strong = bull_strong and bull_slope_ok
        bear_strong = bear_strong and bear_slope_ok

    if USE_SPREAD_FILTER:
        bull_strong = bull_strong and spread_ok
        bear_strong = bear_strong and spread_ok

    if USE_PERSISTENCE_FILTER:
        bull_strong = bull_strong and bull_persist_ok
        bear_strong = bear_strong and bear_persist_ok

    # Tendencia fuerte previa
    bull_prev_strong = bull_prev
    bear_prev_strong = bear_prev

    if USE_SLOPE_FILTER:
        bull_prev_strong = bull_prev_strong and bull_slope_prev_ok
        bear_prev_strong = bear_prev_strong and bear_slope_prev_ok

    if USE_SPREAD_FILTER:
        bull_prev_strong = bull_prev_strong and spread_prev_ok
        bear_prev_strong = bear_prev_strong and spread_prev_ok

    if USE_PERSISTENCE_FILTER:
        bull_prev_strong = bull_prev_strong and bull_persist_ok
        bear_prev_strong = bear_prev_strong and bear_persist_ok

    # Nuevas tendencias
    new_bull = bull_strong and not bull_prev_strong
    new_bear = bear_strong and not bear_prev_strong

    # Precio respecto a la cinta
    price_above_ribbon = close_now > e1_now and close_now > e6_now
    price_below_ribbon = close_now < e1_now and close_now < e6_now

    # ============================
    # VELA DE IMPULSO
    # ============================
    last_candle = candles[-1]

    open_price = float(last_candle[1])
    high_price = float(last_candle[2])
    low_price = float(last_candle[3])
    close_price = float(last_candle[4])

    body = abs(close_price - open_price)
    range_candle = high_price - low_price

    if range_candle == 0:
        body_ratio = 0
    else:
        body_ratio = body / range_candle

    bull_candle = close_price > open_price
    bear_candle = close_price < open_price

    impulse_ok = True
    if USE_IMPULSE_FILTER:
        impulse_ok = body_ratio >= MIN_BODY_RATIO

    long_signal = new_bull and price_above_ribbon and atr_ok and impulse_ok and bull_candle
    short_signal = new_bear and price_below_ribbon and atr_ok and impulse_ok and bear_candle

    return {
        "close": close_now,
        "ema1": e1_now,
        "ema6": e6_now,
        "bull_now": bull_strong,
        "bear_now": bear_strong,
        "new_bull": new_bull,
        "new_bear": new_bear,
        "long_signal": long_signal,
        "short_signal": short_signal,
        "spread_rel": spread_rel,
        "bull_slope_ok": bull_slope_ok,
        "bear_slope_ok": bear_slope_ok,
        "bull_persist_bars": bull_persist_bars,
        "bear_persist_bars": bear_persist_bars,
        "atr": atr,
        "atr_ok": atr_ok,
        "body_ratio": body_ratio,
        "impulse_ok": impulse_ok,
        "bull_candle": bull_candle,
        "bear_candle": bear_candle
    }

# ==============================
# BOT
# ==============================
def main():
    ultima_senal = None

    enviar_mensaje("🤖 Bravus Bot iniciado correctamente.")

    while True:
        try:
            data = analizar_bravus()

            precio = round(data["close"], 2)
            ema1 = round(data["ema1"], 2)
            ema6 = round(data["ema6"], 2)

            print(
                f"Precio: {precio} | EMA1: {ema1} | EMA6: {ema6} | "
                f"spread: {round(data['spread_rel'], 5)} | "
                f"ATR: {round(data['atr'], 2) if data['atr'] else None} | atrOK: {data['atr_ok']} | "
                f"bodyRatio: {round(data['body_ratio'], 2)} | impulseOK: {data['impulse_ok']} | "
                f"bullSlope: {data['bull_slope_ok']} | bearSlope: {data['bear_slope_ok']} | "
                f"bullBars: {data['bull_persist_bars']} | bearBars: {data['bear_persist_bars']} | "
                f"newBull: {data['new_bull']} | newBear: {data['new_bear']}"
            )

            if data["long_signal"]:
                senal = "buy"
                mensaje = (
                    f"🟢 BRAVUS BOT - BUY\n"
                    f"Par: {PAIR}\n"
                    f"Precio: {precio}\n"
                    f"EMA1: {ema1}\n"
                    f"EMA6: {ema6}\n"
                    f"Tendencia alcista nueva confirmada."
                )
            elif data["short_signal"]:
                senal = "sell"
                mensaje = (
                    f"🔴 BRAVUS BOT - SELL\n"
                    f"Par: {PAIR}\n"
                    f"Precio: {precio}\n"
                    f"EMA1: {ema1}\n"
                    f"EMA6: {ema6}\n"
                    f"Tendencia bajista nueva confirmada."
                )
            else:
                senal = "neutral"
                mensaje = None

            if mensaje and senal != ultima_senal:
                enviar_mensaje(mensaje)
                print("Mensaje enviado a Telegram")
                ultima_senal = senal
            elif senal == "neutral":
                ultima_senal = None

        except Exception as e:
            print("Error en Bravus Bot:", e)

        time.sleep(CHECK_EVERY_SECONDS)

if __name__ == "__main__":
    main()