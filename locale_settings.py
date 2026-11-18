"""Locale settings: currency, co-lending or not, partner share, date order, number format, payment channels and
the order of a borrower's given and family names.

They live in .env, are typed on the Connect tab, and default to India, so a project with no such lines
behaves exactly as it did before they existed. A country preset only fills the boxes in as a starting point
the person then edits; nothing below is read from it later except the browser's number-format locale.

Stage A of the global rollout (docs/superpowers/specs/2026-11-17-global-locale-design.md).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from pathlib import Path

from matcher.names import ORDERS

DATE_ORDERS = ("auto", "day-first", "month-first")
NUMBER_FORMATS = ("auto", "1,234.56", "1.234,56", "1 234,56", "1'234.56")
NAME_ORDERS = ORDERS  # how a borrower's name splits into given and family name (matcher.names)

# Starting points, not facts about a country. UNVERIFIED for every preset except India's channel names
# (those come from the sandbox work): none was checked against a real bank export or a real Fineract of that
# country. The non-India presets set dates and numbers to "auto" because banks in one country differ.
# `ui_locale` is only the browser's Intl locale for showing money; Eurozone has no single right answer
# (en-IE is a choice, de-DE or fr-FR would group differently), and en-KE needs a browser that knows it.
# The channel lists are typical payment-scheme names, not what any one Fineract has as payment types.
PRESETS = {
    "India": {"currency": "INR", "currency_name": "Indian Rupee", "date_order": "day-first", "number_format": "1,234.56",
              "channels": "UPI,NEFT,IMPS,NACH", "ui_locale": "en-IN", "name_order": "western"},
    "United Kingdom": {"currency": "GBP", "currency_name": "Pound Sterling", "date_order": "auto", "number_format": "auto",
                       "channels": "FASTER PAYMENTS,BACS,CHAPS,DIRECT DEBIT", "ui_locale": "en-GB", "name_order": "western"},
    "Eurozone (SEPA)": {"currency": "EUR", "currency_name": "Euro", "date_order": "auto", "number_format": "auto",
                        "channels": "SEPA,SEPA INSTANT,SCT,SDD", "ui_locale": "en-IE", "name_order": "western"},
    "United States": {"currency": "USD", "currency_name": "US Dollar", "date_order": "auto", "number_format": "auto",
                      "channels": "ACH,WIRE,ZELLE,CHECK", "ui_locale": "en-US", "name_order": "western"},
    "Brazil": {"currency": "BRL", "currency_name": "Brazilian Real", "date_order": "auto", "number_format": "auto",
               "channels": "PIX,TED,DOC,BOLETO", "ui_locale": "pt-BR", "name_order": "western"},
    "Kenya": {"currency": "KES", "currency_name": "Kenyan Shilling", "date_order": "auto", "number_format": "auto",
              "channels": "MPESA,PESALINK,EFT", "ui_locale": "en-KE", "name_order": "western"},
    "Other": {"currency": "", "currency_name": "", "date_order": "auto", "number_format": "auto",
              "channels": "", "ui_locale": "en", "name_order": "western"},
}

# Currencies whose minor unit is not 2 decimals: a table of exceptions, not the ISO list. UNVERIFIED against
# ISO 4217's current edition. Fineract's own decimal places for the currency win where it can be asked.
MINOR_UNITS = {**dict.fromkeys(("JPY", "KRW", "VND", "CLP", "ISK", "UGX", "RWF", "XAF", "XOF", "PYG"), 0),
               **dict.fromkeys(("KWD", "BHD", "OMR", "JOD", "TND", "LYD", "IQD"), 3)}


@dataclass(frozen=True)
class Settings:
    country: str = "India"
    currency: str = "INR"
    partner_share: Decimal = Decimal("80")  # percent
    date_order: str = "day-first"
    number_format: str = "1,234.56"
    channels: tuple = ("UPI", "NEFT", "IMPS", "NACH")
    ui_locale: str = "en-IN"
    digits: int | None = None  # decimal places asked of Fineract; None: the table
    co_lending: bool = True  # False: no partner bank; partner_share is then 0, so every repayment is all lender's
    name_order: str = "western"

    @property
    def minor(self) -> int:
        return MINOR_UNITS.get(self.currency, 2) if self.digits is None else self.digits

    @property
    def ratio(self) -> str:
        """The partner:lender split as text, for messages."""
        return f"{_plain(self.partner_share)}:{_plain(Decimal(100) - self.partner_share)}"


DEFAULT = Settings()


def _plain(number: Decimal) -> str:
    return format(number.normalize(), "f")


def number_format_of(text: str) -> str:
    """A NUMBER_FORMAT value with any no-break space written as a plain one."""
    return text.strip().replace("\u00a0", " ").replace("\u202f", " ")


def channel_list(text: str) -> tuple:
    return tuple(dict.fromkeys(name.strip().upper() for name in text.split(",") if name.strip()))


def load(values: dict) -> Settings:
    """Settings from .env's values (a dict of name -> text). Raises ValueError, in plain words, on a bad one."""
    def given(name: str) -> str:
        return str(values.get(name) or "").strip()

    country = given("COUNTRY") or "India"
    if country not in PRESETS:
        raise ValueError(f"COUNTRY must be one of {', '.join(PRESETS)}")
    preset = PRESETS[country]
    currency = (given("CURRENCY") or preset["currency"]).upper()
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise ValueError("CURRENCY must be a three-letter ISO 4217 code such as EUR" +
                         (" (it is empty: type your currency's code)" if not currency else ""))
    try:
        share = Decimal(given("PARTNER_SHARE_PERCENT") or "80")
        if not share.is_finite() or not 0 <= share <= 100:
            raise InvalidOperation
    except InvalidOperation:
        raise ValueError("PARTNER_SHARE_PERCENT must be a number from 0 to 100, such as 80 or 62.5") from None
    order = given("DATE_ORDER") or preset["date_order"]
    if order not in DATE_ORDERS:
        raise ValueError(f"DATE_ORDER must be one of {', '.join(DATE_ORDERS)}")
    fmt = number_format_of(given("NUMBER_FORMAT")) or preset["number_format"]
    if fmt not in NUMBER_FORMATS:
        raise ValueError(f"NUMBER_FORMAT must be one of {', '.join(NUMBER_FORMATS)}")
    channels = channel_list(given("CHANNELS") or preset["channels"])
    colend = given("CO_LENDING").lower() or "yes"
    if colend not in ("yes", "no"):
        raise ValueError("CO_LENDING must be yes or no")
    names = given("NAME_ORDER").lower() or preset["name_order"]
    if names not in NAME_ORDERS:
        raise ValueError(f"NAME_ORDER must be one of {', '.join(NAME_ORDERS)}")
    # No partner is the same split as a 0% share, so the one split() and the ledger columns stay as they are.
    return Settings(country, currency, share if colend == "yes" else Decimal(0), order, fmt, channels,
                    preset["ui_locale"], co_lending=colend == "yes", name_order=names)


def from_env(path: Path | None) -> Settings:
    """The settings in this .env file (a missing file or missing lines: the India defaults)."""
    if path is None:
        return DEFAULT
    from ai.client import read_env  # here: this module must import without the AI code
    return load(read_env(path))


def fineract_digits(fc, code: str) -> int | None:
    """Decimal places Fineract has for this currency, or None if it cannot be asked.

    UNVERIFIED key names: Fineract's currency options carry `decimalPlaces`; `digitsAfterDecimal` is what its
    loan product uses. Either is taken.
    """
    try:
        page = fc.execute_get("currencies")
        for option in (*page["selectedCurrencyOptions"], *page.get("currencyOptions", [])):
            if option.get("code") == code:
                return int(option.get("decimalPlaces", option.get("digitsAfterDecimal")))
    except Exception:  # noqa: BLE001  any refusal or odd answer: fall back to the table
        pass
    return None


def with_fineract_digits(settings: Settings, fc) -> Settings:
    """These settings with the decimal places Fineract itself has for the currency, where it says."""
    digits = fineract_digits(fc, settings.currency)
    return settings if digits is None else replace(settings, digits=digits)


def words(settings: Settings = DEFAULT) -> dict:
    """Every piece of text that differs between co-lending and a lender with no partner, in one place.

    Server messages, the page and the report read these, so no caller needs its own if.
    """
    if settings.co_lending:
        return {"report_file": "partner_report.csv", "report_name": "partner report", "report_title": "Partner report",
                "lender_tile": "Lender's share", "recorded": f"split each {settings.ratio} in the ledger",
                "ledger_how": f"split {settings.ratio} in the ledger", "post_help": f"splits it {settings.ratio} in",
                "ledger_note": "split by PARTNER_SHARE_PERCENT", "split_detail": "(partner {partner}, lender {lender})",
                "ledger_mode": f"split {settings.ratio}"}
    return {"report_file": "repayments_report.csv", "report_name": "repayments report", "report_title": "Repayments report",
            "lender_tile": "Collected", "recorded": "recorded each in the ledger as the lender's",
            "ledger_how": "recorded in the ledger as the lender's", "post_help": "records it, all as the lender's, in",
            "ledger_note": "all the lender's, no partner", "split_detail": "",
            "ledger_mode": "100% lender (no partner bank)"}


def view(settings: Settings) -> dict:
    """The effective values the Connect tab shows, and the presets it fills the boxes from."""
    return {"values": {"COUNTRY": settings.country, "CURRENCY": settings.currency,
                       "PARTNER_SHARE_PERCENT": _plain(settings.partner_share), "DATE_ORDER": settings.date_order,
                       "NUMBER_FORMAT": settings.number_format, "CHANNELS": ",".join(settings.channels),
                       "CO_LENDING": "yes" if settings.co_lending else "no", "NAME_ORDER": settings.name_order},
            "ui_locale": settings.ui_locale, "ratio": settings.ratio, "minor": settings.minor,
            "words": words(settings), "presets": PRESETS,
            "date_orders": DATE_ORDERS, "number_formats": NUMBER_FORMATS, "name_orders": NAME_ORDERS}
