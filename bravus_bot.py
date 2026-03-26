import requests
import time
import os
import csv
from datetime import datetime
from dotenv import load_dotenv

# ==============================
# CONFIG
# ==============================
load_dotenv()

TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

if not TOKEN or not CHAT_ID:
    raise ValueError("Faltan TELEGRAM_TOKEN o CHAT_ID en las variables de entorno")

PAIR = "BTCUSD"

INTERVAL = 1
HTF_INTERVAL = 5

# Ribbon
LEN_EMA1 = 30
LEN_EMA2 = 35
LEN_EMA3 = 40
LEN_EMA4 = 45
LEN_EMA5 = 50
LEN_EMA6 = 60

# Tendencia
LEN_EMA_TREND = 200

# Filtros
MIN_SPREAD_PERC = 0.0008
BARS_FOR_TREND_HOLD = 4
ATR_LENGTH = 14
ATR_MIN_MULT = 0.8
MIN_BODY_RATIO = 0.65

USE_SLOPE_FILTER = True
USE_SPREAD_FILTER = True
USE_PERSISTENCE_FILTER = True
USE_ATR_FILTER = True
USE_IMPULSE_FILTER = True

# Riesgo
SL_ATR_MULT = 1.5

CHECK_EVERY_SECONDS = 60
MINUTES_BETWEEN_SIGNALS = 30

TRADES_FILE = "trades.csv"

# ==============================
# ESTADO GLOBAL
# ==============================
open_trade = None

# ==============================
# TELEGRAM
# ==============================
def enviar_mensaje(texto: str):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    try:
        response = requests.post(
            url,
            data={"chat_id": CHAT_ID, "text": texto},
            timeout=20
        )
        response.raise_for_status()
    except Exception as e:
        print("Error Telegram:", e, flush=True)

# ==============================
# CSV
# ==============================
def inicializar_csv():
    if not os.path.exists(TRADES_FILE):
        with open(TRADES_FILE, mode="w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "fecha_apertura",
                "fecha_cierre",
                "pair",
                "tipo",
                "entry",
                "sl_inicial",
                "tp1",
                "tp2",
                "tp3",
                "exit",
                "resultado",
                "rr"
            ])

def guardar_trade_cerrado(trade, exit_price, resultado, rr):
    with open(TRADES_FILE, mode="a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            trade["open_time"],
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            trade["pair"],
            trade["type"],
            trade["entry"],
            trade["sl_inicial"],
            trade["tp1"],
            trade["tp2"],
            trade["tp3"],
            exit_price,
            resultado,
            rr
        ])

# ==============================
# DATOS
# ==============================
def obtener_ohlc(interval):
    url = f"https://api.kraken.com/0/public/OHLC?pair={PAIR}&interval={interval}"
    response = requests.get(url, timeout=15)
    data = response.json()

    if data.get("error"):
        raise ValueError(f"Error Kraken: {data['error']}")

    result = data["result"]
    pair_key = [k for k in result.keys() if k != "last"][0]

    # Quitamos la última vela porque puede estar en formación
    return result[pair_key][:-1]

# ==============================
# INDICADORES
# ==============================
def calcular_serie_ema(valores, periodo):
    if len(valores) < periodo:
        return []

    serie = []
    k = 2 / (periodo + 1)
    ema_val = sum(valores[:periodo]) / periodo
    serie.append(ema_val)

    for precio in valores[periodo:]:
        ema_val = precio * k + ema_val * (1 - k)
        serie.append(ema_val)

    return serie

def ultimos_dos(lista):
    if len(lista) < 2:
        return None, None
    return lista[-2], lista[-1]

def barras_desde_false(lista_booleana):
    contador = 0
    for valor in reversed(lista_booleana):
        if valor:
            contador += 1
        else:
            break
    return contador

def calcular_atr(candles, period=14):
    tr = []

    for i in range(1, len(candles)):
        high = float(candles[i][2])
        low = float(candles[i][3])
        prev_close = float(candles[i - 1][4])

        tr_val = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close)
        )
        tr.append(tr_val)

    if len(tr) < period:
        return None

    return sum(tr[-period:]) / period

# ==============================
# ANÁLISIS
# ==============================
def analizar():
    candles = obtener_ohlc(INTERVAL)
    candles_htf = obtener_ohlc(HTF_INTERVAL)

    if len(candles) < LEN_EMA_TREND + 5:
        raise ValueError("No hay suficientes velas en timeframe principal.")

    if len(candles_htf) < LEN_EMA_TREND + 5:
        raise ValueError("No hay suficientes velas en HTF.")

    closes = [float(c[4]) for c in candles]
    closes_htf = [float(c[4]) for c in candles_htf]

    ema1_series = calcular_serie_ema(closes, LEN_EMA1)
    ema2_series = calcular_serie_ema(closes, LEN_EMA2)
    ema3_series = calcular_serie_ema(closes, LEN_EMA3)
    ema4_series = calcular_serie_ema(closes, LEN_EMA4)
    ema5_series = calcular_serie_ema(closes, LEN_EMA5)
    ema6_series = calcular_serie_ema(closes, LEN_EMA6)
    ema200_series = calcular_serie_ema(closes, LEN_EMA_TREND)
    ema200_htf_series = calcular_serie_ema(closes_htf, LEN_EMA_TREND)

    min_len = min(
        len(ema1_series),
        len(ema2_series),
        len(ema3_series),
        len(ema4_series),
        len(ema5_series),
        len(ema6_series),
        len(ema200_series)
    )

    if min_len < 3 or len(ema200_htf_series) < 1:
        raise ValueError("No hay suficientes datos.")

    ema1_series = ema1_series[-min_len:]
    ema2_series = ema2_series[-min_len:]
    ema3_series = ema3_series[-min_len:]
    ema4_series = ema4_series[-min_len:]
    ema5_series = ema5_series[-min_len:]
    ema6_series = ema6_series[-min_len:]
    ema200_series = ema200_series[-min_len:]
    close_aligned = closes[-min_len:]

    e1_prev, e1_now = ultimos_dos(ema1_series)
    e2_prev, e2_now = ultimos_dos(ema2_series)
    e6_prev, e6_now = ultimos_dos(ema6_series)
    _, ema200_now = ultimos_dos(ema200_series)
    close_prev, close_now = ultimos_dos(close_aligned)

    close_htf = closes_htf[-1]
    ema200_htf = ema200_htf_series[-1]

    htf_bull = close_htf > ema200_htf
    htf_bear = close_htf < ema200_htf

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
    bull_now = bull_raw_series[-1]
    bear_prev = bear_raw_series[-2]
    bear_now = bear_raw_series[-1]

    bull_slope_ok = e1_now > e1_prev and e2_now > e2_prev and e6_now > e6_prev
    bear_slope_ok = e1_now < e1_prev and e2_now < e2_prev and e6_now < e6_prev

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

    spread_rel = abs(e1_now - e6_now) / close_now if close_now != 0 else 0
    spread_prev_rel = abs(e1_prev - e6_prev) / close_prev if close_prev != 0 else 0
    spread_ok = spread_rel >= MIN_SPREAD_PERC
    spread_prev_ok = spread_prev_rel >= MIN_SPREAD_PERC

    bull_persist_bars = barras_desde_false(bull_raw_series)
    bear_persist_bars = barras_desde_false(bear_raw_series)
    bull_persist_ok = bull_persist_bars >= BARS_FOR_TREND_HOLD
    bear_persist_ok = bear_persist_bars >= BARS_FOR_TREND_HOLD

    atr = calcular_atr(candles, ATR_LENGTH)
    atr_ok = True

    if USE_ATR_FILTER and atr is not None and len(close_aligned) >= ATR_LENGTH:
        avg_price = sum(close_aligned[-ATR_LENGTH:]) / ATR_LENGTH
        atr_ratio = atr / avg_price
        atr_ok = atr_ratio > (0.0005 * ATR_MIN_MULT)

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

    new_bull = bull_strong and not bull_prev_strong
    new_bear = bear_strong and not bear_prev_strong

    # Filtro de tendencia más fuerte
    trend_bull = close_now > ema200_now and e1_now > ema200_now
    trend_bear = close_now < ema200_now and e1_now < ema200_now

    last = candles[-1]
    open_p = float(last[1])
    high_p = float(last[2])
    low_p = float(last[3])
    close_p = float(last[4])

    body = abs(close_p - open_p)
    range_candle = high_p - low_p
    body_ratio = body / range_candle if range_candle != 0 else 0

    bull_candle = close_p > open_p
    bear_candle = close_p < open_p

    impulse_ok = True
    if USE_IMPULSE_FILTER:
        impulse_ok = body_ratio >= MIN_BODY_RATIO

    long_signal = (
        new_bull and
        trend_bull and
        htf_bull and
        atr_ok and
        impulse_ok and
        bull_candle
    )

    short_signal = (
        new_bear and
        trend_bear and
        htf_bear and
        atr_ok and
        impulse_ok and
        bear_candle
    )

    return {
        "close": close_now,
        "ema1": e1_now,
        "ema6": e6_now,
        "ema200": ema200_now,
        "atr": atr,
        "spread_rel": spread_rel,
        "body_ratio": body_ratio,
        "htf": "ALCISTA" if htf_bull else "BAJISTA",
        "long": long_signal,
        "short": short_signal
    }

# ==============================
# CONTROL TRADE ABIERTO POR 3 BLOQUES
# ==============================
def abrir_trade(tipo, entry, atr):
    global open_trade

    if tipo == "buy":
        sl_inicial = round(entry - atr * SL_ATR_MULT, 2)
        tp1 = round(entry + atr * 1.0, 2)
        tp2 = round(entry + atr * 2.0, 2)
        tp3 = round(entry + atr * 3.0, 2)
    else:
        sl_inicial = round(entry + atr * SL_ATR_MULT, 2)
        tp1 = round(entry - atr * 1.0, 2)
        tp2 = round(entry - atr * 2.0, 2)
        tp3 = round(entry - atr * 3.0, 2)

    open_trade = {
        "type": tipo,
        "entry": entry,
        "sl_inicial": sl_inicial,
        "sl_actual": sl_inicial,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "pair": PAIR,
        "open_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "bloques_restantes": 3,
        "tp1_hit": False,
        "tp2_hit": False,
        "tp3_hit": False
    }

def gestionar_trade(precio_actual):
    global open_trade

    if open_trade is None:
        return None

    tipo = open_trade["type"]
    sl_actual = open_trade["sl_actual"]
    tp1 = open_trade["tp1"]
    tp2 = open_trade["tp2"]
    tp3 = open_trade["tp3"]

    if tipo == "buy":

        if not open_trade["tp1_hit"] and precio_actual >= tp1:
            open_trade["tp1_hit"] = True
            open_trade["bloques_restantes"] = 2
            open_trade["sl_actual"] = tp1
            return (
                f"✅ TP1 alcanzado en BUY\n"
                f"Precio actual: {precio_actual}\n"
                f"Bloque 1 cerrado\n"
                f"Nuevo SL: {open_trade['sl_actual']}\n"
                f"Quedan 2 bloques"
            )

        if open_trade["tp1_hit"] and not open_trade["tp2_hit"] and precio_actual >= tp2:
            open_trade["tp2_hit"] = True
            open_trade["bloques_restantes"] = 1
            open_trade["sl_actual"] = tp2
            return (
                f"✅ TP2 alcanzado en BUY\n"
                f"Precio actual: {precio_actual}\n"
                f"Bloque 2 cerrado\n"
                f"Nuevo SL: {open_trade['sl_actual']}\n"
                f"Queda 1 bloque"
            )

        if open_trade["tp2_hit"] and not open_trade["tp3_hit"] and precio_actual >= tp3:
            open_trade["tp3_hit"] = True
            guardar_trade_cerrado(open_trade, precio_actual, "TP3", 3.0)
            open_trade = None
            return (
                f"🚀 TP3 alcanzado en BUY\n"
                f"Precio actual: {precio_actual}\n"
                f"Bloque 3 cerrado\n"
                f"Trade terminado"
            )

        if precio_actual <= sl_actual:
            if open_trade["tp2_hit"]:
                guardar_trade_cerrado(open_trade, precio_actual, "SL_despues_TP2", 2.0)
                open_trade = None
                return (
                    f"⚠️ BUY cerrado por retroceso al TP2\n"
                    f"Precio actual: {precio_actual}\n"
                    f"Se cierra el último bloque"
                )

            elif open_trade["tp1_hit"]:
                guardar_trade_cerrado(open_trade, precio_actual, "SL_despues_TP1", 1.0)
                open_trade = None
                return (
                    f"⚠️ BUY cerrado por retroceso al TP1\n"
                    f"Precio actual: {precio_actual}\n"
                    f"Se cierran los 2 bloques restantes"
                )

            else:
                guardar_trade_cerrado(open_trade, precio_actual, "SL", -1.0)
                open_trade = None
                return (
                    f"❌ BUY cerrado por SL inicial\n"
                    f"Precio actual: {precio_actual}"
                )

    elif tipo == "sell":

        if not open_trade["tp1_hit"] and precio_actual <= tp1:
            open_trade["tp1_hit"] = True
            open_trade["bloques_restantes"] = 2
            open_trade["sl_actual"] = tp1
            return (
                f"✅ TP1 alcanzado en SELL\n"
                f"Precio actual: {precio_actual}\n"
                f"Bloque 1 cerrado\n"
                f"Nuevo SL: {open_trade['sl_actual']}\n"
                f"Quedan 2 bloques"
            )

        if open_trade["tp1_hit"] and not open_trade["tp2_hit"] and precio_actual <= tp2:
            open_trade["tp2_hit"] = True
            open_trade["bloques_restantes"] = 1
            open_trade["sl_actual"] = tp2
            return (
                f"✅ TP2 alcanzado en SELL\n"
                f"Precio actual: {precio_actual}\n"
                f"Bloque 2 cerrado\n"
                f"Nuevo SL: {open_trade['sl_actual']}\n"
                f"Queda 1 bloque"
            )

        if open_trade["tp2_hit"] and not open_trade["tp3_hit"] and precio_actual <= tp3:
            open_trade["tp3_hit"] = True
            guardar_trade_cerrado(open_trade, precio_actual, "TP3", 3.0)
            open_trade = None
            return (
                f"🚀 TP3 alcanzado en SELL\n"
                f"Precio actual: {precio_actual}\n"
                f"Bloque 3 cerrado\n"
                f"Trade terminado"
            )

        if precio_actual >= sl_actual:
            if open_trade["tp2_hit"]:
                guardar_trade_cerrado(open_trade, precio_actual, "SL_despues_TP2", 2.0)
                open_trade = None
                return (
                    f"⚠️ SELL cerrado por retroceso al TP2\n"
                    f"Precio actual: {precio_actual}\n"
                    f"Se cierra el último bloque"
                )

            elif open_trade["tp1_hit"]:
                guardar_trade_cerrado(open_trade, precio_actual, "SL_despues_TP1", 1.0)
                open_trade = None
                return (
                    f"⚠️ SELL cerrado por retroceso al TP1\n"
                    f"Precio actual: {precio_actual}\n"
                    f"Se cierran los 2 bloques restantes"
                )

            else:
                guardar_trade_cerrado(open_trade, precio_actual, "SL", -1.0)
                open_trade = None
                return (
                    f"❌ SELL cerrado por SL inicial\n"
                    f"Precio actual: {precio_actual}"
                )

    return None

# ==============================
# MÉTRICAS CSV
# ==============================
def calcular_metricas_csv():
    if not os.path.exists(TRADES_FILE):
        return None

    with open(TRADES_FILE, mode="r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    if not rows:
        return None

    rr_list = [float(r["rr"]) for r in rows]
    wins = [x for x in rr_list if x > 0]
    losses = [x for x in rr_list if x < 0]

    winrate = (len(wins) / len(rr_list)) * 100 if rr_list else 0
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0

    return {
        "trades": len(rr_list),
        "winrate": round(winrate, 2),
        "profit_factor": round(profit_factor, 2)
    }

# ==============================
# BOT
# ==============================
def main():
    global open_trade

    inicializar_csv()

    ultima_senal = None
    ultimo_envio = None

    enviar_mensaje(
        f"🚀 BRAVUS BOT PRO ACTIVADO\n"
        f"Par: {PAIR}\n"
        f"TF: {INTERVAL}m | HTF: {HTF_INTERVAL}m"
    )

    while True:
        try:
            data = analizar()

            precio = round(data["close"], 2)
            ema1 = round(data["ema1"], 2)
            ema6 = round(data["ema6"], 2)
            ema200 = round(data["ema200"], 2)
            atr = data["atr"]

            print(
                f"Precio: {precio} | EMA1: {ema1} | EMA6: {ema6} | EMA200: {ema200} | "
                f"ATR: {round(atr, 2) if atr else None} | Spread: {round(data['spread_rel'], 5)} | "
                f"BodyRatio: {round(data['body_ratio'], 2)} | HTF: {data['htf']} | "
                f"OpenTrade: {open_trade['type'] if open_trade else 'NO'}",
                flush=True
            )

            ahora = datetime.now()

            cierre = gestionar_trade(precio)
            if cierre:
                enviar_mensaje(
                    f"{cierre}\n"
                    f"Hora: {ahora.strftime('%Y-%m-%d %H:%M:%S')}\n"
                    f"Precio actual: {precio}"
                )

            if open_trade is None and atr is not None:
                if data["long"]:
                    senal = "buy"

                    sl_inicial = round(precio - atr * SL_ATR_MULT, 2)
                    tp1 = round(precio + atr * 1.0, 2)
                    tp2 = round(precio + atr * 2.0, 2)
                    tp3 = round(precio + atr * 3.0, 2)

                    mensaje = (
                        f"🟢 BRAVUS BOT PRO - BUY\n"
                        f"Par: {PAIR}\n"
                        f"Hora: {ahora.strftime('%Y-%m-%d %H:%M:%S')}\n"
                        f"Precio: {precio}\n"
                        f"SL inicial: {sl_inicial}\n"
                        f"TP1: {tp1}\n"
                        f"TP2: {tp2}\n"
                        f"TP3: {tp3}\n"
                        f"EMA200: {ema200}\n"
                        f"HTF: {data['htf']}\n"
                        f"Spread: {round(data['spread_rel'], 5)}\n"
                        f"ATR: {round(atr, 2)}\n"
                        f"BodyRatio: {round(data['body_ratio'], 2)}"
                    )

                elif data["short"]:
                    senal = "sell"

                    sl_inicial = round(precio + atr * SL_ATR_MULT, 2)
                    tp1 = round(precio - atr * 1.0, 2)
                    tp2 = round(precio - atr * 2.0, 2)
                    tp3 = round(precio - atr * 3.0, 2)

                    mensaje = (
                        f"🔴 BRAVUS BOT PRO - SELL\n"
                        f"Par: {PAIR}\n"
                        f"Hora: {ahora.strftime('%Y-%m-%d %H:%M:%S')}\n"
                        f"Precio: {precio}\n"
                        f"SL inicial: {sl_inicial}\n"
                        f"TP1: {tp1}\n"
                        f"TP2: {tp2}\n"
                        f"TP3: {tp3}\n"
                        f"EMA200: {ema200}\n"
                        f"HTF: {data['htf']}\n"
                        f"Spread: {round(data['spread_rel'], 5)}\n"
                        f"ATR: {round(atr, 2)}\n"
                        f"BodyRatio: {round(data['body_ratio'], 2)}"
                    )
                else:
                    senal = "neutral"
                    mensaje = None

                puede_enviar = (
                    ultimo_envio is None or
                    (ahora - ultimo_envio).total_seconds() > MINUTES_BETWEEN_SIGNALS * 60
                )

                if mensaje and senal != ultima_senal and puede_enviar:
                    enviar_mensaje(mensaje)
                    print("ENVIADO:", mensaje, flush=True)
                    abrir_trade(senal, precio, atr)
                    ultima_senal = senal
                    ultimo_envio = ahora

                if senal == "neutral":
                    ultima_senal = None

            metricas = calcular_metricas_csv()
            if metricas:
                print(
                    f"Trades: {metricas['trades']} | "
                    f"Winrate: {metricas['winrate']}% | "
                    f"Profit Factor: {metricas['profit_factor']}",
                    flush=True
                )

        except Exception as e:
            print("ERROR:", e, flush=True)

        time.sleep(CHECK_EVERY_SECONDS)

if __name__ == "__main__":
    main()
