from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import Message
from database.crud import record_first_mile_supply

logistics_router = Router()


class FirstMileForm(StatesGroup):
    supply_number = State()
    marketplace = State()
    sku = State()
    quantity = State()
    transport_cost = State()


@logistics_router.message(Command("first_mile"))
async def cmd_first_mile_start(message: Message, state: FSMContext) -> None:
    await state.set_state(FirstMileForm.supply_number)
    await message.answer("Введите номер поставки (например, <code>FBO-2026-8841</code>):", parse_mode="HTML")


@logistics_router.message(FirstMileForm.supply_number)
async def process_supply_num(message: Message, state: FSMContext) -> None:
    await state.update_data(supply_number=(message.text or "").strip())
    await state.set_state(FirstMileForm.marketplace)
    await message.answer("Укажите маркетплейс поставки (<b>ozon</b> или <b>wb</b>):", parse_mode="HTML")


@logistics_router.message(FirstMileForm.marketplace)
async def process_supply_mp(message: Message, state: FSMContext) -> None:
    mp = (message.text or "").strip().lower()
    if mp not in ("ozon", "wb"):
        await message.answer("Пожалуйста, введите <code>ozon</code> или <code>wb</code>:", parse_mode="HTML")
        return
    await state.update_data(marketplace=mp)
    await state.set_state(FirstMileForm.sku)
    await message.answer("Введите отгруженный артикул (SKU):")


@logistics_router.message(FirstMileForm.sku)
async def process_supply_sku(message: Message, state: FSMContext) -> None:
    await state.update_data(sku=(message.text or "").strip())
    await state.set_state(FirstMileForm.quantity)
    await message.answer("Введите количество отгруженных единиц (целое число):")


@logistics_router.message(FirstMileForm.quantity)
async def process_supply_qty(message: Message, state: FSMContext) -> None:
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


@logistics_router.message(FirstMileForm.transport_cost)
async def process_supply_cost(message: Message, state: FSMContext) -> None:
    try:
        cost = float((message.text or "").replace(",", ".").strip())
        if cost < 0:
            raise ValueError
    except ValueError:
        await message.answer("Введите корректную сумму затрат в рублях:")
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
        f"Номер: <code>{supply.supply_number}</code> ({supply.marketplace.upper()})\n"
        f"SKU: <code>{supply.sku}</code> — {supply.quantity} шт.\n"
        f"Общие затраты ТК: <b>{supply.total_transport_cost:.2f} ₽</b>\n"
        f"Распределено на 1 ед.: <b>{supply.unit_cost:.2f} ₽/шт.</b>",
        parse_mode="HTML",
    )