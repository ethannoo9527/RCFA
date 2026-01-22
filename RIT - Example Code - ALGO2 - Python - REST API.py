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


# --- Volume optimization knobs ---
MAX_POS = 2000
BASE_EDGE = 0.01
REQUOTE_TOL = 0.01
MIN_MARKET_SPREAD = 0.02
SLEEP_SEC = 0.25

BASE_QTY = 500          # normal quoting size
MIN_QTY  = 100          # smallest quote size
MAX_QTY  = 3000         # cap size so you don't blow up inventory

POS_SOFT = 1000         # start scaling down size as you approach this
POS_HARD = MAX_POS      # stop quoting the side that increases risk at this

NO_FILL_TICKS = 6       # if no fills for ~6 ticks, try sizing up
SIZE_UP_FACTOR = 1.5    # multiply size when volume too low
SIZE_DOWN_FACTOR = 0.7  # multiply size when risk rising

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
    MAX_POS = 2000
    BASE_EDGE = 0.01
    REQUOTE_TOL = 0.01
    MIN_MARKET_SPREAD = 0.02
    SLEEP_SEC = 0.25

    # --- Volume optimization knobs ---
    BASE_QTY = 500
    MIN_QTY  = 100
    MAX_QTY  = 3000

    POS_SOFT = 1000
    NO_FILL_TICKS = 6
    SIZE_UP_FACTOR = 1.5
    SIZE_DOWN_FACTOR = 0.7

    with requests.Session() as s:
        s.headers.update(API_KEY)

        tick = get_tick(s)

        # Track fills using position changes (simple + robust)
        last_pos = get_position(s, TICKER)
        last_fill_tick = tick

        # Adaptive size state (starts at base)
        target_qty = BASE_QTY

        while (not shutdown) and (tick > 5) and (tick < 295):
            # 1) Read market
            best_bid, best_ask = get_top_of_book(s, TICKER)

            if best_bid is None or best_ask is None or best_ask <= best_bid:
                sleep(SLEEP_SEC)
                tick = get_tick(s)
                continue

            market_spread = best_ask - best_bid
            mid = (best_bid + best_ask) / 2.0

            # If no room to make edge, don't quote
            if market_spread < MIN_MARKET_SPREAD:
                sleep(SLEEP_SEC)
                tick = get_tick(s)
                continue

            # 2) Risk state
            pos = get_position(s, TICKER)

            # Detect fills: if position changed, you traded
            if pos != last_pos:
                last_pos = pos
                last_fill_tick = tick
                # After fills, usually reduce size slightly (avoid runaway inventory)
                target_qty = max(MIN_QTY, int(target_qty * SIZE_DOWN_FACTOR))

            # If no fills for a while, increase size to improve volume/queue impact
            if (tick - last_fill_tick) >= NO_FILL_TICKS:
                target_qty = min(MAX_QTY, int(target_qty * SIZE_UP_FACTOR))
                last_fill_tick = tick  # prevent ramping every loop

            # Scale size down as inventory grows (soft risk control)
            # When |pos| near POS_SOFT -> smaller quotes. When near MAX_POS -> tiny quotes / stop one side.
            inv = abs(pos)
            if inv >= POS_SOFT:
                # linear scale from POS_SOFT..MAX_POS
                # scale goes from 1.0 down to 0.2
                span = max(1, (MAX_POS - POS_SOFT))
                scale = max(0.2, 1.0 - (inv - POS_SOFT) / span)
                target_qty = max(MIN_QTY, int(target_qty * scale))

            # Decide if we are allowed to add more inventory on each side
            allow_buy = (pos < MAX_POS)
            allow_sell = (pos > -MAX_POS)

            # 3) Compute quotes
            edge = max(BASE_EDGE, 0.25 * market_spread)
            k = 0.00001
            skew = k * pos

            desired_bid = (mid - edge) - skew
            desired_ask = (mid + edge) - skew

            # 4) Read open orders
            open_orders = get_orders(s, 'OPEN')

            my_bid = None
            my_ask = None
            for o in open_orders:
                if o.get('ticker') != TICKER:
                    continue
                if o.get('action') == 'BUY':
                    if (my_bid is None) or (o.get('price', -1e9) > my_bid.get('price', -1e9)):
                        my_bid = o
                elif o.get('action') == 'SELL':
                    if (my_ask is None) or (o.get('price', 1e9) < my_ask.get('price', 1e9)):
                        my_ask = o

            # 5) Cancel extra orders (keep at most one per side)
            # IMPORTANT: your schema might use 'id' not 'order_id'
            def oid(order):
                return order.get('order_id', order.get('id'))

            for o in open_orders:
                if o.get('ticker') != TICKER:
                    continue
                if my_bid and o.get('action') == 'BUY' and oid(o) != oid(my_bid):
                    cancel_order(s, oid(o))
                if my_ask and o.get('action') == 'SELL' and oid(o) != oid(my_ask):
                    cancel_order(s, oid(o))

            # 6) Requote logic + dynamic quantity per side
            bid_qty = target_qty
            ask_qty = target_qty

            # Optional: inventory-aware asymmetry
            # If long, make ask bigger / bid smaller to work out of the position
            if pos > 0:
                ask_qty = min(MAX_QTY, int(target_qty * 1.2))
                bid_qty = max(MIN_QTY, int(target_qty * 0.8))
            elif pos < 0:
                bid_qty = min(MAX_QTY, int(target_qty * 1.2))
                ask_qty = max(MIN_QTY, int(target_qty * 0.8))

            # BUY side
            if allow_buy:
                if my_bid is None:
                    place_limit(s, TICKER, 'BUY', bid_qty, desired_bid)
                else:
                    if abs(my_bid.get('price', 0) - desired_bid) >= REQUOTE_TOL:
                        cancel_order(s, oid(my_bid))
                        place_limit(s, TICKER, 'BUY', bid_qty, desired_bid)
            else:
                if my_bid is not None:
                    cancel_order(s, oid(my_bid))

            # SELL side
            if allow_sell:
                if my_ask is None:
                    place_limit(s, TICKER, 'SELL', ask_qty, desired_ask)
                else:
                    if abs(my_ask.get('price', 0) - desired_ask) >= REQUOTE_TOL:
                        cancel_order(s, oid(my_ask))
                        place_limit(s, TICKER, 'SELL', ask_qty, desired_ask)
            else:
                if my_ask is not None:
                    cancel_order(s, oid(my_ask))

            sleep(SLEEP_SEC)
            tick = get_tick(s)

# this calls the main() method when you type 'python algo2.py' into the command prompt
if __name__ == '__main__':
    signal.signal(signal.SIGINT, signal_handler)

    main()

