from __future__ import annotations

import csv
import os
from datetime import date
from datetime import datetime
from datetime import timedelta
from typing import Any, Dict
import pathlib

import requests
import ccxt
from flask import Blueprint
from flask import redirect
from flask import render_template
from flask import request
from flask import flash
from flask.helpers import url_for
from flask import current_app
from flask_login import login_user, logout_user, login_required, current_user
from typing_extensions import TypedDict
from CredentialManager import CredentialManager

from futuresboard import db_manager, scraper
from futuresboard.forms import *
from futuresboard.db_manager import *

from addition.generate_report_v1 import get_report
from addition.generate_wallet import generate_usdt_trc20
from addition.tron_wallet import get_wallet_by_user_id
from addition.is_activate import is_activate

app = Blueprint("main", __name__)


class CoinsTotals(TypedDict):
    active: int
    inactive: int
    buys: int
    sells: int
    pbr: int


class Coins(TypedDict):
    active: dict[str, tuple[int, int, int]]
    inactive: list[str]
    totals: CoinsTotals
    warning: bool


class History(TypedDict):
    columns: list[dict[str, Any]]


class Projections(TypedDict):
    dates: list[str]
    proj: dict[float, list[float]]
    pcustom: list[float]
    pcustom_value: float


def api_credentials_ok(api_credentials, exchange_name):
    try:
        exchange_class = getattr(ccxt, exchange_name)
        params = {
            'timeout': 30000,
            'enableRateLimit': True,
            'hedgeMode': True,
            'options':{'defaultType': 'future', 'adjustForTimeDifference': True, 'defaultTimeInForce':'GTC', 'recvWindow': 59999}
        }
        params.update(api_credentials)
        exchange = exchange_class(params)
        exchange.fetch_total_balance()
    except:
        return False
    return True


def zero_value(x):
    if x is None:
        return 0
    else:
        return x


def format_dp(value, dp=2):
    return "{:.{}f}".format(value, dp)


def calc_pbr(volume, price, side, balance):
    if price > 0.0:
        if side == "SHORT":
            return abs(volume * price) / balance
        elif side == "LONG":
            return abs(volume * price) / balance
    return 0.0


def get_default_api_label():
    active_api_label = None
    label_lst = get_api_label_list()
    if len(label_lst) > 0:
        active_api_label = sorted(label_lst)[0]
    return active_api_label


def get_coins(active_api_label=None):
    coins: Coins = {
        "active": {},
        "inactive": [],
        "totals": {"active": 0, "inactive": 0, "buys": 0, "sells": 0, "pbr": 0},
        "warning": False,
    }
    if active_api_label == None:
        return coins

    all_active_positions = db_manager.query(active_api_label,
                                            "SELECT symbol, entryPrice, positionSide, positionAmt FROM positions_model WHERE positionAmt != 0 ORDER BY symbol ASC"
                                            )

    all_symbols_with_pnl = db_manager.query(active_api_label,
                                            'SELECT DISTINCT(symbol) FROM income_model WHERE asset <> "BNB" AND symbol <> "" AND incomeType <> "TRANSFER" ORDER BY symbol ASC'
                                            )

    balance = db_manager.query(active_api_label, "SELECT totalWalletBalance FROM account_model", one=True)

    active_symbols = []

    for position in all_active_positions:
        active_symbols.append(position[0])

        pbr = round(calc_pbr(position[3], position[1], position[2], float(balance[0])), 2)

        buy, sell = 0, 0

        buyorders = db_manager.query(active_api_label,
                                     'SELECT COUNT(OID) FROM orders_model WHERE symbol = ? AND side = "BUY"',
                                     [position[0]],
                                     one=True,
                                     )

        sellorders = db_manager.query(active_api_label,
                                      'SELECT COUNT(OID) FROM orders_model WHERE symbol = ? AND side = "SELL"',
                                      [position[0]],
                                      one=True,
                                      )

        if buyorders is not None:
            buy = int(buyorders[0])
            if buy == 0:
                coins["warning"] = True
        if sellorders is not None:
            sell = int(sellorders[0])

        coins["active"][position[0]] = (buy, sell, pbr)
        coins["totals"]["active"] += 1
        coins["totals"]["buys"] += buy
        coins["totals"]["sells"] += sell
        coins["totals"]["pbr"] += pbr

    for symbol in all_symbols_with_pnl:
        if symbol[0] not in active_symbols:
            coins["inactive"].append(symbol[0])
            coins["totals"]["inactive"] += 1

    coins["totals"]["pbr"] = format_dp(coins["totals"]["pbr"])
    return coins


def get_lastupdate(active_api_label):
    lastupdate = db_manager.query(active_api_label, "SELECT MAX(time) FROM orders_model", one=True)
    if lastupdate[0] is None:
        return "-"
    return datetime.fromtimestamp(lastupdate[0] / 1000.0).strftime("%Y-%m-%d %H:%M:%S")


def timeranges():
    today = date.today()
    yesterday_start = today - timedelta(days=1)

    this_week_start = today - timedelta(days=today.weekday())
    last_week_start = this_week_start - timedelta(days=7)
    last_week_end = this_week_start - timedelta(days=1)

    this_month_start = today.replace(day=1)
    last_month_start = (this_month_start - timedelta(days=1)).replace(day=1)
    last_month_end = this_month_start - timedelta(days=1)

    this_year_start = today.replace(day=1).replace(month=1)
    last_year_start = (this_year_start - timedelta(days=1)).replace(day=1).replace(month=1)
    last_year_end = this_year_start - timedelta(days=1)

    return [
        [today.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")],
        [yesterday_start.strftime("%Y-%m-%d"), yesterday_start.strftime("%Y-%m-%d")],
        [this_week_start.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")],
        [last_week_start.strftime("%Y-%m-%d"), last_week_end.strftime("%Y-%m-%d")],
        [this_month_start.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")],
        [last_month_start.strftime("%Y-%m-%d"), last_month_end.strftime("%Y-%m-%d")],
        [this_year_start.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")],
        [last_year_start.strftime("%Y-%m-%d"), last_year_end.strftime("%Y-%m-%d")],
        [last_year_start.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")],
    ]


def get_api_label_list():
    if current_user.is_admin:
        label_user_lst = CredentialManager.get_api_label_list()
        binance_api_lst = [*filter(lambda x : CredentialManager.get_exchange_name_from_api_label(x) == 'binance', label_user_lst)]
    else:
        label_user_lst = CredentialManager.get_api_label_list(current_user.username)
        binance_api_lst = [*filter(lambda x : CredentialManager.get_exchange_name_from_api_label(x) == 'binance', label_user_lst)]
        binance_api_lst = [*map(lambda x : x.split('@')[0], binance_api_lst)]
    binance_api_lst.sort()
    return binance_api_lst


@app.route('/register', methods=['GET', 'POST'])
def register_page():
    form = RegisterForm()
    if form.validate_on_submit():
        user_to_create = UserModel(username=form.username.data,
                                   email_address=form.email_address.data,
                                   password=form.password1.data)
        wallet: Dict = generate_usdt_trc20()

        db.session.add(user_to_create)
        db.session.commit()

        user_wallet_create = UserWalletModel(
            address=wallet["address"],
            private_key=wallet["private_key"],
            status=False,
            last_activate_time=0,
            user_id=user_to_create.id
        )

        db.session.add(user_wallet_create)
        db.session.commit()

        login_user(user_to_create)
        flash(f"Account created successfully! You are now logged in as {user_to_create.username}", category='success')
        return redirect(url_for('main.index_page'))
    if form.errors != {}:  # If there are not errors from the validations
        for err_msg in form.errors.values():
            flash(f'There was an error with creating a user: {err_msg}', category='danger')
    return render_template('register.html',
                           coin_list=get_coins(),
                           custom=current_app.config["CUSTOM"],
                           form=form)


@app.route('/login', methods=['GET', 'POST'])
def login_page():
    form = LoginForm()
    if form.validate_on_submit():
        attempted_user = UserModel.query.filter_by(username=form.username.data).first()
        if attempted_user and attempted_user.check_password_correction(
                attempted_password=form.password.data
        ):
            if attempted_user.status != 'active':
                flash(f'Your account was {attempted_user.status}, please contact site administrator for more info!', category='danger')
            else:
                login_user(attempted_user)
                return redirect(url_for('main.index_page'))
        else:
            flash('Username and password are not match! Please try again', category='danger')

    return render_template('login.html',
                           coin_list=get_coins(),
                           custom=current_app.config["CUSTOM"],
                           form=form)


@app.route('/logout')
def logout_page():
    if current_user.status != 'active':
        flash(f'Your account was {current_user.status}, please contact site administrator for more info!', category='danger')
    logout_user()
    flash("You have been logged out!", category='info')
    return redirect(url_for("main.index_page"))


@app.route("/apis", methods=['GET', 'POST'])
@app.route("/<active_api_label>/apis", methods=['GET', 'POST'])
@login_required
def api_page(active_api_label=""):
    if current_user.status != 'active':
        return redirect(url_for('main.logout_page'))
    if active_api_label == "":
        active_api_label = get_default_api_label()
    if active_api_label == None:
        flash(f"Please add APIs to get started!", category='warning')
    remove_form = RemoveApiForm()
    add_form = AddApiForm()
    wallet = get_wallet_by_user_id(user_id=current_user.id)

    if request.method == "POST":
        if remove_form.validate_on_submit():
            removed_api = request.form.get('removed_api_label')
            if removed_api != None:
                if removed_api in get_api_label_list():
                    CredentialManager.remove_credentials(removed_api, current_user.username)
                else:
                    flash(f"Could not remove API: {removed_api}!", category='danger')

                return redirect(url_for('main.api_page'))
        if add_form.validate_on_submit():
            api_credentials = {}
            exchange_name = 'binance'
            added_api = request.form.get('added_api_label')
            if added_api != None:
                added_api = add_form.api_name.data
                api_credentials['apiKey'] = add_form.api_key.data
                api_credentials['secret'] = add_form.secret_key.data
                if api_credentials_ok(api_credentials, exchange_name):
                    if not added_api in get_api_label_list():
                        CredentialManager.set_credentials(added_api, exchange_name, api_credentials, current_user.username)
                    else:
                        flash(f"Could not add or update {added_api} as it is already added", category='danger')
                else:
                    flash(f"Could not add  {added_api}, please check credential values and API permissions", category='danger')
                return redirect(url_for('main.api_page'))
    status = is_activate(user_id=current_user.id)
    print(f"User: {current_user.username} | Active: {status}")
    return render_template(
        "apis.html",
        api_label_list=get_api_label_list(),
        coin_list=get_coins(active_api_label),
        custom=current_app.config["CUSTOM"],
        remove_form=remove_form,
        add_form=add_form,
        active_api_label=active_api_label,
        wallet_address=wallet["address"] if wallet["address"] is not None else "Not wallet",
        wallet_status="Active" if wallet["status"] != 0 else "Not activated",
        wallet_time=datetime.fromtimestamp(int(str(wallet["last_activate_time"])[:10])) if wallet["last_activate_time"] != 0 else "Not activated"
    )

@app.route("/", methods=["GET"])
@app.route("/<active_api_label>", methods=["GET"])
@login_required
def index_page(active_api_label=""):
    if current_user.status != 'active':
        return redirect(url_for('main.logout_page'))
    if active_api_label == "":
        active_api_label = get_default_api_label()
        if active_api_label == None:
            return redirect(url_for('main.api_page'))
    daterange = request.args.get("daterange")
    ranges = timeranges()
    scraper.scrape(active_api_label, app)
    if daterange is not None:
        daterange = daterange.split(" - ")
        if len(daterange) == 2:
            try:
                start = (
                        datetime.combine(
                            datetime.fromisoformat(daterange[0]), datetime.min.time()
                        ).timestamp()
                        * 1000
                )
                end = (
                        datetime.combine(
                            datetime.fromisoformat(daterange[1]), datetime.max.time()
                        ).timestamp()
                        * 1000
                )
                startdate, enddate = daterange[0], daterange[1]
                return redirect(url_for("main.dashboard_page", start=startdate, end=enddate, active_api_label=active_api_label))
            except Exception:
                pass

    todaystart = (
            datetime.combine(datetime.fromisoformat(ranges[0][0]), datetime.min.time()).timestamp()
            * 1000
    )
    todayend = (
            datetime.combine(datetime.fromisoformat(ranges[0][1]), datetime.max.time()).timestamp()
            * 1000
    )
    weekstart = (
            datetime.combine(datetime.fromisoformat(ranges[2][0]), datetime.min.time()).timestamp()
            * 1000
    )
    weekend = (
            datetime.combine(datetime.fromisoformat(ranges[2][1]), datetime.max.time()).timestamp()
            * 1000
    )
    monthstart = (
            datetime.combine(datetime.fromisoformat(ranges[4][0]), datetime.min.time()).timestamp()
            * 1000
    )
    monthend = (
            datetime.combine(datetime.fromisoformat(ranges[4][1]), datetime.max.time()).timestamp()
            * 1000
    )

    start = (
            datetime.combine(datetime.fromisoformat(ranges[2][0]), datetime.min.time()).timestamp()
            * 1000
    )
    end = (
            datetime.combine(datetime.fromisoformat(ranges[2][1]), datetime.max.time()).timestamp()
            * 1000
    )
    startdate, enddate = ranges[2][0], ranges[2][1]

    balance = db_manager.query(active_api_label, "SELECT totalWalletBalance FROM account_model", one=True)
    total = db_manager.query(active_api_label,
                             'SELECT SUM(income) FROM income_model WHERE asset <> "BNB" AND incomeType <> "TRANSFER"', one=True
                             )
    today = db_manager.query(active_api_label,
                             'SELECT SUM(income) FROM income_model WHERE asset <> "BNB" AND incomeType <> "TRANSFER" AND time >= ? AND time <= ?',
                             [todaystart, todayend],
                             one=True,
                             )
    week = db_manager.query(active_api_label,
                            'SELECT SUM(income) FROM income_model WHERE asset <> "BNB" AND incomeType <> "TRANSFER" AND time >= ? AND time <= ?',
                            [weekstart, weekend],
                            one=True,
                            )
    month = db_manager.query(active_api_label,
                             'SELECT SUM(income) FROM income_model WHERE asset <> "BNB" AND incomeType <> "TRANSFER" AND time >= ? AND time <= ?',
                             [monthstart, monthend],
                             one=True,
                             )

    unrealized = db_manager.query(active_api_label, "SELECT SUM(unrealizedProfit) FROM positions_model", one=True)

    all_fees = db_manager.query(active_api_label,
                                'SELECT SUM(income), asset FROM income_model WHERE incomeType ="COMMISSION" GROUP BY asset'
                                )

    by_date = db_manager.query(active_api_label,
                               'SELECT DATE(time / 1000, "unixepoch") AS Date, SUM(income) AS inc FROM income_model WHERE asset <> "BNB" AND incomeType <> "TRANSFER" AND time >= ?  AND time <= ? GROUP BY Date',
                               [start, end],
                               )

    by_symbol = db_manager.query(active_api_label,
                                 'SELECT SUM(income) AS inc, symbol FROM income_model WHERE asset <> "BNB" AND incomeType <> "TRANSFER" AND time >= ? AND time <= ? GROUP BY symbol ORDER BY inc DESC',
                                 [start, end],
                                 )

    fees = {"USDT": 0, "BNB": 0}

    balance = float(balance[0])

    temptotal: tuple[list[float], list[float]] = ([], [])
    profit_period = balance - zero_value(week[0])

    temp: tuple[list[float], list[float]] = ([], [])
    for each in by_date:
        temp[0].append(round(float(each[1]), 2))
        temp[1].append(each[0])
        temptotal[1].append(each[0])
        temptotal[0].append(round(profit_period + float(each[1]), 2))
        profit_period += float(each[1])
    by_date = temp
    total_by_date = temptotal

    temp = ([], [])
    for each in by_symbol:
        temp[0].append(each[1])
        temp[1].append(round(float(each[0]), 2))
    by_symbol = temp

    if balance == 0.0:
        percentages = ["-", "-", "-", "-"]
    else:
        percentages = [
            format_dp(zero_value(today[0]) / balance * 100),
            format_dp(zero_value(week[0]) / balance * 100),
            format_dp(zero_value(month[0]) / balance * 100),
            format_dp(zero_value(total[0]) / balance * 100),
        ]

    for row in all_fees:
        fees[row[1]] = format_dp(abs(zero_value(row[0])), 4)

    pnl = [format_dp(zero_value(unrealized[0])), format_dp(balance)]
    totals = [
        format_dp(zero_value(total[0])),
        format_dp(zero_value(today[0])),
        format_dp(zero_value(week[0])),
        format_dp(zero_value(month[0])),
        ranges[3],
        fees,
        percentages,
        pnl,
        datetime.now().strftime("%B"),
        zero_value(week[0]),
        len(by_symbol[0]),
    ]
    wallet = get_wallet_by_user_id(user_id=current_user.id)
    status = is_activate(user_id=current_user.id)
    print(f"User: {current_user.username} | Active: {status}")
    return render_template(
        "home.html",
        coin_list=get_coins(active_api_label),
        totals=totals,
        data=[by_date, by_symbol, total_by_date],
        timeframe="week",
        lastupdate=get_lastupdate(active_api_label),
        startdate=startdate,
        enddate=enddate,
        timeranges=ranges,
        custom=current_app.config["CUSTOM"],
        api_label_list=get_api_label_list(),
        active_api_label=active_api_label,
        wallet_address=wallet["address"] if wallet["address"] is not None else "Not wallet",
        wallet_status="Active" if wallet["status"] != 0 else "Not activated",
        wallet_time=datetime.fromtimestamp(int(str(wallet["last_activate_time"])[:10])) if wallet["last_activate_time"] != 0 else "Not activated"
    )


@app.route("/dashboard/<start>/<end>", methods=["GET"])
@app.route("/dashboard/<active_api_label>/<start>/<end>", methods=["GET"])
@login_required
def dashboard_page(start, end, active_api_label=""):
    if current_user.status != 'active':
        return redirect(url_for('main.logout_page'))
    if active_api_label == "":
        active_api_label = get_default_api_label()
    scraper.scrape(active_api_label, app)
    ranges = timeranges()
    daterange = request.args.get("daterange")
    if daterange is not None:
        daterange = daterange.split(" - ")
        if len(daterange) == 2:
            try:
                start = (
                        datetime.combine(
                            datetime.fromisoformat(daterange[0]), datetime.min.time()
                        ).timestamp()
                        * 1000
                )
                end = (
                        datetime.combine(
                            datetime.fromisoformat(daterange[1]), datetime.max.time()
                        ).timestamp()
                        * 1000
                )
                startdate, enddate = daterange[0], daterange[1]
                return redirect(url_for("main.dashboard_page", start=startdate, end=enddate, active_api_label=active_api_label))
            except Exception:
                return redirect(url_for("main.dashboard_page", start=start, end=end, active_api_label=active_api_label))

    try:
        startdate, enddate = start, end
        start = (
                datetime.combine(datetime.fromisoformat(start), datetime.min.time()).timestamp() * 1000
        )
        end = datetime.combine(datetime.fromisoformat(end), datetime.max.time()).timestamp() * 1000
    except Exception:
        startdate, enddate = ranges[2][0], ranges[2][1]
        return redirect(url_for("main.dashboard_page", start=startdate, end=enddate, active_api_label=active_api_label))

    todaystart = (
            datetime.combine(datetime.fromisoformat(ranges[0][0]), datetime.min.time()).timestamp()
            * 1000
    )
    todayend = (
            datetime.combine(datetime.fromisoformat(ranges[0][1]), datetime.max.time()).timestamp()
            * 1000
    )
    weekstart = (
            datetime.combine(datetime.fromisoformat(ranges[2][0]), datetime.min.time()).timestamp()
            * 1000
    )
    weekend = (
            datetime.combine(datetime.fromisoformat(ranges[2][1]), datetime.max.time()).timestamp()
            * 1000
    )
    monthstart = (
            datetime.combine(datetime.fromisoformat(ranges[4][0]), datetime.min.time()).timestamp()
            * 1000
    )
    monthend = (
            datetime.combine(datetime.fromisoformat(ranges[4][1]), datetime.max.time()).timestamp()
            * 1000
    )

    balance = db_manager.query(active_api_label, "SELECT totalWalletBalance FROM account_model", one=True)
    total = db_manager.query(active_api_label,
                             'SELECT SUM(income) FROM income_model WHERE asset <> "BNB" AND incomeType <> "TRANSFER"', one=True
                             )

    today = db_manager.query(active_api_label,
                             'SELECT SUM(income) FROM income_model WHERE asset <> "BNB" AND incomeType <> "TRANSFER" AND time >= ? AND time <= ?',
                             [todaystart, todayend],
                             one=True,
                             )
    week = db_manager.query(active_api_label,
                            'SELECT SUM(income) FROM income_model WHERE asset <> "BNB" AND incomeType <> "TRANSFER" AND time >= ? AND time <= ?',
                            [weekstart, weekend],
                            one=True,
                            )
    month = db_manager.query(active_api_label,
                             'SELECT SUM(income) FROM income_model WHERE asset <> "BNB" AND incomeType <> "TRANSFER" AND time >= ? AND time <= ?',
                             [monthstart, monthend],
                             one=True,
                             )

    unrealized = db_manager.query(active_api_label, "SELECT SUM(unrealizedProfit) FROM positions_model", one=True)

    all_fees = db_manager.query(active_api_label,
                                'SELECT SUM(income), asset FROM income_model WHERE incomeType ="COMMISSION" GROUP BY asset'
                                )

    by_date = db_manager.query(active_api_label,
                               'SELECT DATE(time / 1000, "unixepoch") AS Date, SUM(income) AS inc FROM income_model WHERE asset <> "BNB" AND incomeType <> "TRANSFER" AND time >= ?  AND time <= ? GROUP BY Date',
                               [start, end],
                               )

    by_symbol = db_manager.query(active_api_label,
                                 'SELECT SUM(income) AS inc, symbol FROM income_model WHERE asset <> "BNB" AND incomeType <> "TRANSFER" AND time >= ? AND time <= ? GROUP BY symbol ORDER BY inc DESC',
                                 [start, end],
                                 )

    fees = {"USDT": 0, "BNB": 0}

    balance = float(balance[0])

    temptotal: tuple[list[float], list[float]] = ([], [])

    customframe = db_manager.query(active_api_label,
                                   'SELECT SUM(income) FROM income_model WHERE asset <> "BNB" AND incomeType <> "TRANSFER" AND time >= ? AND time <= ?',
                                   [start, end],
                                   one=True,
                                   )

    profit_period = balance - zero_value(customframe[0])

    temp: tuple[list[float], list[float]] = ([], [])
    for each in by_date:
        temp[0].append(round(float(each[1]), 2))
        temp[1].append(each[0])
        temptotal[1].append(each[0])
        temptotal[0].append(round(profit_period + float(each[1]), 2))
        profit_period += float(each[1])
    by_date = temp
    total_by_date = temptotal

    temp = ([], [])
    for each in by_symbol:
        temp[0].append(each[1])
        temp[1].append(round(float(each[0]), 2))
    by_symbol = temp

    if balance == 0.0:
        percentages = ["-", "-", "-", "-"]
    else:
        percentages = [
            format_dp(zero_value(today[0]) / balance * 100),
            format_dp(zero_value(week[0]) / balance * 100),
            format_dp(zero_value(month[0]) / balance * 100),
            format_dp(zero_value(total[0]) / balance * 100),
        ]

    for row in all_fees:
        fees[row[1]] = format_dp(abs(zero_value(row[0])), 4)
    pnl = [format_dp(zero_value(unrealized[0])), format_dp(balance)]
    totals = [
        format_dp(zero_value(total[0])),
        format_dp(zero_value(today[0])),
        format_dp(zero_value(week[0])),
        format_dp(zero_value(month[0])),
        ranges[3],
        fees,
        percentages,
        pnl,
        datetime.now().strftime("%B"),
        zero_value(customframe[0]),
        len(by_symbol[0]),
    ]
    wallet = get_wallet_by_user_id(user_id=current_user.id)
    status = is_activate(user_id=current_user.id)
    print(f"User: {current_user.username} | Active: {status}")
    return render_template(
        "home.html",
        coin_list=get_coins(active_api_label),
        totals=totals,
        data=[by_date, by_symbol, total_by_date],
        lastupdate=get_lastupdate(active_api_label),
        startdate=startdate,
        enddate=enddate,
        timeranges=ranges,
        custom=current_app.config["CUSTOM"],
        api_label_list=get_api_label_list(),
        active_api_label=active_api_label,
        wallet_address=wallet["address"] if wallet["address"] is not None else "Not wallet",
        wallet_status="Active" if wallet["status"] != 0 else "Not activated",
        wallet_time=datetime.fromtimestamp(int(str(wallet["last_activate_time"])[:10])) if wallet[
                                                                                               "last_activate_time"] != 0 else "Not activated"
    )


@app.route("/positions")
@app.route("/<active_api_label>/positions")
@login_required
def positions_page(active_api_label=""):
    if current_user.status != 'active':
        return redirect(url_for('main.logout_page'))
    if active_api_label == "":
        active_api_label = get_default_api_label()
    scraper.scrape(active_api_label, app)
    coins = get_coins(active_api_label)
    positions = {}
    for coin in coins["active"]:
        if not coin in positions:
            positions[coin] = []

        allpositions = db_manager.query(active_api_label,
                                        "SELECT * FROM positions_model WHERE symbol = ?",
                                        [coin],
                                        )

        for position in allpositions:
            position = list(position)
            position.remove(active_api_label)  # TEMP
            position[4] = round(float(position[4]), 5)
            pos_side = position[5]
            allorders = db_manager.query(active_api_label,
                                         "SELECT * FROM orders_model WHERE symbol = ? AND positionSide = ? ORDER BY side, price, origQty",
                                         [coin, pos_side])
            all_formated_orders = []
            for order in allorders:
                order = list(order)
                order.remove(active_api_label)  # TEMP
                order[7] = datetime.fromtimestamp(order[7] / 1000.0).strftime("%Y-%m-%d %H:%M:%S")
                all_formated_orders.append(order)
            positions[coin].append([[position], all_formated_orders])
    wallet = get_wallet_by_user_id(user_id=current_user.id)
    status = is_activate(user_id=current_user.id)
    print(f"User: {current_user.username} | Active: {status}")
    return render_template(
        "positions.html",
        coin_list=get_coins(active_api_label),
        positions=positions,
        custom=current_app.config["CUSTOM"],
        api_label_list=get_api_label_list(),
        active_api_label=active_api_label,
        wallet_address=wallet["address"] if wallet["address"] is not None else "Not wallet",
        wallet_status="Active" if wallet["status"] != 0 else "Not activated",
        wallet_time=datetime.fromtimestamp(int(str(wallet["last_activate_time"])[:10])) if wallet[
                                                                                               "last_activate_time"] != 0 else "Not activated"
    )


@app.route("/coins/<coin>", methods=["GET"])
@app.route("/<active_api_label>/coins/<coin>", methods=["GET"])
@login_required
def coin_page(coin, active_api_label=""):
    if current_user.status != 'active':
        return redirect(url_for('main.logout_page'))
    if active_api_label == "":
        active_api_label = get_default_api_label()
    scraper.scrape(active_api_label, app)
    coins = get_coins(active_api_label)
    if coin not in coins["inactive"] and coin not in coins["active"]:
        return (
            render_template(
                "error.html",
                coin_list=get_coins(active_api_label),
                custom=current_app.config["CUSTOM"],
            ),
            404,
        )

    daterange = request.args.get("daterange")
    ranges = timeranges()

    if daterange is not None:
        daterange = daterange.split(" - ")
        if len(daterange) == 2:
            try:
                (
                        datetime.combine(
                            datetime.fromisoformat(daterange[0]), datetime.min.time()
                        ).timestamp()
                        * 1000
                )
                (
                        datetime.combine(
                            datetime.fromisoformat(daterange[1]), datetime.max.time()
                        ).timestamp()
                        * 1000
                )
                startdate, enddate = daterange[0], daterange[1]
                return redirect(
                    url_for("main.coin_page_timeframe", coin=coin, start=startdate, end=enddate, active_api_label=active_api_label)
                )
            except Exception:
                pass

    try:
        response = requests.get(
            "https://fapi.binance.com/fapi/v1/premiumIndex?symbol=" + coin, timeout=1
        )
        markPrice: float | str
        if response:
            markPrice = round(float(response.json()["markPrice"]), 5)
        else:
            markPrice = "-"
    except Exception:
        markPrice = "-"

    balance = db_manager.query(active_api_label, "SELECT totalWalletBalance FROM account_model", one=True)
    if balance[0] is None:
        totals = ["-", "-", "-", "-", "-", {"USDT": 0, "BNB": 0}, ["-", "-", "-", "-"]]
    else:

        todaystart = (
                datetime.combine(datetime.fromisoformat(ranges[0][0]), datetime.min.time()).timestamp()
                * 1000
        )
        todayend = (
                datetime.combine(datetime.fromisoformat(ranges[0][1]), datetime.max.time()).timestamp()
                * 1000
        )
        weekstart = (
                datetime.combine(datetime.fromisoformat(ranges[2][0]), datetime.min.time()).timestamp()
                * 1000
        )
        weekend = (
                datetime.combine(datetime.fromisoformat(ranges[2][1]), datetime.max.time()).timestamp()
                * 1000
        )
        monthstart = (
                datetime.combine(datetime.fromisoformat(ranges[4][0]), datetime.min.time()).timestamp()
                * 1000
        )
        monthend = (
                datetime.combine(datetime.fromisoformat(ranges[4][1]), datetime.max.time()).timestamp()
                * 1000
        )

        startdate, enddate = ranges[2][0], ranges[2][1]

        total = db_manager.query(active_api_label,
                                 'SELECT SUM(income) FROM income_model WHERE asset <> "BNB" AND incomeType <> "TRANSFER" AND symbol = ?',
                                 [coin],
                                 one=True,
                                 )
        today = db_manager.query(active_api_label,
                                 'SELECT SUM(income) FROM income_model WHERE asset <> "BNB" AND incomeType <> "TRANSFER" AND time >= ? AND time <= ? AND symbol = ?',
                                 [todaystart, todayend, coin],
                                 one=True,
                                 )
        week = db_manager.query(active_api_label,
                                'SELECT SUM(income) FROM income_model WHERE asset <> "BNB" AND incomeType <> "TRANSFER" AND time >= ? AND time <= ? AND symbol = ?',
                                [weekstart, weekend, coin],
                                one=True,
                                )
        month = db_manager.query(active_api_label,
                                 'SELECT SUM(income) FROM income_model WHERE asset <> "BNB" AND incomeType <> "TRANSFER" AND time >= ? AND time <= ? AND symbol = ?',
                                 [monthstart, monthend, coin],
                                 one=True,
                                 )

        result = db_manager.query(active_api_label,
                                  'SELECT SUM(income), asset FROM income_model WHERE incomeType ="COMMISSION" AND symbol = ? GROUP BY asset',
                                  [coin],
                                  )
        unrealized = db_manager.query(active_api_label,
                                      "SELECT SUM(unrealizedProfit) FROM positions_model WHERE symbol = ?",
                                      [coin],
                                      one=True,
                                      )
        allpositions = db_manager.query(active_api_label,
                                        "SELECT * FROM positions_model WHERE symbol = ?",
                                        [coin],
                                        )
        allorders = db_manager.query(active_api_label,
                                     "SELECT * FROM orders_model WHERE symbol = ? ORDER BY side, price, origQty",
                                     [coin],
                                     )

        temp = []
        for position in allpositions:
            position = list(position)
            position.remove(active_api_label)  # TEMP
            position[4] = round(float(position[4]), 5)
            temp.append(position)
        allpositions = temp

        temp = []
        for order in allorders:
            order = list(order)
            order.remove(active_api_label)  # TEMP
            order[7] = datetime.fromtimestamp(order[7] / 1000.0).strftime("%Y-%m-%d %H:%M:%S")
            temp.append(order)
        allorders = temp

        fees = {"USDT": 0, "BNB": 0}
        balance = float(balance[0])
        if balance == 0.0:
            percentages = ["-", "-", "-", "-"]
        else:
            percentages = [
                format_dp(zero_value(today[0]) / balance * 100),
                format_dp(zero_value(week[0]) / balance * 100),
                format_dp(zero_value(month[0]) / balance * 100),
                format_dp(zero_value(total[0]) / balance * 100),
            ]
        for row in result:
            fees[row[1]] = format_dp(abs(zero_value(row[0])), 4)
        pnl = [format_dp(zero_value(unrealized[0])), format_dp(balance)]
        totals = [
            format_dp(zero_value(total[0])),
            format_dp(zero_value(today[0])),
            format_dp(zero_value(week[0])),
            format_dp(zero_value(month[0])),
            ranges[3],
            fees,
            percentages,
            pnl,
            datetime.now().strftime("%B"),
            zero_value(week[0]),
        ]
        by_date = db_manager.query(active_api_label,
                                   'SELECT DATE(time / 1000, "unixepoch") AS Date, SUM(income) AS inc FROM income_model WHERE asset <> "BNB" AND incomeType <> "TRANSFER" AND time >= ? AND time <= ? AND symbol = ? GROUP BY Date',
                                   [weekstart, weekend, coin],
                                   )
        temp = [[], []]
        for each in by_date:
            temp[0].append(round(float(each[1]), 2))
            temp[1].append(each[0])
        by_date = temp

    wallet = get_wallet_by_user_id(user_id=current_user.id)
    status = is_activate(user_id=current_user.id)
    print(f"User: {current_user.username} | Active: {status}")

    return render_template(
        "coin.html",
        coin_list=get_coins(active_api_label),
        coin=coin,
        totals=totals,
        summary=[],
        data=[by_date],
        orders=[allpositions, allorders],
        lastupdate=get_lastupdate(active_api_label),
        markprice=markPrice,
        startdate=startdate,
        enddate=enddate,
        timeranges=ranges,
        custom=current_app.config["CUSTOM"],
        api_label_list=get_api_label_list(),
        active_api_label=active_api_label,
        wallet_address=wallet["address"] if wallet["address"] is not None else "Not wallet",
        wallet_status="Active" if wallet["status"] != 0 else "Not activated",
        wallet_time=datetime.fromtimestamp(int(str(wallet["last_activate_time"])[:10])) if wallet[
                                                                                               "last_activate_time"] != 0 else "Not activated"
    )


@app.route("/coins/<coin>/<start>/<end>")
@app.route("/coins/<active_api_label>/<coin>/<start>/<end>")
@login_required
def coin_page_timeframe(coin, start, end, active_api_label=""):
    if current_user.status != 'active':
        return redirect(url_for('main.logout_page'))
    if active_api_label == "":
        active_api_label = get_default_api_label()
    coins = get_coins(active_api_label)
    if coin not in coins["inactive"] and coin not in coins["active"]:
        return (
            render_template(
                "error.html",
                coin_list=get_coins(active_api_label),
                custom=current_app.config["CUSTOM"],
            ),
            404,
        )

    ranges = timeranges()
    daterange = request.args.get("daterange")

    if daterange is not None:
        daterange = daterange.split(" - ")
        if len(daterange) == 2:
            try:
                start = (
                        datetime.combine(
                            datetime.fromisoformat(daterange[0]), datetime.min.time()
                        ).timestamp()
                        * 1000
                )
                end = (
                        datetime.combine(
                            datetime.fromisoformat(daterange[1]), datetime.max.time()
                        ).timestamp()
                        * 1000
                )
                startdate, enddate = daterange[0], daterange[1]
                return redirect(
                    url_for("main.coin_page_timeframe", coin=coin, start=startdate, end=enddate, active_api_label=active_api_label)
                )
            except Exception:
                return redirect(
                    url_for("main.coin_page_timeframe", coin=coin, start=start, end=end, active_api_label=active_api_label)
                )

    try:
        startdate, enddate = start, end
        start = (
                datetime.combine(datetime.fromisoformat(start), datetime.min.time()).timestamp() * 1000
        )
        end = datetime.combine(datetime.fromisoformat(end), datetime.max.time()).timestamp() * 1000
    except Exception:
        startdate, enddate = ranges[2][0], ranges[2][1]
        return redirect(
            url_for("main.coin_page_timeframe", coin=coin, start=startdate, end=enddate, active_api_label=active_api_label)
        )

    todaystart = (
            datetime.combine(datetime.fromisoformat(ranges[0][0]), datetime.min.time()).timestamp()
            * 1000
    )
    todayend = (
            datetime.combine(datetime.fromisoformat(ranges[0][1]), datetime.max.time()).timestamp()
            * 1000
    )
    weekstart = (
            datetime.combine(datetime.fromisoformat(ranges[2][0]), datetime.min.time()).timestamp()
            * 1000
    )
    weekend = (
            datetime.combine(datetime.fromisoformat(ranges[2][1]), datetime.max.time()).timestamp()
            * 1000
    )
    monthstart = (
            datetime.combine(datetime.fromisoformat(ranges[4][0]), datetime.min.time()).timestamp()
            * 1000
    )
    monthend = (
            datetime.combine(datetime.fromisoformat(ranges[4][1]), datetime.max.time()).timestamp()
            * 1000
    )

    try:
        response = requests.get(
            "https://fapi.binance.com/fapi/v1/premiumIndex?symbol=" + coin, timeout=1
        )
        markPrice: float | str
        if response:
            markPrice = round(float(response.json()["markPrice"]), 5)
        else:
            markPrice = "-"
    except Exception:
        markPrice = "-"

    balance = db_manager.query(active_api_label, "SELECT totalWalletBalance FROM account_model", one=True)
    if balance[0] is None:
        totals = ["-", "-", "-", "-", "-", {"USDT": 0, "BNB": 0}, ["-", "-", "-", "-"]]
    else:
        total = db_manager.query(active_api_label,
                                 'SELECT SUM(income) FROM income_model WHERE asset <> "BNB" AND incomeType <> "TRANSFER" AND symbol = ?',
                                 [coin],
                                 one=True,
                                 )
        today = db_manager.query(active_api_label,
                                 'SELECT SUM(income) FROM income_model WHERE asset <> "BNB" AND incomeType <> "TRANSFER" AND time >= ? AND time <= ? AND symbol = ?',
                                 [todaystart, todayend, coin],
                                 one=True,
                                 )
        week = db_manager.query(active_api_label,
                                'SELECT SUM(income) FROM income_model WHERE asset <> "BNB" AND incomeType <> "TRANSFER" AND time >= ? AND time <= ? AND symbol = ?',
                                [weekstart, weekend, coin],
                                one=True,
                                )
        month = db_manager.query(active_api_label,
                                 'SELECT SUM(income) FROM income_model WHERE asset <> "BNB" AND incomeType <> "TRANSFER" AND time >= ? AND time <= ? AND symbol = ?',
                                 [monthstart, monthend, coin],
                                 one=True,
                                 )
        result = db_manager.query(active_api_label,
                                  'SELECT SUM(income), asset FROM income_model WHERE incomeType ="COMMISSION" AND symbol = ? GROUP BY asset',
                                  [coin],
                                  )
        unrealized = db_manager.query(active_api_label,
                                      "SELECT SUM(unrealizedProfit) FROM positions_model WHERE symbol = ?",
                                      [coin],
                                      one=True,
                                      )
        allpositions = db_manager.query(active_api_label,
                                        "SELECT * FROM positions_model WHERE symbol = ?",
                                        [coin],
                                        )
        allorders = db_manager.query(active_api_label,
                                     "SELECT * FROM orders_model WHERE symbol = ? ORDER BY side, price, origQty",
                                     [coin],
                                     )

        temp = []
        for position in allpositions:
            position = list(position)
            position[4] = round(float(position[4]), 5)
            temp.append(position)
        allpositions = temp

        temp = []
        for order in allorders:
            order = list(order)
            order[7] = datetime.fromtimestamp(order[7] / 1000.0).strftime("%Y-%m-%d %H:%M:%S")
            temp.append(order)
        allorders = temp
        fees = {"USDT": 0, "BNB": 0}
        balance = float(balance[0])
        if balance == 0.0:
            percentages = ["-", "-", "-", "-"]
        else:
            percentages = [
                format_dp(zero_value(today[0]) / balance * 100),
                format_dp(zero_value(week[0]) / balance * 100),
                format_dp(zero_value(month[0]) / balance * 100),
                format_dp(zero_value(total[0]) / balance * 100),
            ]
        for row in result:
            fees[row[1]] = format_dp(abs(zero_value(row[0])), 4)

        by_date = db_manager.query(active_api_label,
                                   'SELECT DATE(time / 1000, "unixepoch") AS Date, SUM(income) AS inc FROM income_model WHERE asset <> "BNB" AND incomeType <> "TRANSFER" AND time >= ? AND time <= ? AND symbol = ? GROUP BY Date',
                                   [start, end, coin],
                                   )
        temp = [[], []]
        for each in by_date:
            temp[0].append(round(float(each[1]), 2))
            temp[1].append(each[0])
        by_date = temp

        pnl = [format_dp(zero_value(unrealized[0])), format_dp(balance)]

        customframe = db_manager.query(active_api_label,
                                       'SELECT SUM(income) FROM income_model WHERE asset <> "BNB" AND incomeType <> "TRANSFER" AND time >= ? AND time <= ? AND symbol = ?',
                                       [start, end, coin],
                                       one=True,
                                       )

        totals = [
            format_dp(zero_value(total[0])),
            format_dp(zero_value(today[0])),
            format_dp(zero_value(week[0])),
            format_dp(zero_value(month[0])),
            ranges[3],
            fees,
            percentages,
            pnl,
            datetime.now().strftime("%B"),
            zero_value(customframe[0]),
        ]

    wallet = get_wallet_by_user_id(user_id=current_user.id)
    status = is_activate(user_id=current_user.id)
    print(f"User: {current_user.username} | Active: {status}")

    return render_template(
        "coin.html",
        coin_list=get_coins(active_api_label),
        coin=coin,
        totals=totals,
        summary=[],
        data=[by_date],
        orders=[allpositions, allorders],
        lastupdate=get_lastupdate(),
        markprice=markPrice,
        startdate=startdate,
        enddate=enddate,
        timeranges=ranges,
        custom=current_app.config["CUSTOM"],
        api_label_list=get_api_label_list(),
        active_api_label=active_api_label,
        wallet_address=wallet["address"] if wallet["address"] is not None else "Not wallet",
        wallet_status="Active" if wallet["status"] != 0 else "Not activated",
        wallet_time=datetime.fromtimestamp(int(str(wallet["last_activate_time"])[:10])) if wallet[
                                                                                               "last_activate_time"] != 0 else "Not activated"
    )


@app.route("/history")
@app.route("/<active_api_label>/history")
@login_required
def history_page(active_api_label=""):
    if current_user.status != 'active':
        return redirect(url_for('main.logout_page'))
    if active_api_label == "":
        active_api_label = get_default_api_label()
    scraper.scrape(active_api_label, app)
    ranges = timeranges()
    history: History = {"columns": []}

    for timeframe in ranges:
        start = (
                datetime.combine(datetime.fromisoformat(timeframe[0]), datetime.min.time()).timestamp()
                * 1000
        )
        end = (
                datetime.combine(datetime.fromisoformat(timeframe[1]), datetime.max.time()).timestamp()
                * 1000
        )
        incomesummary = db_manager.query(active_api_label,
                                         "SELECT incomeType, COUNT(id) FROM income_model WHERE time >= ? AND time <= ? GROUP BY incomeType",
                                         [start, end],
                                         )
        temp = timeframe[0] + "/" + timeframe[1]
        if temp not in history:
            history[temp] = {}  # type: ignore[misc]
            history[temp]["total"] = 0  # type: ignore[misc]

        for totals in incomesummary:
            history[temp][totals[0]] = int(totals[1])  # type: ignore[misc]
            history[temp]["total"] += int(totals[1])  # type: ignore[misc]
            if totals[0] not in history["columns"]:
                history["columns"].append(totals[0])
    for timeframe in ranges:
        temp = timeframe[0] + "/" + timeframe[1]
        for column in history["columns"]:
            if column not in history[temp]:  # type: ignore[misc]
                history[temp][column] = 0  # type: ignore[misc]

    history["columns"].sort()

    previous_files = []
    for file in os.listdir(os.path.join(app.root_path, "static", "csv")):
        if file.endswith(".csv"):
            previous_files.append("csv/" + file)

    wallet = get_wallet_by_user_id(user_id=current_user.id)
    status = is_activate(user_id=current_user.id)
    print(f"User: {current_user.username} | Active: {status}")

    return render_template(
        "history.html",
        coin_list=get_coins(active_api_label),
        history=history,
        filename="-",
        files=previous_files,
        custom=current_app.config["CUSTOM"],
        api_label_list=get_api_label_list(),
        active_api_label=active_api_label,
        wallet_address=wallet["address"] if wallet["address"] is not None else "Not wallet",
        wallet_status="Active" if wallet["status"] != 0 else "Not activated",
        wallet_time=datetime.fromtimestamp(int(str(wallet["last_activate_time"])[:10])) if wallet[
                                                                                               "last_activate_time"] != 0 else "Not activated"
    )


@app.route("/history/<start>/<end>")
@app.route("/<active_api_label>/history/<start>/<end>")
@login_required
def history_page_timeframe(start, end, active_api_label=""):
    if current_user.status != 'active':
        return redirect(url_for('main.logout_page'))
    if active_api_label == "":
        active_api_label = get_default_api_label()
    scraper.scrape(active_api_label, app)
    try:
        startdate, enddate = start, end
        start = (
                datetime.combine(datetime.fromisoformat(start), datetime.min.time()).timestamp() * 1000
        )
        end = datetime.combine(datetime.fromisoformat(end), datetime.max.time()).timestamp() * 1000
    except Exception:
        return redirect(url_for("main.history_page", active_api_label=active_api_label))

    ranges = timeranges()

    history = db_manager.query(
        "SELECT * FROM income_model WHERE time >= ? AND time <= ? ORDER BY time desc",
        [start, end],
    )

    history_temp = []
    for inc in history:
        inc = list(inc)
        inc[7] = datetime.fromtimestamp(inc[7] / 1000.0).strftime("%Y-%m-%d %H:%M:%S")
        history_temp.append(inc)
    history = history_temp

    filename = (
            datetime.now().strftime("%Y-%m-%dT%H%M%S") + "_income_" + startdate + "_" + enddate + ".csv"
    )

    with open(os.path.join(app.root_path, "static", "csv", filename), "w", newline="") as csvfile:
        spamwriter = csv.writer(csvfile, delimiter=",")
        spamwriter.writerow(
            [
                "sqliteID",
                "TransactionId",
                "Symbol",
                "IncomeType",
                "Income",
                "Asset",
                "Info",
                "Time",
                "TradeId",
            ]
        )
        spamwriter.writerows(history)

    history = {"columns": []}

    temp: tuple[str, str]
    for timeframe in ranges:
        start = (
                datetime.combine(datetime.fromisoformat(timeframe[0]), datetime.min.time()).timestamp()
                * 1000
        )
        end = (
                datetime.combine(datetime.fromisoformat(timeframe[1]), datetime.max.time()).timestamp()
                * 1000
        )
        incomesummary = db_manager.query(
            "SELECT incomeType, COUNT(IID) FROM income_model WHERE time >= ? AND time <= ? GROUP BY incomeType",
            [start, end],
        )
        temp = (timeframe[0], timeframe[1])
        if temp not in history:
            history[temp] = {}
            history[temp]["total"] = 0

        for totals in incomesummary:
            history[temp][totals[0]] = int(totals[1])
            history[temp]["total"] += int(totals[1])
            if totals[0] not in history["columns"]:
                history["columns"].append(totals[0])
    for timeframe in ranges:
        temp = (timeframe[0], timeframe[1])
        for column in history["columns"]:
            if column not in history[temp]:
                history[temp][column] = 0

    history["columns"].sort()

    filename = "csv/" + filename

    previous_files = []
    for file in os.listdir(os.path.join(app.root_path, "static", "csv")):
        if file.endswith(".csv"):
            previous_files.append("csv/" + file)

    wallet = get_wallet_by_user_id(user_id=current_user.id)
    status = is_activate(user_id=current_user.id)
    print(f"User: {current_user.username} | Active: {status}")

    return render_template(
        "history.html",
        coin_list=get_coins(active_api_label),
        history=history,
        fname=filename,
        files=previous_files,
        custom=current_app.config["CUSTOM"],
        api_label_list=get_api_label_list(),
        active_api_label=active_api_label,
        wallet_address=wallet["address"] if wallet["address"] is not None else "Not wallet",
        wallet_status="Active" if wallet["status"] != 0 else "Not activated",
        wallet_time=datetime.fromtimestamp(int(str(wallet["last_activate_time"])[:10])) if wallet[
                                                                                               "last_activate_time"] != 0 else "Not activated"
    )


@app.route("/projection")
@app.route("/<active_api_label>/projection")
@login_required
def projection_page(active_api_label=""):
    if current_user.status != 'active':
        return redirect(url_for('main.logout_page'))
    if active_api_label == "":
        active_api_label = get_default_api_label()
    balance = db_manager.query(active_api_label, "SELECT totalWalletBalance FROM account_model", one=True)
    projections: Projections = {
        "dates": [],
        "proj": {},
        "pcustom": [],
        "pcustom_value": 0.0,
    }
    if balance[0] is not None:

        ranges = timeranges()

        todayend = (
                datetime.combine(datetime.fromisoformat(ranges[0][1]), datetime.max.time()).timestamp()
                * 1000
        )
        minus_7_start = (
                datetime.combine(
                    datetime.fromisoformat((date.today() - timedelta(days=7)).strftime("%Y-%m-%d")),
                    datetime.max.time(),
                ).timestamp()
                * 1000
        )

        week = db_manager.query(active_api_label,
                                'SELECT SUM(income) FROM income_model WHERE asset <> "BNB" AND incomeType <> "TRANSFER" AND time >= ? AND time <= ?',
                                [minus_7_start, todayend],
                                one=True,
                                )
        custom = round(week[0] / balance[0] * 100 / 7, 2)
        projections["pcustom_value"] = custom
        today = date.today()
        x = 1
        config_projections = current_app.config["CUSTOM"]["PROJECTIONS"]
        while x < 365:
            nextday = today + timedelta(days=x)
            projections["dates"].append(nextday.strftime("%Y-%m-%d"))

            for each_projection in config_projections:
                if each_projection not in projections["proj"]:
                    projections["proj"][each_projection] = []

                if len(projections["proj"][each_projection]) < 1:
                    newbalance = balance[0]
                    projections["proj"][each_projection].append(newbalance)
                else:
                    newbalance = projections["proj"][each_projection][-1]

                projections["proj"][each_projection].append(newbalance * each_projection)

            if len(projections["pcustom"]) < 1:
                newbalance = balance[0]
            else:
                newbalance = projections["pcustom"][-1]

            projections["pcustom"].append(newbalance * (1 + (week[0] / balance[0]) / 7))

            x += 1

    wallet = get_wallet_by_user_id(user_id=current_user.id)
    status = is_activate(user_id=current_user.id)
    print(f"User: {current_user.username} | Active: {status}")
    return render_template(
        "projection.html",
        coin_list=get_coins(active_api_label),
        data=projections,
        custom=current_app.config["CUSTOM"],
        api_label_list=get_api_label_list(),
        active_api_label=active_api_label,
        wallet_address=wallet["address"] if wallet["address"] is not None else "Not wallet",
        wallet_status="Active" if wallet["status"] != 0 else "Not activated",
        wallet_time=datetime.fromtimestamp(int(str(wallet["last_activate_time"])[:10])) if wallet[
                                                                                               "last_activate_time"] != 0 else "Not activated"
    )


@app.errorhandler(404)
def not_found(error):
    active_api_label = get_default_api_label()
    return (
        render_template(
            "error.html",
            coin_list=get_coins(),
            custom=current_app.config["CUSTOM"],
            api_label_list=get_api_label_list(),
            active_api_label=active_api_label
        ),
        404,
    )

@app.route("/report", methods=["GET"])
@app.route("/report/<active_api_label>", methods=["GET"])
@login_required
def report_index(active_api_label=""):
    if current_user.status != 'active':
        return redirect(url_for('main.logout_page'))
    if active_api_label == "":
        active_api_label = get_default_api_label()
    scraper.scrape(active_api_label, app)
    ranges = timeranges()
    daterange = request.args.get("daterange")

    if daterange is not None:
        daterange = daterange.split(" - ")
        if len(daterange) == 2:
            try:
                start = (
                        datetime.combine(
                            datetime.fromisoformat(daterange[0]), datetime.min.time()
                        ).timestamp()
                        * 1000
                )
                end = (
                        datetime.combine(
                            datetime.fromisoformat(daterange[1]), datetime.max.time()
                        ).timestamp()
                        * 1000
                )
                startdate, enddate = daterange[0], daterange[1]
                return redirect(
                    url_for("main.report_page", start=startdate, end=enddate, active_api_label=active_api_label))
            except Exception:
                pass
    weekstart = (datetime.combine(datetime.fromisoformat(ranges[2][0]), datetime.min.time()).timestamp() * 1000)
    weekend = (datetime.combine(datetime.fromisoformat(ranges[2][1]), datetime.max.time()).timestamp() * 1000)
    start = (datetime.combine(datetime.fromisoformat(ranges[2][0]), datetime.min.time()).timestamp() * 1000)
    end = (datetime.combine(datetime.fromisoformat(ranges[2][1]), datetime.max.time()).timestamp() * 1000)
    startdate, enddate = ranges[2][0], ranges[2][1]
    by_date = db_manager.query(
        active_api_label,
        'SELECT DATE(time / 1000, "unixepoch") AS Date, SUM(income) AS inc FROM income_model WHERE asset <> "BNB" AND incomeType <> "TRANSFER" AND time >= ?  AND time <= ? GROUP BY Date',
        [start, end]
    )

    by_symbol = db_manager.query(active_api_label,
                                 'SELECT SUM(income) AS inc, symbol FROM income_model WHERE asset <> "BNB" AND incomeType <> "TRANSFER" AND time >= ? AND time <= ? GROUP BY symbol ORDER BY inc DESC',
                                 [start, end],
                                 )

    week = db_manager.query(active_api_label,
                            'SELECT SUM(income) FROM income_model WHERE asset <> "BNB" AND incomeType <> "TRANSFER" AND time >= ? AND time <= ?',
                            [weekstart, weekend],
                            one=True,
                            )
    balance = db_manager.query(active_api_label, "SELECT totalWalletBalance FROM account_model", one=True)
    balance = float(balance[0])

    temptotal: tuple[list[float], list[float]] = ([], [])
    profit_period = balance - zero_value(week[0])

    temp: tuple[list[float], list[float]] = ([], [])

    for each in by_date:
        temp[0].append(round(float(each[1]), 2))
        temp[1].append(each[0])
        temptotal[1].append(each[0])
        temptotal[0].append(round(profit_period + float(each[1]), 2))
        profit_period += float(each[1])
    by_date = temp
    total_by_date = temptotal

    temp = ([], [])
    for each in by_symbol:
        temp[0].append(each[1])
        temp[1].append(round(float(each[0]), 2))
    by_symbol = temp

    report: Dict = get_report(
        start=int(start),
        end=int(end),
        api_label=active_api_label,
        user_id=current_user.id
    )

    wallet = get_wallet_by_user_id(user_id=current_user.id)
    status = is_activate(user_id=current_user.id)
    print(f"User: {current_user.username} | Active: {status}")
    return render_template(
        "report.html",
        data=[by_date, by_symbol, total_by_date],
        coin_list=get_coins(active_api_label),
        lastupdate=get_lastupdate(active_api_label),
        startdate=startdate,
        enddate=enddate,
        timeranges=ranges,
        custom=current_app.config["CUSTOM"],
        api_label_list=get_api_label_list(),
        active_api_label=active_api_label,
        wallet_address=wallet["address"] if wallet["address"] is not None else "Not wallet",
        wallet_status="Active" if wallet["status"] != 0 else "Not activated",
        wallet_time=datetime.fromtimestamp(int(str(wallet["last_activate_time"])[:10])) if wallet[
                                                                                               "last_activate_time"] != 0 else "Not activated",
        report_HowMuchDidTheBotEarnDuringThePeriod=report["HowMuchDidTheBotEarnDuringThePeriod"],       # Decimal
        report_HowMuchDidYouWithdrawAndWhen=report["HowMuchDidYouWithdrawAndWhen"],                     # List[Dict]
        report_HowMuchDidTheUserTopUpAndWhen=report["HowMuchDidTheUserTopUpAndWhen"],                   # List[Dict]
        report_Remains=report["Remains"]                                                                # Decimal
    )

@app.route("/report/<start>/<end>", methods=["GET"])
@app.route("/report/<active_api_label>/<start>/<end>", methods=["GET"])
@login_required
def report_page(start, end, active_api_label=""):
    if current_user.status != 'active':
        return redirect(url_for('main.logout_page'))
    if active_api_label == "":
        active_api_label = get_default_api_label()
    scraper.scrape(active_api_label, app)
    ranges = timeranges()
    daterange = request.args.get("daterange")
    if daterange is not None:
        daterange = daterange.split(" - ")
        if len(daterange) == 2:
            try:
                start = (
                        datetime.combine(
                            datetime.fromisoformat(daterange[0]), datetime.min.time()
                        ).timestamp()
                        * 1000
                )
                end = (
                        datetime.combine(
                            datetime.fromisoformat(daterange[1]), datetime.max.time()
                        ).timestamp()
                        * 1000
                )
                startdate, enddate = daterange[0], daterange[1]
                return redirect(
                    url_for("main.report_page", start=startdate, end=enddate, active_api_label=active_api_label))
            except Exception:
                return redirect(url_for("main.report_page", start=start, end=end, active_api_label=active_api_label))
    try:
        startdate, enddate = start, end
        start = (
                datetime.combine(datetime.fromisoformat(start), datetime.min.time()).timestamp() * 1000
        )
        end = datetime.combine(datetime.fromisoformat(end), datetime.max.time()).timestamp() * 1000
    except Exception:
        startdate, enddate = ranges[2][0], ranges[2][1]
        return redirect(url_for("main.report_page", start=startdate, end=enddate, active_api_label=active_api_label))

    todaystart = (
            datetime.combine(datetime.fromisoformat(ranges[0][0]), datetime.min.time()).timestamp()
            * 1000
    )
    todayend = (
            datetime.combine(datetime.fromisoformat(ranges[0][1]), datetime.max.time()).timestamp()
            * 1000
    )
    weekstart = (
            datetime.combine(datetime.fromisoformat(ranges[2][0]), datetime.min.time()).timestamp()
            * 1000
    )
    weekend = (
            datetime.combine(datetime.fromisoformat(ranges[2][1]), datetime.max.time()).timestamp()
            * 1000
    )

    balance = db_manager.query(active_api_label, "SELECT totalWalletBalance FROM account_model", one=True)
    by_date = db_manager.query(active_api_label,
                               'SELECT DATE(time / 1000, "unixepoch") AS Date, SUM(income) AS inc FROM income_model WHERE asset <> "BNB" AND incomeType <> "TRANSFER" AND time >= ?  AND time <= ? GROUP BY Date',
                               [start, end],
                               )

    by_symbol = db_manager.query(active_api_label,
                                 'SELECT SUM(income) AS inc, symbol FROM income_model WHERE asset <> "BNB" AND incomeType <> "TRANSFER" AND time >= ? AND time <= ? GROUP BY symbol ORDER BY inc DESC',
                                 [start, end],
                                 )

    balance = float(balance[0])

    temptotal: tuple[list[float], list[float]] = ([], [])

    customframe = db_manager.query(active_api_label,
                                   'SELECT SUM(income) FROM income_model WHERE asset <> "BNB" AND incomeType <> "TRANSFER" AND time >= ? AND time <= ?',
                                   [start, end],
                                   one=True,
                                   )
    profit_period = balance - zero_value(customframe[0])

    temp: tuple[list[float], list[float]] = ([], [])
    for each in by_date:
        temp[0].append(round(float(each[1]), 2))
        temp[1].append(each[0])
        temptotal[1].append(each[0])
        temptotal[0].append(round(profit_period + float(each[1]), 2))
        profit_period += float(each[1])
    by_date = temp
    total_by_date = temptotal

    temp = ([], [])
    for each in by_symbol:
        temp[0].append(each[1])
        temp[1].append(round(float(each[0]), 2))
    by_symbol = temp


    report: Dict = get_report(
        start=int(start),
        end=int(end),
        api_label=active_api_label,
        user_id=current_user.id
    )

    wallet = get_wallet_by_user_id(user_id=current_user.id)
    status = is_activate(user_id=current_user.id)
    print(f"User: {current_user.username} | Active: {status}")
    return render_template(
        "report.html",
        data=[by_date, by_symbol, total_by_date],
        coin_list=get_coins(active_api_label),
        lastupdate=get_lastupdate(active_api_label),
        startdate=startdate,
        enddate=enddate,
        timeranges=ranges,
        custom=current_app.config["CUSTOM"],
        api_label_list=get_api_label_list(),
        active_api_label=active_api_label,
        wallet_address=wallet["address"] if wallet["address"] is not None else "Not wallet",
        wallet_status="Active" if wallet["status"] != 0 else "Not activated",
        wallet_time=datetime.fromtimestamp(int(str(wallet["last_activate_time"])[:10])) if wallet[
                                                                                               "last_activate_time"] != 0 else "Not activated",
        report_HowMuchDidTheBotEarnDuringThePeriod=report["HowMuchDidTheBotEarnDuringThePeriod"],  # Decimal
        report_HowMuchDidYouWithdrawAndWhen=report["HowMuchDidYouWithdrawAndWhen"],  # List[Dict]
        report_HowMuchDidTheUserTopUpAndWhen=report["HowMuchDidTheUserTopUpAndWhen"],  # List[Dict]
        report_Remains=report["Remains"]  # Decimal
    )