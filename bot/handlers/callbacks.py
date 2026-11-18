import json
from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from database.crud import get_pending_action, mark_action_executed, mark_action_rejected
from services.ozon.seller_api import OzonSellerAPI
from services.wb.statistics_api import WBStatisticsAPI
from products_catalog import PRODUCTS_CATALOG

callbacks_router = Router()
ozon_api = OzonSellerAPI()
wb_api = WBStatisticsAPI()


def _resolve_wb_nm_id(payload: dict, fallback_sku: str, call_data: str) -> int:
    for key in ("nm_id", "nmId", "nmID", "external_id", "product_id", "article"):
        val = payload.get(key)
        if val and str(val).strip().isdigit() and len(str(val).strip()) >= 6:
            return int(str(val).strip())

    sku_key = str(payload.get("sku") or fallback_sku or "").strip()
    if sku_key in PRODUCTS_CATALOG and PRODUCTS_CATALOG[sku_key].get("wb_sku"):
        return int(PRODUCTS_CATALOG[sku_key]["wb_sku"])

    for key in ("nm_id", "external_id", "product_id"):
        val = str(payload.get(key) or "").strip()
        if val in PRODUCTS_CATALOG and PRODUCTS_CATALOG[val].get("wb_sku"):
            return int(PRODUCTS_CATALOG[val]["wb_sku"])
        if val.isdigit():
            return int(val)

    parts = call_data.split(":")
    if parts[-1].isdigit():
        return int(parts[-1])

    raise ValueError(f"Не удалось определить числовой nmID для артикула {sku_key}")


def build_approval_keyboard(action_id: int, custom_approve_text: str = "✅ Подтвердить") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=custom_approve_text, callback_data=f"act:app:{action_id}"),
                InlineKeyboardButton(text="❌ Отклонить", callback_data=f"act:rej:{action_id}"),
            ]
        ]
    )


def build_dual_ozon_approval_keyboard(
    act_299_id: int,
    profit_299: float,
    act_over300_id: int,
    price_over300: float,
    profit_over300: float,
) -> InlineKeyboardMarkup:
    """
    Клавиатура выбора ценового сценария для Ozon:
    - Кнопка 1: Установить максимальный порог 299 ₽ (с расчетом чистой прибыли)
    - Кнопка 2: Установить оптимальную цену свыше 300 ₽ (с расчетом чистой прибыли)
    - Кнопка 3: Отклонить
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"✅ Порог 299 ₽ (прибыль {profit_299:+.0f} ₽/шт.)",
                    callback_data=f"act:app:{act_299_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"🚀 Свыше 300: {price_over300:.0f} ₽ (прибыль {profit_over300:+.0f} ₽/шт.)",
                    callback_data=f"act:app:{act_over300_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отклонить",
                    callback_data=f"act:rej:{act_299_id}",
                )
            ],
        ]
    )


@callbacks_router.callback_query(F.data.startswith("act:app:"))
async def cb_approve_action(call: CallbackQuery):
    await call.answer("⏳ Отправляю выбранную цену в API маркетплейса...")

    action_id = int(call.data.split(":")[2])
    action = await get_pending_action(action_id)
    if not action or action.status != "pending":
        await call.message.reply("ℹ️ Это действие уже было обработано ранее.")
        return

    payload = action.payload if isinstance(action.payload, dict) else json.loads(action.payload)
    success = False
    err_msg = ""
    details_str = ""

    try:
        new_price = float(payload.get("new_price") or payload.get("recommended_price") or payload.get("price") or 0.0)
        if new_price <= 0:
            raise ValueError("Некорректная целевая цена (<= 0)")

        if action.marketplace == "ozon":
            product_id = str(payload.get("product_id") or payload.get("external_id") or "")
            offer_id = str(payload.get("offer_id") or action.sku)
            res = await ozon_api.update_price(product_id, offer_id, new_price)
            success = bool(res.get("result", {}).get("task_id") or res.get("result"))
            details_str = f"Ozon SKU {offer_id} -> установлена цена {new_price:.0f} ₽"
        else:
            nm_id = _resolve_wb_nm_id(payload, str(action.sku), call.data)
            res = await wb_api.update_price_and_min_price(
                nm_id=nm_id,
                recommended_price_after_discount=new_price
            )
            success = res.get("status") == "success"
            base_p = res.get("price_before_discount_set", int(new_price))
            disc = res.get("seller_discount_pct", 0)
            details_str = f"WB nmID {nm_id}: до скидки {base_p} ₽ (скидка {disc}%), итог {new_price:.0f} ₽"
    except Exception as exc:
        success = False
        err_msg = str(exc)

    if success:
        await mark_action_executed(action_id)
        await call.message.edit_text(
            f"{call.message.html_text}\n\n<b>✅ Цена успешно обновлена!</b>\n<code>{details_str}</code>",
            parse_mode="HTML"
        )
    else:
        await call.message.edit_text(
            f"{call.message.html_text}\n\n<b>❌ Ошибка при установке цены:</b>\n<code>{err_msg}</code>",
            parse_mode="HTML",
            reply_markup=build_approval_keyboard(action_id, "🔄 Повторить отправку")
        )


@callbacks_router.callback_query(F.data.startswith("act:rej:"))
async def cb_reject_action(call: CallbackQuery):
    await call.answer("Рекомендация отклонена")
    action_id = int(call.data.split(":")[2])
    await mark_action_rejected(action_id)
    await call.message.edit_text(
        f"{call.message.html_text}\n\n<b>❌ Рекомендация отклонена пользователем.</b>",
        parse_mode="HTML"
    )