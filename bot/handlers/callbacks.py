import html
import math
from typing import Any

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from config import settings
from database.crud import claim_price_action, finish_price_action
from services.ozon.seller_api import OzonSellerAPI
from services.wb.statistics_api import WBStatisticsAPI

callbacks_router = Router()
ozon_api = OzonSellerAPI()
wb_api = WBStatisticsAPI()


def _resolve_wb_nm_id(payload: dict, fallback_sku: str, call_data: str) -> int:
    """nmID берётся только из явно указанного поля, не из SKU или callback ID."""
    value = payload.get("nm_id")
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("Не указан положительный числовой nmID WB; создайте новый аудит")
    return value


def build_approval_keyboard(action_id: int, custom_approve_text: str = "✅ Подтвердить") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=custom_approve_text, callback_data=f"act:app:{action_id}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"act:rej:{action_id}"),
    ]])


def build_dual_ozon_approval_keyboard(act_299_id: int, profit_299: float, act_over300_id: int,
                                      price_over300: float, profit_over300: float) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"✅ Порог 299 ₽ (прибыль {profit_299:+.0f} ₽/шт.)", callback_data=f"act:app:{act_299_id}")],
        [InlineKeyboardButton(text=f"🚀 Свыше 300: {price_over300:.0f} ₽ (прибыль {profit_over300:+.0f} ₽/шт.)", callback_data=f"act:app:{act_over300_id}")],
        [InlineKeyboardButton(text="❌ Отклонить", callback_data=f"act:rej:{act_299_id}")],
    ])


def _ozon_result(response: Any, offer_id: str, product_id: str) -> None:
    if not isinstance(response, dict) or not isinstance(response.get("result"), list):
        raise RuntimeError("Неизвестный ответ Ozon; проверьте цену в кабинете")
    rows = [r for r in response["result"] if isinstance(r, dict) and
            (str(r.get("offer_id") or "") == offer_id or
             (product_id and str(r.get("product_id") or "") == product_id))]
    if len(rows) != 1:
        raise RuntimeError("Нет однозначного результата Ozon; проверьте цену в кабинете")
    row = rows[0]
    if row.get("updated") is not True or row.get("errors"):
        raise ValueError(f"Ozon отклонил цену: {row.get('errors') or row}")


def _action_id(call: CallbackQuery) -> int:
    parts = (call.data or "").split(":")
    if len(parts) != 3 or not parts[2].isdigit() or int(parts[2]) <= 0:
        raise ValueError("Некорректный идентификатор действия")
    return int(parts[2])


async def _authorized(call: CallbackQuery) -> bool:
    if not call.from_user or call.from_user.id != settings.TG_ADMIN_ID:
        await call.answer("Доступ запрещён", show_alert=True)
        return False
    return True


@callbacks_router.callback_query(F.data.startswith("act:app:"))
async def cb_approve_action(call: CallbackQuery):
    if not await _authorized(call):
        return
    try:
        action_id = _action_id(call)
    except ValueError:
        await call.answer("Некорректное действие", show_alert=True)
        return
    action = await claim_price_action(action_id)
    if not action:
        await call.answer("Действие уже обработано или выполняется", show_alert=True)
        return
    await call.answer("Отправляю запрос маркетплейсу")
    submitted_request = False
    try:
        if action.action_type != "update_price":
            raise ValueError("Неподдерживаемый тип действия")
        payload = action.payload
        price = float(payload.get("new_price") or 0)
        if not math.isfinite(price) or price <= 0:
            raise ValueError("Некорректная цена")
        operation_result = None
        if action.marketplace == "ozon":
            offer_id = str(payload.get("offer_id") or action.sku)
            product_id = str(payload.get("product_id") or "")
            submitted_request = True
            response = await ozon_api.update_price(product_id, offer_id, price)
            _ozon_result(response, offer_id, product_id)
            status = "executed"
            detail = f"Ozon подтвердил обновление цены {offer_id}: {price:.2f} ₽. Проверьте витрину и условия акции."
        elif action.marketplace == "wb":
            nm_id = _resolve_wb_nm_id(payload, str(action.sku), call.data or "")
            submitted_request = True
            result = await wb_api.update_price_and_min_price(nm_id, price)
            if result.get("status") != "submitted":
                raise RuntimeError("Неизвестный ответ WB")
            status = "submitted"
            operation_result = {"upload_id": result["upload_id"], "min_price_for_promotions": result["min_price_for_promotions"]}
            detail = (f"WB принял задачу №{result['upload_id']} для nmID {nm_id}; "
                      "цена ещё не подтверждена. Проверьте статус загрузки и карантин в кабинете. "
                      f"Рекомендуемый минимум акции {result['min_price_for_promotions']} ₽ не установлен API.")
        else:
            raise ValueError("Неизвестный маркетплейс")
        await finish_price_action(action_id, status, operation_result)
    except Exception as exc:
        # После отправки сетевой сбой/неожиданный ответ может означать уже принятое действие.
        status = "unknown" if submitted_request and not isinstance(exc, ValueError) else "pending"
        await finish_price_action(action_id, status)
        detail = (f"Ошибка: {exc}. " + ("Проверьте цену/задачу в кабинете до повторного запуска." if status == "unknown" else "Действие не подтверждено."))
    if call.message:
        await call.message.reply(html.escape(detail), parse_mode="HTML")


@callbacks_router.callback_query(F.data.startswith("act:rej:"))
async def cb_reject_action(call: CallbackQuery):
    if not await _authorized(call):
        return
    try:
        action_id = _action_id(call)
    except ValueError:
        await call.answer("Некорректное действие", show_alert=True)
        return
    action = await claim_price_action(action_id)
    if not action:
        await call.answer("Действие уже обработано или выполняется", show_alert=True)
        return
    await finish_price_action(action_id, "rejected")
    await call.answer("Рекомендация отклонена")
    if call.message:
        await call.message.reply("Рекомендация отклонена.")
