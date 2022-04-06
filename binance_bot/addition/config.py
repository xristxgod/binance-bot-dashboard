import os
import decimal
import logging
import emoji
from decouple import config

from hlp import BASE_DIR, BASE_FILE, ROOT_DIR

logger = logging.getLogger(__name__)

_node_url = config("TRON_NODE", "http://3.225.171.164:8090")
_network = config("TRON_NETWORK", "shasta")

decimals = decimal.Context()
decimals.prec = 8

percentage_of_pnl = int(config("PERCENTAGE_OF_WITHDRAWALS_PER_DAY", "30"))
db_path = os.path.join(BASE_DIR, "database.db")

adminWallet = {
    "address": config("ADMIN_ADDRESS", "THadHjK1UhZvnHaVPYNTsTDCR3mPd8XXDK"),
    "privateKey": config("ADMIN_PRIVATE_KEY", "00caa190282d4c89dbdc9c0481ac7c200348f6ab231ff3f158f595b772813e79")
}
LIMIT_USDT = decimals.create_decimal(config("LIMIT_USDT", "2.0"))

TOKEN = config("TOKEN", "5283221517:AAGx3HIayIo9kKpaHlQlM85TCZyygqr5fq8")
ADMIN_IDS = config("ADMIN_IDS", "688225742,").split(",")


if _network == "mainnet":    __token = "tokensMainNet.json"
else:   __token = "tokensShastaNet.json"
# File for TRC20 tokens
fileTokens = os.path.join(BASE_FILE, __token)

symbol = {
    "add": emoji.emojize(":green_circle:"),
    "dec": emoji.emojize(":red_circle:")
}

PDF_INSTRUCTIONS = os.path.join(BASE_FILE, "instructions.pdf")
USERS_FILE = os.path.join(BASE_FILE, "users_file.json")

SENDER_EMAIL = config("SENDER_EMAIL", "test@gmail.com")
SENDER_PASSWORD = config("SENDER_PASSWORD", "test00")
# https://www.youtube.com/watch?v=zYWpEJAHvaI | 49:00 - Если проблемиы с отправкой сообщения
SENDER_SERVER = config("SENDER_SERVER", "smtp.gmail.com")

BOT_NAME = config("USER_BOT_NAME", "@TestAdminBinanceDashboardBot")