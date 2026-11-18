import html
import math

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

from bot.keyboards import get_main_menu_keyboard
from config import settings
from database.crud import record_first_mile_supply

logistics_router = Router()


def _is_admin(message: Message) -> bool:
    return bool(message.from_user and message.from_user.id == settings.TG_ADMIN_ID)


class FirstMileForm(StatesGroup):
    supply_number = State()
    marketplace = State()
    sku = State()
    quantity = State()
    transport_cost = State()


@logistics_router.message(StateFilter(FirstMileForm), Command("cancel", "menu", "start"))
@logistics_router.message(StateFilter(FirstMileForm), F.text == "Отмена")
async def cmd_cancel_first_mile(message: Message, state: FSMContext) -> None:
    if not _is_admin(message):
        return
    await state.clear()
    await message.answer("❌ Учет первой мили отменен.", reply_markup=get_main_menu_keyboard())


@logistics_router.message(Command("first_mile"))
async def cmd_first_mile_start(message: Message, state: FSMContext) -> None:
    if not _is_admin(message):
        return
    await state.set_state(FirstMileForm.supply_number)
    await message.answer(
        "Введите номер поставки (например, <code>FBO-2026-8841</code>).\n"
        "<i>Для отмены отправьте /cancel</i>",
        parse_mode="HTML",
    )


@logistics_router.message(FirstMileForm.supply_number, F.text)
async def process_supply_num(message: Message, state: FSMContext) -> None:
    if not _is_admin(message):
        return
    supply_num = (message.text or "").strip()
    if not supply_num:
        await message.answer("Номер поставки не может быть пустым. Повторите ввод:")
        return
    await state.update_data(supply_number=supply_num)
    await state.set_state(FirstMileForm.marketplace)
    await message.answer("Укажите маркетплейс поставки (<b>ozon</b> или <b>wb</b>):", parse_mode="HTML")


@logistics_router.message(FirstMileForm.marketplace, F.text)
async def process_supply_mp(message: Message, state: FSMContext) -> None:
    if not _is_admin(message):
        return
    mp = (message.text or "").strip().lower()
    if mp not in ("ozon", "wb"):
        await message.answer("Пожалуйста, введите <code>ozon</code> или <code>wb</code>:", parse_mode="HTML")
        return
    await state.update_data(marketplace=mp)
    await state.set_state(FirstMileForm.sku)
    await message.answer("Введите отгруженный артикул (SKU):")


@logistics_router.message(FirstMileForm.sku, F.text)
async def process_supply_sku(message: Message, state: FSMContext) -> None:
    if not _is_admin(message):
        return
    sku = (message.text or "").strip()
    if not sku:
        await message.answer("Артикул не может быть пустым. Введите SKU:")
        return
    await state.update_data(sku=sku)
    await state.set_state(FirstMileForm.quantity)
    await message.answer("Введите количество отгруженных единиц (целое число):")


@logistics_router.message(FirstMileForm.quantity, F.text)
async def process_supply_qty(message: Message, state: FSMContext) -> None:
    if not _is_admin(message):
        return
    try:
        qty = int((message.text or "").strip())
        if qty <= 0:
            raise ValueError
    except ValueError:
        await message.answer("Количество должно быть целым числом больше 0:")
        return
    await state.update_data(quantity=qty)
    await state.set_state(FirstMileForm.transport_cost)
    await message.answer("Введите общую сумму затрат на ТК / водителя до склада МП (в рублях):")


@logistics_router.message(FirstMileForm.transport_cost, F.text)
async def process_supply_cost(message: Message, state: FSMContext) -> None:
    if not _is_admin(message):
        return
    try:
        cost = float((message.text or "").replace(",", ".").strip())
        if not math.isfinite(cost) or cost < 0:
            raise ValueError
    except ValueError:
        await message.answer("Введите корректную неотрицательную сумму затрат в рублях:")
        return

    data = await state.get_data()
    await state.clear()

    supply = await record_first_mile_supply(
        supply_number=data["supply_number"],
        marketplace=data["marketplace"],
        sku=data["sku"],
        quantity=data["quantity"],
        total_transport_cost=cost,
    )
    await message.answer(
        f"🚚 <b>Поставка первой мили учтена</b>\n"
        f"Номер: <code>{html.escape(supply.supply_number)}</code> ({html.escape(supply.marketplace.upper())})\n"
        f"SKU: <code>{html.escape(supply.sku)}</code> — {supply.quantity} шт.\n"
        f"Общие затраты ТК: <b>{supply.total_transport_cost:.2f} ₽</b>\n"
        f"Распределено на 1 ед.: <b>{supply.unit_cost:.2f} ₽/шт.</b>",
        parse_mode="HTML",
        reply_markup=get_main_menu_keyboard(),
    )
