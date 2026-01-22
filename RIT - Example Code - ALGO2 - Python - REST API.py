# This is a python example algorithm using REST API for the RIT ALGO2 Case
import signal
import requests
from time import sleep

# this class definition allows us to print error messages and stop the program when needed
class ApiException(Exception):
    pass

BASE_SPREAD = 0.02          # start here (like your SPREAD)
MIN_SPREAD  = 0.005         # don't go tighter than this (avoid giving away edge)
MAX_SPREAD  = 0.08          # don't go wider than this (or you'll never fill)

NO_FILL_TICKS_TO_TIGHTEN = 5   # if no fills for 5 ticks, tighten
TIGHTEN_FACTOR = 0.85          # multiply spread by this to tighten
WIDEN_FACTOR   = 1.20          # widen after fills (optional)

# this signal handler allows for a graceful shutdown when CTRL+C is pressed
def signal_handler(signum, frame):
    global shutdown
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    shutdown = True

# set your API key to authenticate to the RIT client
API_KEY = {'X-API-Key': 'YOUR API KEY HERE'}
shutdown = False
# other settings for market making algo
SPREAD = 0.02
BUY_VOLUME = 500
SELL_VOLUME = 500

# this helper method returns the current 'tick' of the running case
def get_tick(session):
    resp = session.get('http://localhost:9999/v1/case')
    if resp.status_code == 401:
        raise ApiException('The API key provided in this Python code must match that in the RIT client (please refer to the API hyperlink in the client toolbar and/or the RIT – User Guide – REST API Documentation.pdf)')
    case = resp.json()
    return case['tick']

# this helper method returns the last close price for the given security, one tick ago
def ticker_close(session, ticker):
    payload = {'ticker': ticker, 'limit': 1}
    resp = session.get('http://localhost:9999/v1/securities/history', params=payload)
    if resp.status_code == 401:
        raise ApiException('The API key provided in this Python code must match that in the RIT client (please refer to the API hyperlink in the client toolbar and/or the RIT – User Guide – REST API Documentation.pdf)')
    ticker_history = resp.json()
    if ticker_history:
        return ticker_history[0]['close']
    else:
        raise ApiException('Response error. Unexpected JSON response.')

# this helper method submits a pair of limit orders to buy and sell VOLUME of each security, at the last price +/- SPREAD
def buy_sell(session, to_buy, to_sell, last):
    buy_payload = {'ticker': to_buy, 'type': 'LIMIT', 'quantity': BUY_VOLUME, 'action': 'BUY', 'price': last - SPREAD}
    sell_payload = {'ticker': to_sell, 'type': 'LIMIT', 'quantity': SELL_VOLUME, 'action': 'SELL', 'price': last + SPREAD}
    session.post('http://localhost:9999/v1/orders', params=buy_payload)
    session.post('http://localhost:9999/v1/orders', params=sell_payload)

# this helper method gets all the orders of a given type (OPEN/TRANSACTED/CANCELLED)
def get_orders(session, status):
    payload = {'status': status}
    resp = session.get('http://localhost:9999/v1/orders', params=payload)
    if resp.status_code == 401:
        raise ApiException('The API key provided in this Python code must match that in the RIT client (please refer to the API hyperlink in the client toolbar and/or the RIT – User Guide – REST API Documentation.pdf)')
    orders = resp.json()
    return orders

def get_top_of_book(session, ticker):
    # Common RIT endpoint; adjust if your API differs
    resp = session.get('http://localhost:9999/v1/securities/book', params={'ticker': ticker})
    if resp.status_code == 401:
        raise ApiException("Bad API key.")
    book = resp.json()

    # These key names sometimes differ across setups—adjust if needed
    bids = book.get('bids', [])
    asks = book.get('asks', [])

    best_bid = bids[0]['price'] if bids else None
    best_ask = asks[0]['price'] if asks else None
    return best_bid, best_ask

def get_position(session, ticker):
    # This endpoint/key may differ in your RIT client; adjust if needed.
    # Some versions: GET /v1/securities?ticker=ALGO returns a list of securities with 'position'.
    resp = session.get('http://localhost:9999/v1/securities', params={'ticker': ticker})
    if resp.status_code == 401:
        raise ApiException("Bad API key.")
    data = resp.json()

    # Often it's a list with one dict
    if isinstance(data, list) and len(data) > 0:
        return data[0].get('position', 0)
    # Sometimes it's a dict
    if isinstance(data, dict):
        return data.get('position', 0)

    return 0

def cancel_order(session, order_id):
    # Some RIT APIs cancel by POST /v1/orders/cancel or DELETE /v1/orders/{id}
    # Adjust to match your client. This is a common pattern:
    session.post('http://localhost:9999/v1/commands/cancel', params={'id': order_id})

def place_limit(session, ticker, side, qty, price):
    payload = {'ticker': ticker, 'type': 'LIMIT', 'quantity': qty, 'action': side, 'price': price}
    session.post('http://localhost:9999/v1/orders', params=payload)
    
def count_transacted(session):
    transacted = get_orders(session, 'TRANSACTED')
    return len(transacted)

def main():
    global shutdown
    TICKER = 'ALGO'

    with requests.Session() as s:
        s.headers.update(API_KEY)

        tick = get_tick(s)

        # Adaptive state
        spread = BASE_SPREAD
        last_transacted_count = count_transacted(s)
        last_fill_tick = tick

        while (not shutdown) and (tick > 5) and (tick < 295):

            # Read current state
            open_orders = get_orders(s, 'OPEN')
            algo_close = ticker_close(s, TICKER)

            # Detect fills by checking transacted count
            transacted_count = count_transacted(s)
            got_fill = (transacted_count > last_transacted_count)
            if got_fill:
                last_transacted_count = transacted_count
                last_fill_tick = tick

                # Optional: after getting fills, widen a bit to reduce adverse selection
                spread = min(MAX_SPREAD, spread * WIDEN_FACTOR)

            # If no fills for a while, tighten (become more competitive)
            if (tick - last_fill_tick) >= NO_FILL_TICKS_TO_TIGHTEN:
                spread = max(MIN_SPREAD, spread * TIGHTEN_FACTOR)
                # reset the clock so we don't tighten every single loop tick
                last_fill_tick = tick

            # --- Order management (keep it close to your original style) ---

            # If no open orders, place a pair
            if len(open_orders) == 0:
                buy_payload = {
                    'ticker': TICKER, 'type': 'LIMIT', 'quantity': BUY_VOLUME,
                    'action': 'BUY', 'price': algo_close - spread
                }
                sell_payload = {
                    'ticker': TICKER, 'type': 'LIMIT', 'quantity': SELL_VOLUME,
                    'action': 'SELL', 'price': algo_close + spread
                }
                s.post('http://localhost:9999/v1/orders', params=buy_payload)
                s.post('http://localhost:9999/v1/orders', params=sell_payload)
                sleep(1)

            else:
                # If your open orders aren't exactly a clean pair, cancel & re-quote
                # (You can improve this later to cancel only the wrong side.)
                if len(open_orders) != 2:
                    s.post('http://localhost:9999/v1/commands/cancel?all=1')
                    sleep(1)

                # Re-quote periodically so you follow the market (and apply new spread)
                # Simple rule: always cancel+replace each loop (still crude but works)
                else:
                    s.post('http://localhost:9999/v1/commands/cancel?all=1')
                    sleep(0.2)

                    buy_payload = {
                        'ticker': TICKER, 'type': 'LIMIT', 'quantity': BUY_VOLUME,
                        'action': 'BUY', 'price': algo_close - spread
                    }
                    sell_payload = {
                        'ticker': TICKER, 'type': 'LIMIT', 'quantity': SELL_VOLUME,
                        'action': 'SELL', 'price': algo_close + spread
                    }
                    s.post('http://localhost:9999/v1/orders', params=buy_payload)
                    s.post('http://localhost:9999/v1/orders', params=sell_payload)

            tick = get_tick(s)

# this calls the main() method when you type 'python algo2.py' into the command prompt
if __name__ == '__main__':
    signal.signal(signal.SIGINT, signal_handler)
    main()
