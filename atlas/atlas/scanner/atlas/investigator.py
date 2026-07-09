import yfinance as yf

WATCHLIST = [
    "SOXL",
    "NVDA",
    "PLTR",
    "MARA",
    "CCJ",
    "KGC"
]

print("=" * 40)
print("ATLAS - PRIMER ESCANEO")
print("=" * 40)

for ticker in WATCHLIST:

    try:
        stock = yf.Ticker(ticker)

        hist = stock.history(period="2d")

        last = hist["Close"].iloc[-1]
        prev = hist["Close"].iloc[-2]

        change = ((last - prev) / prev) * 100

        print(f"{ticker:6} ${last:.2f} {change:.2f}%")

    except Exception:
        print(f"{ticker} Error")