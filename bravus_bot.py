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
BALANCE_FILE = "balance_log.csv"

# ==============================
# SIMULACIÓN
# ==============================
INITIAL_BALANCE = 1000.0
RISK_PER_TRADE = 0.01        # 1% del balance por trade
MAX_DAILY_LOSS_PERC = 0.03   # 3% pérdida diaria máxima
TAKER_FEE_RATE = 0.0005      # 0.05% por lado

# Filtros extra de eficiencia
MIN_ATR_PERC = 0.0004        # evita mercado muerto
COOLDOWN_TRADES = 2          # ciclos sin abrir trade tras cerrar uno

# ==============================
# ESTADO GLOBAL
# ==============================
open_trade = None

sim_balance = INITIAL_BALANCE
peak_balance = INITIAL_BALANCE
max_drawdown_perc = 0.0

current_day = datetime.now().date()
day_start_balance = INITIAL_BALANCE
daily_loss_amount = 0.0

trade_cooldown = 0

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
                "position_size",
                "pnl_bruto",
                "comisiones",
                "pnl_neto",
                "balance_antes",
                "balance_despues"
            ])

    if not os.path.exists(BALANCE_FILE):
        with open(BALANCE_FILE, mode="w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "fecha",
                "balance",
                "peak_balance",
                "drawdown_perc",
                "daily_loss_amount"
            ])

def guardar_balance():
    with open(BALANCE_FILE, mode="a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            round(sim_balance, 2),
            round(peak_balance, 2),
            round(max_drawdown_perc, 2),
            round(daily_loss_amount, 2)
        ])

def guardar_trade_cerrado(trade, exit_price, resultado):
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
            round(trade["position_size"], 8),
            round(trade["realized_gross_pnl"], 2),
            round(trade["fees_paid"], 2),
            round(trade["realized_net_pnl"], 2),
            round(trade["balance_before_trade"], 2),
            round(sim_balance, 2)
        ])

# ==============================
# CAPITAL / RIESGO
# ==============================
def enviar_resumen_diario():
    metricas = calcular_metricas_csv()

    if not metricas:
        return

    mensaje = (
        f"📊 RESUMEN DIARIO\n\n"
        f"Balance: {round(sim_balance, 2)} €\n"
        f"Net Profit: {metricas['net_profit']} €\n"
        f"Trades: {metricas['trades']}\n"
        f"Winrate: {metricas['winrate']}%\n"
        f"Profit Factor: {metricas['profit_factor']}\n"
        f"Drawdown max: {round(max_drawdown_perc, 2)}%"
    )

    enviar_mensaje(mensaje)

def reset_daily_loss_if_new_day():
    global current_day, day_start_balance, daily_loss_amount
    hoy = datetime.now().date()
    if hoy != current_day:
        enviar_resumen_diario()
        current_day = hoy
        day_start_balance = sim_balance
        daily_loss_amount = 0.0

def puede_operar_por_perdida_diaria():
    limite = day_start_balance * MAX_DAILY_LOSS_PERC
    return daily_loss_amount < limite

def actualizar_drawdown():
    global peak_balance, max_drawdown_perc

    if sim_balance > peak_balance:
        peak_balance = sim_balance

    if peak_balance > 0:
        dd = ((peak_balance - sim_balance) / peak_balance) * 100
        if dd > max_drawdown_perc:
            max_drawdown_perc = dd

def calcular_risk_amount():
    return sim_balance * RISK_PER_TRADE

def calcular_position_size(entry, sl):
    risk_per_unit = abs(entry - sl)
    if risk_per_unit <= 0:
        return 0.0
    return calcular_risk_amount() / risk_per_unit

def aplicar_balance_change(amount):
    global sim_balance, daily_loss_amount
    sim_balance += amount
    if amount < 0:
        daily_loss_amount += abs(amount)
    actualizar_drawdown()
    guardar_balance()

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

    atr_perc = atr / close_now if atr else 0

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
        "atr_perc": atr_perc,
        "spread_rel": spread_rel,
        "body_ratio": body_ratio,
        "htf": "ALCISTA" if htf_bull else "BAJISTA",
        "long": long_signal,
        "short": short_signal
    }

# ==============================
# GESTIÓN DE TRADE Y PNL
# ==============================
def aplicar_fee_entrada(entry_price, position_size):
    entry_fee = entry_price * position_size * TAKER_FEE_RATE
    aplicar_balance_change(-entry_fee)
    return entry_fee

def calcular_pnl_bruto(tipo, entry_price, exit_price, quantity):
    if tipo == "buy":
        return (exit_price - entry_price) * quantity
    return (entry_price - exit_price) * quantity

def cerrar_cantidad(exit_price, quantity):
    global open_trade

    gross_pnl = calcular_pnl_bruto(
        open_trade["type"],
        open_trade["entry"],
        exit_price,
        quantity
    )

    exit_fee = exit_price * quantity * TAKER_FEE_RATE
    net_pnl = gross_pnl - exit_fee

    open_trade["qty_remaining"] -= quantity
    open_trade["realized_gross_pnl"] += gross_pnl
    open_trade["fees_paid"] += exit_fee
    open_trade["realized_net_pnl"] += net_pnl

    aplicar_balance_change(net_pnl)

    return gross_pnl, exit_fee, net_pnl

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

    position_size = calcular_position_size(entry, sl_inicial)
    if position_size <= 0:
        return False

    block_size = position_size / 3.0
    entry_fee = aplicar_fee_entrada(entry, position_size)

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
        "tp3_hit": False,
        "position_size": position_size,
        "block_size": block_size,
        "qty_remaining": position_size,
        "entry_fee_paid": entry_fee,
        "fees_paid": entry_fee,
        "realized_gross_pnl": 0.0,
        "realized_net_pnl": -entry_fee,
        "balance_before_trade": sim_balance + entry_fee
    }
    return True

def gestionar_trade(precio_actual):
    global open_trade

    if open_trade is None:
        return None

    tipo = open_trade["type"]
    sl_actual = open_trade["sl_actual"]
    tp1 = open_trade["tp1"]
    tp2 = open_trade["tp2"]
    tp3 = open_trade["tp3"]
    block_size = open_trade["block_size"]

    if tipo == "buy":

        if not open_trade["tp1_hit"] and precio_actual >= tp1:
            open_trade["tp1_hit"] = True
            open_trade["bloques_restantes"] = 2
            open_trade["sl_actual"] = tp1

            _, fee, net = cerrar_cantidad(tp1, block_size)

            return {
                "mensaje": (
                    f"✅ TP1 alcanzado en BUY\n"
                    f"Precio de salida: {tp1}\n"
                    f"Bloque 1 cerrado\n"
                    f"Nuevo SL: {open_trade['sl_actual']}\n"
                    f"PnL neto bloque: {round(net, 2)} €\n"
                    f"Fee salida: {round(fee, 2)} €\n"
                    f"Quedan 2 bloques"
                ),
                "cerrar_trade": False
            }

        if open_trade["tp1_hit"] and not open_trade["tp2_hit"] and precio_actual >= tp2:
            open_trade["tp2_hit"] = True
            open_trade["bloques_restantes"] = 1
            open_trade["sl_actual"] = tp2

            _, fee, net = cerrar_cantidad(tp2, block_size)

            return {
                "mensaje": (
                    f"✅ TP2 alcanzado en BUY\n"
                    f"Precio de salida: {tp2}\n"
                    f"Bloque 2 cerrado\n"
                    f"Nuevo SL: {open_trade['sl_actual']}\n"
                    f"PnL neto bloque: {round(net, 2)} €\n"
                    f"Fee salida: {round(fee, 2)} €\n"
                    f"Queda 1 bloque"
                ),
                "cerrar_trade": False
            }

        if open_trade["tp2_hit"] and not open_trade["tp3_hit"] and precio_actual >= tp3:
            open_trade["tp3_hit"] = True

            _, fee, net = cerrar_cantidad(tp3, open_trade["qty_remaining"])
            guardar_trade_cerrado(open_trade, tp3, "TP3")
            open_trade = None

            return {
                "mensaje": (
                    f"🚀 TP3 alcanzado en BUY\n"
                    f"Precio de salida: {tp3}\n"
                    f"Bloque 3 cerrado\n"
                    f"PnL neto bloque: {round(net, 2)} €\n"
                    f"Fee salida: {round(fee, 2)} €\n"
                    f"Trade terminado"
                ),
                "cerrar_trade": True
            }

        if precio_actual <= sl_actual:
            if open_trade["tp2_hit"]:
                _, fee, net = cerrar_cantidad(sl_actual, open_trade["qty_remaining"])
                guardar_trade_cerrado(open_trade, sl_actual, "SL_despues_TP2")
                open_trade = None

                return {
                    "mensaje": (
                        f"⚠️ BUY cerrado por retroceso al TP2\n"
                        f"Precio de salida: {sl_actual}\n"
                        f"Se cierra el último bloque\n"
                        f"PnL neto bloque: {round(net, 2)} €\n"
                        f"Fee salida: {round(fee, 2)} €"
                    ),
                    "cerrar_trade": True
                }

            elif open_trade["tp1_hit"]:
                _, fee, net = cerrar_cantidad(sl_actual, open_trade["qty_remaining"])
                guardar_trade_cerrado(open_trade, sl_actual, "SL_despues_TP1")
                open_trade = None

                return {
                    "mensaje": (
                        f"⚠️ BUY cerrado por retroceso al TP1\n"
                        f"Precio de salida: {sl_actual}\n"
                        f"Se cierran los 2 bloques restantes\n"
                        f"PnL neto resto: {round(net, 2)} €\n"
                        f"Fee salida: {round(fee, 2)} €"
                    ),
                    "cerrar_trade": True
                }

            else:
                _, fee, net = cerrar_cantidad(sl_actual, open_trade["qty_remaining"])
                guardar_trade_cerrado(open_trade, sl_actual, "SL")
                open_trade = None

                return {
                    "mensaje": (
                        f"❌ BUY cerrado por SL inicial\n"
                        f"Precio de salida: {sl_actual}\n"
                        f"PnL neto trade: {round(net, 2)} €\n"
                        f"Fee salida: {round(fee, 2)} €"
                    ),
                    "cerrar_trade": True
                }

    elif tipo == "sell":

        if not open_trade["tp1_hit"] and precio_actual <= tp1:
            open_trade["tp1_hit"] = True
            open_trade["bloques_restantes"] = 2
            open_trade["sl_actual"] = tp1

            _, fee, net = cerrar_cantidad(tp1, block_size)

            return {
                "mensaje": (
                    f"✅ TP1 alcanzado en SELL\n"
                    f"Precio de salida: {tp1}\n"
                    f"Bloque 1 cerrado\n"
                    f"Nuevo SL: {open_trade['sl_actual']}\n"
                    f"PnL neto bloque: {round(net, 2)} €\n"
                    f"Fee salida: {round(fee, 2)} €\n"
                    f"Quedan 2 bloques"
                ),
                "cerrar_trade": False
            }

        if open_trade["tp1_hit"] and not open_trade["tp2_hit"] and precio_actual <= tp2:
            open_trade["tp2_hit"] = True
            open_trade["bloques_restantes"] = 1
            open_trade["sl_actual"] = tp2

            _, fee, net = cerrar_cantidad(tp2, block_size)

            return {
                "mensaje": (
                    f"✅ TP2 alcanzado en SELL\n"
                    f"Precio de salida: {tp2}\n"
                    f"Bloque 2 cerrado\n"
                    f"Nuevo SL: {open_trade['sl_actual']}\n"
                    f"PnL neto bloque: {round(net, 2)} €\n"
                    f"Fee salida: {round(fee, 2)} €\n"
                    f"Queda 1 bloque"
                ),
                "cerrar_trade": False
            }

        if open_trade["tp2_hit"] and not open_trade["tp3_hit"] and precio_actual <= tp3:
            open_trade["tp3_hit"] = True

            _, fee, net = cerrar_cantidad(tp3, open_trade["qty_remaining"])
            guardar_trade_cerrado(open_trade, tp3, "TP3")
            open_trade = None

            return {
                "mensaje": (
                    f"🚀 TP3 alcanzado en SELL\n"
                    f"Precio de salida: {tp3}\n"
                    f"Bloque 3 cerrado\n"
                    f"PnL neto bloque: {round(net, 2)} €\n"
                    f"Fee salida: {round(fee, 2)} €\n"
                    f"Trade terminado"
                ),
                "cerrar_trade": True
            }

        if precio_actual >= sl_actual:
            if open_trade["tp2_hit"]:
                _, fee, net = cerrar_cantidad(sl_actual, open_trade["qty_remaining"])
                guardar_trade_cerrado(open_trade, sl_actual, "SL_despues_TP2")
                open_trade = None

                return {
                    "mensaje": (
                        f"⚠️ SELL cerrado por retroceso al TP2\n"
                        f"Precio de salida: {sl_actual}\n"
                        f"Se cierra el último bloque\n"
                        f"PnL neto bloque: {round(net, 2)} €\n"
                        f"Fee salida: {round(fee, 2)} €"
                    ),
                    "cerrar_trade": True
                }

            elif open_trade["tp1_hit"]:
                _, fee, net = cerrar_cantidad(sl_actual, open_trade["qty_remaining"])
                guardar_trade_cerrado(open_trade, sl_actual, "SL_despues_TP1")
                open_trade = None

                return {
                    "mensaje": (
                        f"⚠️ SELL cerrado por retroceso al TP1\n"
                        f"Precio de salida: {sl_actual}\n"
                        f"Se cierran los 2 bloques restantes\n"
                        f"PnL neto resto: {round(net, 2)} €\n"
                        f"Fee salida: {round(fee, 2)} €"
                    ),
                    "cerrar_trade": True
                }

            else:
                _, fee, net = cerrar_cantidad(sl_actual, open_trade["qty_remaining"])
                guardar_trade_cerrado(open_trade, sl_actual, "SL")
                open_trade = None

                return {
                    "mensaje": (
                        f"❌ SELL cerrado por SL inicial\n"
                        f"Precio de salida: {sl_actual}\n"
                        f"PnL neto trade: {round(net, 2)} €\n"
                        f"Fee salida: {round(fee, 2)} €"
                    ),
                    "cerrar_trade": True
                }

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

    pnl_list = [float(r["pnl_neto"]) for r in rows]
    wins = [x for x in pnl_list if x > 0]
    losses = [x for x in pnl_list if x < 0]

    winrate = (len(wins) / len(pnl_list)) * 100 if pnl_list else 0
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0

    return {
        "trades": len(pnl_list),
        "winrate": round(winrate, 2),
        "profit_factor": round(profit_factor, 2),
        "net_profit": round(sum(pnl_list), 2)
    }

# ==============================
# BOT
# ==============================
def main():
    global open_trade, trade_cooldown

    inicializar_csv()
    guardar_balance()

    ultima_senal = None
    ultimo_envio = None

    enviar_mensaje(
        f"🚀 BRAVUS BOT PRO ACTIVADO\n"
        f"Par: {PAIR}\n"
        f"TF: {INTERVAL}m | HTF: {HTF_INTERVAL}m\n"
        f"Balance inicial: {round(sim_balance, 2)} €\n"
        f"Riesgo por trade: {round(RISK_PER_TRADE * 100, 2)}%\n"
        f"Comisión simulada por lado: {round(TAKER_FEE_RATE * 100, 4)}%"
    )

    while True:
        try:
            reset_daily_loss_if_new_day()

            data = analizar()

            precio = round(data["close"], 2)
            ema1 = round(data["ema1"], 2)
            ema6 = round(data["ema6"], 2)
            ema200 = round(data["ema200"], 2)
            atr = data["atr"]

            print(
                f"Precio: {precio} | EMA1: {ema1} | EMA6: {ema6} | EMA200: {ema200} | "
                f"ATR: {round(atr, 2) if atr else None} | ATR%: {round(data['atr_perc'], 5)} | "
                f"Spread: {round(data['spread_rel'], 5)} | BodyRatio: {round(data['body_ratio'], 2)} | "
                f"HTF: {data['htf']} | OpenTrade: {open_trade['type'] if open_trade else 'NO'} | "
                f"Cooldown: {trade_cooldown} | Balance: {round(sim_balance, 2)} € | "
                f"DD max: {round(max_drawdown_perc, 2)}%",
                flush=True
            )

            ahora = datetime.now()

            evento = gestionar_trade(precio)
            if evento:
                enviar_mensaje(
                    f"{evento['mensaje']}\n"
                    f"Hora: {ahora.strftime('%Y-%m-%d %H:%M:%S')}\n"
                    f"Balance actual: {round(sim_balance, 2)} €"
                )

                if evento["cerrar_trade"]:
                    trade_cooldown = COOLDOWN_TRADES

            if (
                open_trade is None
                and atr is not None
                and puede_operar_por_perdida_diaria()
                and trade_cooldown == 0
                and data["atr_perc"] > MIN_ATR_PERC
            ):
                if data["long"]:
                    senal = "buy"

                    sl_inicial = round(precio - atr * SL_ATR_MULT, 2)
                    tp1 = round(precio + atr * 1.0, 2)
                    tp2 = round(precio + atr * 2.0, 2)
                    tp3 = round(precio + atr * 3.0, 2)
                    position_size = calcular_position_size(precio, sl_inicial)
                    entry_fee_est = precio * position_size * TAKER_FEE_RATE

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
                        f"ATR%: {round(data['atr_perc'], 5)}\n"
                        f"BodyRatio: {round(data['body_ratio'], 2)}\n"
                        f"Tamaño posición: {round(position_size, 6)}\n"
                        f"Fee entrada estimada: {round(entry_fee_est, 2)} €\n"
                        f"Balance: {round(sim_balance, 2)} €"
                    )

                elif data["short"]:
                    senal = "sell"

                    sl_inicial = round(precio + atr * SL_ATR_MULT, 2)
                    tp1 = round(precio - atr * 1.0, 2)
                    tp2 = round(precio - atr * 2.0, 2)
                    tp3 = round(precio - atr * 3.0, 2)
                    position_size = calcular_position_size(precio, sl_inicial)
                    entry_fee_est = precio * position_size * TAKER_FEE_RATE

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
                        f"ATR%: {round(data['atr_perc'], 5)}\n"
                        f"BodyRatio: {round(data['body_ratio'], 2)}\n"
                        f"Tamaño posición: {round(position_size, 6)}\n"
                        f"Fee entrada estimada: {round(entry_fee_est, 2)} €\n"
                        f"Balance: {round(sim_balance, 2)} €"
                    )
                else:
                    senal = "neutral"
                    mensaje = None

                puede_enviar = (
                    ultimo_envio is None or
                    (ahora - ultimo_envio).total_seconds() > MINUTES_BETWEEN_SIGNALS * 60
                )

                if mensaje and senal != ultima_senal and puede_enviar:
                    trade_opened = abrir_trade(senal, precio, atr)
                    if trade_opened:
                        enviar_mensaje(mensaje)
                        print("ENVIADO:", mensaje, flush=True)
                        ultima_senal = senal
                        ultimo_envio = ahora

                if senal == "neutral":
                    ultima_senal = None

            if trade_cooldown > 0:
                trade_cooldown -= 1

            if not puede_operar_por_perdida_diaria():
                print("Límite de pérdida diaria alcanzado. No se abren más trades hoy.", flush=True)

            metricas = calcular_metricas_csv()
            if metricas:
                print(
                    f"Trades: {metricas['trades']} | "
                    f"Winrate: {metricas['winrate']}% | "
                    f"Profit Factor: {metricas['profit_factor']} | "
                    f"Net Profit: {metricas['net_profit']} € | "
                    f"Balance: {round(sim_balance, 2)} € | "
                    f"DD max: {round(max_drawdown_perc, 2)}%",
                    flush=True
                )

        except Exception as e:
            print("ERROR:", e, flush=True)

        time.sleep(CHECK_EVERY_SECONDS)

if __name__ == "__main__":
    main()
