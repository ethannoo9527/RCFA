# This is a python example algorithm using REST API for the RIT ALGO2 Case
import signal
import requests
from time import sleep

# this class definition allows us to print error messages and stop the program when needed
class ApiException(Exception):
    pass

# this signal handler allows for a graceful shutdown when CTRL+C is pressed
def signal_handler(signum, frame):
    global shutdown
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    shutdown = True

# set your API key to authenticate to the RIT client
API_KEY = {'X-API-Key': 'EZ91106P'}
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

def main():
    global shutdown

    TICKER = 'ALGO'

    # --- New risk/quote knobs ---
    MAX_POS = 2000            # hard inventory limit
    BASE_EDGE = 0.01          # your minimum edge per side (like half-spread)
    REQUOTE_TOL = 0.01        # only replace if we're off by >= this much
    MIN_MARKET_SPREAD = 0.02  # don’t quote if market spread too tiny (edge gone)
    SLEEP_SEC = 0.25

    with requests.Session() as s:
        s.headers.update(API_KEY)

        tick = get_tick(s)

        while (not shutdown) and (tick > 5) and (tick < 295):
            # 1) Read market
            best_bid, best_ask = get_top_of_book(s, TICKER)

            # If book is empty or broken, just wait
            if best_bid is None or best_ask is None or best_ask <= best_bid:
                sleep(SLEEP_SEC)
                tick = get_tick(s)
                continue

            market_spread = best_ask - best_bid
            mid = (best_bid + best_ask) / 2.0

            # Optional: if the market spread is too tight, no room to make edge
            if market_spread < MIN_MARKET_SPREAD:
                sleep(SLEEP_SEC)
                tick = get_tick(s)
                continue

            # 2) Risk state
            pos = get_position(s, TICKER)

            # If too long/short, stop quoting the side that increases risk
            allow_buy = (pos < MAX_POS)
            allow_sell = (pos > -MAX_POS)

            # 3) Compute dynamic quotes (simple version)
            # Edge: at least BASE_EDGE, but also respect a fraction of the market spread
            edge = max(BASE_EDGE, 0.25 * market_spread)

            # Inventory skew: push quotes to reduce inventory
            # If long (+pos): push both quotes DOWN to encourage selling / discourage buying
            # If short (-pos): push both quotes UP to encourage buying / discourage selling
            k = 0.00001  # tune this (bigger = more aggressive inventory control)
            skew = k * pos

            desired_bid = (mid - edge) - skew
            desired_ask = (mid + edge) - skew

            # 4) Read your open orders and identify current bid/ask
            open_orders = get_orders(s, 'OPEN')

            my_bid = None
            my_ask = None
            for o in open_orders:
                if o.get('ticker') != TICKER:
                    continue
                if o.get('action') == 'BUY':
                    # if multiple, keep the best-priced one and cancel others later
                    if (my_bid is None) or (o.get('price', -1e9) > my_bid.get('price', -1e9)):
                        my_bid = o
                elif o.get('action') == 'SELL':
                    if (my_ask is None) or (o.get('price', 1e9) < my_ask.get('price', 1e9)):
                        my_ask = o

            # 5) Clean up extra orders (keep at most one BUY and one SELL)
            # Cancel any "extra" ALGO orders that are not the chosen my_bid/my_ask
            for o in open_orders:
                if o.get('ticker') != TICKER:
                    continue
                if my_bid and o.get('action') == 'BUY' and o.get('order_id') != my_bid.get('order_id'):
                    cancel_order(s, o.get('order_id'))
                if my_ask and o.get('action') == 'SELL' and o.get('order_id') != my_ask.get('order_id'):
                    cancel_order(s, o.get('order_id'))

            # 6) Requote logic: only replace if price is stale by REQUOTE_TOL
            # BUY side
            if allow_buy:
                if my_bid is None:
                    place_limit(s, TICKER, 'BUY', BUY_VOLUME, desired_bid)
                else:
                    if abs(my_bid.get('price', 0) - desired_bid) >= REQUOTE_TOL:
                        cancel_order(s, my_bid.get('order_id'))
                        place_limit(s, TICKER, 'BUY', BUY_VOLUME, desired_bid)
            else:
                # If buying not allowed, cancel existing buy
                if my_bid is not None:
                    cancel_order(s, my_bid.get('order_id'))

            # SELL side
            if allow_sell:
                if my_ask is None:
                    place_limit(s, TICKER, 'SELL', SELL_VOLUME, desired_ask)
                else:
                    if abs(my_ask.get('price', 0) - desired_ask) >= REQUOTE_TOL:
                        cancel_order(s, my_ask.get('order_id'))
                        place_limit(s, TICKER, 'SELL', SELL_VOLUME, desired_ask)
            else:
                if my_ask is not None:
                    cancel_order(s, my_ask.get('order_id'))

            sleep(SLEEP_SEC)
            tick = get_tick(s)

# this calls the main() method when you type 'python algo2.py' into the command prompt
if __name__ == '__main__':
    signal.signal(signal.SIGINT, signal_handler)
    main()