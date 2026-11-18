import csv
import html
import io
import math

from aiogram import Bot, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Document, Message

from bot.keyboards import get_main_menu_keyboard
from config import settings
from database.crud import bulk_upsert_costs, upsert_product_costs

costs_router = Router()
_MAX_CSV_SIZE_BYTES = 2 * 1024 * 1024


def _is_admin(message: Message) -> bool:
    return bool(message.from_user and message.from_user.id == settings.TG_ADMIN_ID)


class BulkCostsForm(StatesGroup):
    waiting_for_data = State()


class CostsForm(StatesGroup):
    marketplace = State()
    sku = State()
    cost_price = State()
    packaging_cost = State()


@costs_router.message(StateFilter(BulkCostsForm, CostsForm), Command("cancel", "menu", "start"))
@costs_router.message(StateFilter(BulkCostsForm, CostsForm), F.text == "Отмена")
async def cmd_cancel_costs(message: Message, state: FSMContext) -> None:
    if not _is_admin(message):
        return
    await state.clear()
    await message.answer("❌ Ввод себестоимости отменен.", reply_markup=get_main_menu_keyboard())


@costs_router.message(F.text == "📑 Массовая загрузка себестоимости")
@costs_router.message(Command("bulk_costs"))
async def cmd_bulk_costs_start(message: Message, state: FSMContext) -> None:
    if not _is_admin(message):
        return
    await state.set_state(BulkCostsForm.waiting_for_data)
    await message.answer(
        "📤 <b>Массовая загрузка себестоимости</b>\n\n"
        "Отправьте список сообщением или прикрепите <code>.csv</code> файл.\n\n"
        "<b>Формат строк (через точку с запятой):</b>\n"
        "<code>маркетплейс;артикул;себестоимость;упаковка</code>\n\n"
        "<b>Пример:</b>\n"
        "<code>ozon;GEL-BASE-15ML;145.50;18.00</code>\n"
        "<code>ozon;GEL-TOP-15ML;160.00;18.00</code>\n"
        "<code>wb;WB-BASE-COSM;130.00;15.50</code>\n\n"
        "<i>Для отмены отправьте /cancel или /menu</i>",
        parse_mode="HTML",
    )


@costs_router.message(BulkCostsForm.waiting_for_data, F.text)
async def process_bulk_costs_text(message: Message, state: FSMContext) -> None:
    if not _is_admin(message):
        return
    raw_text = (message.text or "").strip()
    parsed_items = []
    errors = []

    for line_num, line in enumerate(raw_text.splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split(";")]
        if len(parts) != 4:
            errors.append(f"Строка {line_num}: требуется 4 параметра через ';'")
            continue

        mp, sku, cost_str, pack_str = parts
        if mp.lower() not in ("ozon", "wb"):
            errors.append(f"Строка {line_num}: маркетплейс должен быть ozon или wb")
            continue
        if not sku:
            errors.append(f"Строка {line_num}: пустой артикул SKU")
            continue

        try:
            cost_val = float(cost_str.replace(",", "."))
            pack_val = float(pack_str.replace(",", "."))
            if not math.isfinite(cost_val) or not math.isfinite(pack_val) or cost_val < 0 or pack_val < 0:
                raise ValueError("negative or non-finite")
            parsed_items.append(
                {
                    "marketplace": mp.lower(),
                    "sku": sku,
                    "cost_price": cost_val,
                    "packaging_cost": pack_val,
                }
            )
        except ValueError:
            errors.append(f"Строка {line_num}: неверный формат чисел")

    if not parsed_items:
        err_preview = html.escape("\n".join(errors[:5]) or "Нет распознанных строк")
        await message.answer(f"❌ Не удалось обработать данные.\nОшибки:\n{err_preview}")
        return

    saved_count = await bulk_upsert_costs(parsed_items)
    await state.clear()

    err_report = f"\n⚠️ Пропущено строк с ошибками: {len(errors)}" if errors else ""
    await message.answer(
        f"✅ <b>Массовая загрузка завершена!</b>\n"
        f"Успешно сохранено/обновлено артикулов: <b>{saved_count}</b>{err_report}",
        parse_mode="HTML",
        reply_markup=get_main_menu_keyboard(),
    )


@costs_router.message(BulkCostsForm.waiting_for_data, F.document)
async def process_bulk_costs_document(message: Message, state: FSMContext, bot: Bot) -> None:
    if not _is_admin(message):
        return
    doc: Document | None = message.document
    if not doc or not doc.file_name or not doc.file_name.lower().endswith((".csv", ".txt")):
        await message.answer(
            "Пожалуйста, отправьте файл формата <code>.csv</code> или <code>.txt</code>.",
            parse_mode="HTML",
        )
        return
    if doc.file_size and doc.file_size > _MAX_CSV_SIZE_BYTES:
        await message.answer("❌ Файл слишком большой (максимум 2 МБ).")
        return

    file_bytes = io.BytesIO()
    await bot.download(doc, destination=file_bytes)
    content = file_bytes.getvalue().decode("utf-8-sig", errors="ignore")

    reader = csv.reader(content.splitlines(), delimiter=";")
    parsed_items = []
    for row in reader:
        if len(row) < 4:
            continue
        mp, sku, cost_str, pack_str = [r.strip() for r in row[:4]]
        if not sku or mp.lower() not in ("ozon", "wb"):
            continue
        try:
            cost_val = float(cost_str.replace(",", "."))
            pack_val = float(pack_str.replace(",", "."))
            if not math.isfinite(cost_val) or not math.isfinite(pack_val) or cost_val < 0 or pack_val < 0:
                continue
            parsed_items.append(
                {
                    "marketplace": mp.lower(),
                    "sku": sku,
                    "cost_price": cost_val,
                    "packaging_cost": pack_val,
                }
            )
        except ValueError:
            continue

    if not parsed_items:
        await message.answer("❌ В файле не найдено корректных строк с данными.")
        return

    saved_count = await bulk_upsert_costs(parsed_items)
    await state.clear()
    await message.answer(
        f"✅ <b>Файл обработан!</b>\nЗагружено артикулов: <b>{saved_count}</b>",
        parse_mode="HTML",
        reply_markup=get_main_menu_keyboard(),
    )


@costs_router.message(Command("costs"))
async def cmd_costs_start(message: Message, state: FSMContext) -> None:
    if not _is_admin(message):
        return
    await state.set_state(CostsForm.marketplace)
    await message.answer(
        "Введите маркетплейс для обновления себестоимости (<b>ozon</b> или <b>wb</b>).\n"
        "<i>Для отмены отправьте /cancel</i>",
        parse_mode="HTML",
    )


@costs_router.message(CostsForm.marketplace, F.text)
async def process_costs_mp(message: Message, state: FSMContext) -> None:
    if not _is_admin(message):
        return
    mp = (message.text or "").strip().lower()
    if mp not in ("ozon", "wb"):
        await message.answer("Пожалуйста, укажите строго <code>ozon</code> или <code>wb</code>:", parse_mode="HTML")
        return
    await state.update_data(marketplace=mp)
    await state.set_state(CostsForm.sku)
    await message.answer("Введите артикул товара (SKU / Offer ID):")


@costs_router.message(CostsForm.sku, F.text)
async def process_costs_sku(message: Message, state: FSMContext) -> None:
    if not _is_admin(message):
        return
    sku = (message.text or "").strip()
    if not sku:
        await message.answer("Артикул не может быть пустым. Введите SKU / Offer ID:")
        return
    await state.update_data(sku=sku)
    await state.set_state(CostsForm.cost_price)
    await message.answer(
        "Введите себестоимость закупки 1 ед. товара (в рублях, например <code>145.50</code>):",
        parse_mode="HTML",
    )


@costs_router.message(CostsForm.cost_price, F.text)
async def process_costs_price(message: Message, state: FSMContext) -> None:
    if not _is_admin(message):
        return
    try:
        val = float((message.text or "").replace(",", ".").strip())
        if not math.isfinite(val) or val < 0:
            raise ValueError
    except ValueError:
        await message.answer("Введите корректное неотрицательное число для себестоимости:")
        return
    await state.update_data(cost_price=val)
    await state.set_state(CostsForm.packaging_cost)
    await message.answer(
        "Введите стоимость расходников на упаковку 1 ед. (в рублях, например <code>18.0</code>):",
        parse_mode="HTML",
    )


@costs_router.message(CostsForm.packaging_cost, F.text)
async def process_packaging_cost(message: Message, state: FSMContext) -> None:
    if not _is_admin(message):
        return
    try:
        pack_val = float((message.text or "").replace(",", ".").strip())
        if not math.isfinite(pack_val) or pack_val < 0:
            raise ValueError
    except ValueError:
        await message.answer("Введите корректное неотрицательное число для стоимости упаковки:")
        return

    data = await state.get_data()
    await state.clear()

    product = await upsert_product_costs(
        sku=data["sku"],
        marketplace=data["marketplace"],
        cost_price=data["cost_price"],
        packaging_cost=pack_val,
    )
    await message.answer(
        f"✅ <b>Себестоимость сохранена</b>\n"
        f"Маркетплейс: <code>{html.escape(product.marketplace.upper())}</code>\n"
        f"SKU: <code>{html.escape(product.sku)}</code>\n"
        f"Закупка: <b>{product.cost_price:.2f} ₽</b>\n"
        f"Упаковка: <b>{product.packaging_cost:.2f} ₽</b>",
        parse_mode="HTML",
        reply_markup=get_main_menu_keyboard(),
    )
