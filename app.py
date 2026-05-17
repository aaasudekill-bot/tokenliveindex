import os
import datetime
import atexit
from flask import Flask, request, jsonify
import requests
from flask_caching import Cache
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from apscheduler.schedulers.background import BackgroundScheduler

app = Flask(__name__)

# ==========================================
# 1. KONFIGURASI UTAMA
# ==========================================
COINGECKO_API_KEY = "CG-5q1PNRDHjFvyNPyx4RdZjJkb"

# CORS (Wajib agar Next.js bisa meminta data)
CORS(app)

# Cache
app.config['CACHE_TYPE'] = 'SimpleCache'
app.config['CACHE_DEFAULT_TIMEOUT'] = 300
cache = Cache(app)

# Database SQLite (Untuk AI Predictions)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///predictions.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# ==========================================
# 2. MODEL DATABASE
# ==========================================
class Prediction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    crypto_id = db.Column(db.String(50), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    price_at_pred = db.Column(db.Float, nullable=False)
    prediction = db.Column(db.String(10), nullable=False) # 'UP' atau 'DOWN'
    price_at_result = db.Column(db.Float, nullable=True)
    status = db.Column(db.String(10), default='PENDING') # PENDING, WIN, LOSE

# Buat database jika belum ada
with app.app_context():
    db.create_all()

# ==========================================
# 3. HELPER: KONVERSI MATA UANG
# ==========================================
def get_exchange_rate(target_fiat):
    if target_fiat == 'usd': return 1.0
    try:
        url = f"https://api.coingecko.com/api/v3/simple/price?ids=usd&vs_currencies={target_fiat}"
        headers = {"x-cg-demo-api-key": COINGECKO_API_KEY}
        res = requests.get(url, headers=headers, timeout=8)
        if res.status_code == 200:
            data = res.json()
            if 'usd' in data and target_fiat in data['usd']: return data['usd'][target_fiat]
    except Exception: pass
    return None

# ==========================================
# 4. API: KALKULATOR KONVERSI
# ==========================================
@app.route('/api/convert', methods=['GET'])
def convert():
    crypto_id = request.args.get('crypto_id')
    fiat_currency = request.args.get('fiat_currency', 'usd')
    amount = request.args.get('amount', '1')
    try: amount = float(amount)
    except ValueError: return jsonify({"status": "error", "message": "Invalid amount"})

    url = f"https://api.coingecko.com/api/v3/simple/price?ids={crypto_id}&vs_currencies={fiat_currency}"
    headers = {"x-cg-demo-api-key": COINGECKO_API_KEY}
    try:
        response = requests.get(url, headers=headers, timeout=8)
        data = response.json()
        if crypto_id in data and fiat_currency in data[crypto_id]:
            price = data[crypto_id][fiat_currency]
            return jsonify({"status": "success", "price": price, "total": round(float(amount) * price, 2), "fiat": fiat_currency.upper()})
        else: return jsonify({"status": "error", "message": "Asset or currency not recognized"})
    except Exception: return jsonify({"status": "error", "message": "Failed to connect to pricing server"})

# ==========================================
# 5. API: DATA GRAFIK HISTORIS (3 TIER FALLBACK)
# ==========================================
binance_symbol_map = { 
    "bitcoin": "BTCUSDT", "ethereum": "ETHUSDT", "tether": "USDTUSDC", 
    "binancecoin": "BNBUSDT", "solana": "SOLUSDT", "ripple": "XRPUSDT", 
    "usd-coin": "USDCUSDT", "cardano": "ADAUSDT", "dogecoin": "DOGEUSDT", 
    "tron": "TRXUSDT", "avalanche-2": "AVAXUSDT", "polkadot": "DOTUSDT", 
    "chainlink": "LINKUSDT", "toncoin": "TONUSDT", "shiba-inu": "SHIBUSDT", 
    "litecoin": "LTCUSDT", "uniswap": "UNIUSDT", "stellar": "XLMUSDT" 
}

@cache.cached(timeout=300, query_string=True)
@app.route('/api/chart', methods=['GET'])
def get_chart_data():
    crypto_id = request.args.get('crypto_id', 'bitcoin')
    days = request.args.get('days', '7')
    fiat_currency = request.args.get('fiat_currency', 'usd')
    base_data = None; store_name = ""

    # JALUR 1: COINGECKO
    try:
        url = f"https://api.coingecko.com/api/v3/coins/{crypto_id}/market_chart?vs_currency={fiat_currency}&days={days}"
        headers = {"x-cg-demo-api-key": COINGECKO_API_KEY}
        response = requests.get(url, headers=headers, timeout=8)
        if response.status_code == 200:
            data = response.json()
            if 'prices' in data: base_data = [{"time": int(item[0] / 1000), "value": item[1]} for item in data['prices']]; store_name = "coingecko"
    except Exception: pass

    # JALUR 2: FALLBACK COINCAP
    if not base_data:
        try:
            url_cc = f"https://api.coincap.io/v2/assets/{crypto_id}/history?interval=h1"
            response_cc = requests.get(url_cc, timeout=8)
            if response_cc.status_code == 200:
                data_cc = response_cc.json()
                if 'data' in data_cc and len(data_cc['data']) > 0: base_data = [{"time": int(item['time'] / 1000), "value": float(item['priceUsd'])} for item in data_cc['data']]; store_name = "coincap"
        except Exception: pass

    # JALUR 3: FALLBACK BINANCE
    if not base_data:
        limit_map = {"7": 168, "14": 336, "30": 720, "90": 2160}
        try:
            symbol_bn = binance_symbol_map.get(crypto_id, f"{crypto_id.upper()}USDT"); limit = limit_map.get(days, 168)
            url_bn = f"https://api.binance.com/api/v3/klines?symbol={symbol_bn}&interval=1h&limit={limit}"
            response_bn = requests.get(url_bn, timeout=8)
            if response_bn.status_code == 200:
                data_bn = response_bn.json()
                if len(data_bn) > 2: base_data = [{"time": int(item[0] / 1000), "value": float(item[4])} for item in data_bn]; store_name = "binance"
        except Exception: pass

    # KONVERSI USD KE MATA UANG LAIN (JIKA DATA DARI FALLBACK)
    if base_data and fiat_currency != 'usd' and store_name != "coingecko":
        rate = get_exchange_rate(fiat_currency)
        if rate:
            for item in base_data: item['value'] = item['value'] * rate
            store_name = f"{store_name} (Converted to {fiat_currency.upper()})"

    if base_data: return jsonify({"status": "success", "data": base_data, "source": store_name})
    else: return jsonify({"status": "error", "message": "All data sources failed to load"})

# ==========================================
# 6. API: MARKET SENTIMEN
# ==========================================
@app.route('/api/sentimen')
def get_sentimen():
    url = "https://api.alternative.me/fng/?limit=30"
    try:
        response = requests.get(url, timeout=8); data = response.json()
        if 'data' in data: return jsonify({"status": "success", "current": data['data'][0], "history": data['data']})
        else: return jsonify({"status": "error", "message": "Sentiment data unavailable"})
    except Exception: return jsonify({"status": "error", "message": "Connection timeout"})

# ==========================================
# 7. API: PIVOT POINTS
# ==========================================
@app.route('/api/pivot')
def get_pivot_points():
    crypto_id = request.args.get('crypto_id', 'bitcoin')
    symbol = binance_symbol_map.get(crypto_id, "BTCUSDT")
    url_bn = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=1d&limit=1"
    try:
        response_bn = requests.get(url_bn, timeout=8)
        if response_bn.status_code == 200:
            data_bn = response_bn.json()[0]; high = float(data_bn[2]); low = float(data_bn[3]); close = float(data_bn[4])
            pp = (high + low + close) / 3; r1 = (2 * pp) - low; s1 = (2 * pp) - high; r2 = pp + (high - low); s2 = pp - (high - low)
            return jsonify({"status": "success", "data": {"pp": round(pp, 2), "r1": round(r1, 2), "r2": round(r2, 2), "s1": round(s1, 2), "s2": round(s2, 2)}})
    except Exception: pass
    return jsonify({"status": "error", "message": "Failed to calculate pivot points"})

# ==========================================
# 8. AI PREDICTION ENGINE & FALLBACK HELPERS
# ==========================================
SUPPORTED_COINS = [ "bitcoin", "ethereum", "tether", "binancecoin", "solana", "ripple", "usd-coin", "cardano", "dogecoin", "tron", "avalanche-2", "polkadot", "chainlink", "toncoin", "shiba-inu", "litecoin", "uniswap", "stellar" ]

def get_live_price(crypto_id):
    """Fallback 3 lapis untuk mendapatkan harga live"""
    # 1. CoinGecko
    try:
        url = f"https://api.coingecko.com/api/v3/simple/price?ids={crypto_id}&vs_currencies=usd"
        headers = {"x-cg-demo-api-key": COINGECKO_API_KEY}
        res = requests.get(url, headers=headers, timeout=8).json()
        if crypto_id in res and 'usd' in res[crypto_id]: return res[crypto_id]['usd']
    except: pass
    # 2. CoinCap
    try:
        url_cc = f"https://api.coincap.io/v2/assets/{crypto_id}"
        res_cc = requests.get(url_cc, timeout=8).json()
        if 'data' in res_cc and 'priceUsd' in res_cc['data']: return float(res_cc['data']['priceUsd'])
    except: pass
    # 3. Binance
    try:
        symbol = binance_symbol_map.get(crypto_id, f"{crypto_id.upper()}USDT")
        url_bn = f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}"
        res_bn = requests.get(url_bn, timeout=8).json()
        if 'price' in res_bn: return float(res_bn['price'])
    except: pass
    return None

def get_24h_history(crypto_id):
    """Fallback 3 lapis untuk mendapatkan data 24 jam"""
    # 1. CoinGecko
    try:
        url = f"https://api.coingecko.com/api/v3/coins/{crypto_id}/market_chart?vs_currency=usd&days=1"
        headers = {"x-cg-demo-api-key": COINGECKO_API_KEY}
        res = requests.get(url, headers=headers, timeout=8).json()
        if 'prices' in res and len(res['prices']) > 4: return res['prices']
    except: pass
    # 2. CoinCap
    try:
        url_cc = f"https://api.coincap.io/v2/assets/{crypto_id}/history?interval=h1"
        res_cc = requests.get(url_cc, timeout=8).json()
        if 'data' in res_cc and len(res_cc['data']) > 4:
            return [[int(item['time']), float(item['priceUsd'])] for item in res_cc['data']]
    except: pass
    # 3. Binance
    try:
        symbol = binance_symbol_map.get(crypto_id, f"{crypto_id.upper()}USDT")
        url_bn = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=1h&limit=24"
        res_bn = requests.get(url_bn, timeout=8).json()
        if len(res_bn) > 4:
            return [[int(item[0]), float(item[4])] for item in res_bn]
    except: pass
    return None

def ai_predict_job():
    """Fungsi Scheduler: Verifikasi, Hapus Expired, Buat Prediksi Baru"""
    with app.app_context():
        now = datetime.datetime.utcnow()
        one_hour_ago = now - datetime.timedelta(hours=1)

        # 1. CEK PREDIKSI LAMA
        pending_preds = Prediction.query.filter(Prediction.timestamp <= one_hour_ago, Prediction.status == 'PENDING').all()
        for pred in pending_preds:
            current_price = get_live_price(pred.crypto_id)
            if current_price:
                pred.price_at_result = current_price
                if pred.prediction == 'UP' and current_price > pred.price_at_pred: pred.status = 'WIN'
                elif pred.prediction == 'DOWN' and current_price < pred.price_at_pred: pred.status = 'WIN'
                else: pred.status = 'LOSE'
                db.session.commit()

        # 2. HAPUS DATA EXPIRED (24 JAM)
        expired_time = now - datetime.timedelta(hours=24)
        Prediction.query.filter(Prediction.timestamp < expired_time).delete()
        db.session.commit()

        # 3. BUAT PREDIKSI BARU
        for coin in SUPPORTED_COINS:
            history = get_24h_history(coin)
            if history:
                current_price = history[-1][1]
                price_4h_ago = history[-4][1] if len(history) > 4 else history[0][1]
                pred_direction = 'UP' if current_price > price_4h_ago else 'DOWN'
                
                new_pred = Prediction(crypto_id=coin, price_at_pred=current_price, prediction=pred_direction)
                db.session.add(new_pred)
        
        db.session.commit()
        print(f"[{now}] AI Prediction Job Completed!")

scheduler = BackgroundScheduler()
scheduler.add_job(func=ai_predict_job, trigger="cron", minute="5")
scheduler.start()
atexit.register(lambda: scheduler.shutdown())

# ==========================================
# 9. API: AI PREDICTIONS
# ==========================================
@app.route('/api/ai-predictions')
def get_ai_predictions():
    predictions = Prediction.query.order_by(Prediction.timestamp.desc()).all()
    result = []
    for p in predictions:
        result.append({ "crypto_id": p.crypto_id, "timestamp": p.timestamp.isoformat(), "price_at_pred": p.price_at_pred, "prediction": p.prediction, "price_at_result": p.price_at_result, "status": p.status })
    return jsonify({"status": "success", "data": result})

# ==========================================
# 10. START SERVER
# ==========================================
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
