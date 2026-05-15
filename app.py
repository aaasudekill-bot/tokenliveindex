from flask import Flask, render_template, request, jsonify
import requests

app = Flask(__name__)

# Route untuk menampilkan halaman utama
@app.route('/')
def home():
    return render_template('index.html')

# Route API untuk memproses perhitungan (Dipanggil oleh Javascript nanti)
@app.route('/api/convert', methods=['GET'])
def convert():
    # Mengambil data dari inputan user
    crypto_id = request.args.get('crypto') # contoh: bitcoin
    fiat_currency = request.args.get('fiat') # contoh: usd atau idr
    amount = float(request.args.get('amount')) # contoh: 1.5

    # Mengambil data harga real-time dari CoinGecko (GRATIS, tanpa API key)
    url = f"https://api.coingecko.com/api/v3/simple/price?ids={crypto_id}&vs_currencies={fiat_currency}"
    
    try:
        response = requests.get(url)
        data = response.json()
        
        # Cek apakah datanya valid
        if crypto_id in data and fiat_currency in data[crypto_id]:
            price = data[crypto_id][fiat_currency]
            total = amount * price
            
            return jsonify({
                "status": "success",
                "price": price,
                "total": round(total, 2), # Dibulatkan 2 desimal
                "fiat": fiat_currency.upper()
            })
        else:
            return jsonify({"status": "error", "message": "Data tidak ditemukan"})
            
    except Exception as e:
        return jsonify({"status": "error", "message": "Gagal terhubung ke server CoinGecko"})

if __name__ == '__main__':
    app.run(debug=True)
