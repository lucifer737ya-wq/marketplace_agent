"""
Полный каталог сертифицированной продукции бренда YaSoKo.
Все товары зарегистрированы в системе «Честный ЗНАК» и имеют Декларацию о соответствии (ТР ТС 009/2011).
"""

from typing import Any, Dict, Optional

LEGAL_COMPLIANCE_INFO = (
    "Продукция бренда YaSoKo официально сертифицирована, прошла лабораторные испытания, "
    "имеет действующую декларацию о соответствии ТР ТС 009/2011 «О безопасности парфюмерно-косметической продукции» "
    "и подлежит обязательной национальной маркировке в государственной системе «Честный ЗНАК»."
)

GEL_COMMON_DESCRIPTION = (
    "YaSoKo — профессиональный камуфляжный моделирующий гель для наращивания, укрепления и коррекции ногтей. "
    "Плотный натуральный оттенок перекрывает ногтевую пластину уже с первого слоя, создавая эффект аккуратного, ухоженного маникюра. "
    "Формула разработана так, чтобы материал был предсказуемым в работе. Средне-вязкая консистенция не растекается и не затекает под кутикулу, "
    "что позволяет наносить гель сразу на несколько ногтей и спокойно формировать архитектуру без риска потери формы. "
    "После полимеризации в UV или LED-лампе в течение 60–90 секунд гель полностью сохраняет заданную форму — усадка отсутствует, "
    "проплешин не образуется. Плотная структура сокращает время опила до минимума. Высокая адгезия обеспечивает прочное сцепление "
    "с натуральным ногтем, а pH-нейтральный состав делает материал подходящим даже для тонких и ломких ногтей. "
    "Стойкость покрытия до четырех недель без сколов, трещин и отслоек. "
    "Формула HEMA FREE и 11-FREE не содержит токсичных мономеров, формальдегидов, толуола и камфоры. "
    "Материал не вызывает жжения в лампе и не имеет резкого химического запаха. "
    "Объем: 15 г. Время полимеризации: 60–90 секунд в UV/LED-лампе."
)

ALLOWED_UPSELL_LIST = (
    "В ассортименте YaSoKo есть ТОЛЬКО следующие категории для рекомендаций:\n"
    "1. Базы (15 мл): каучуковая эластичная (арт. 21000), многофункциональная жесткая 5 в 1 (арт. 21001), адгезивная 8 в 1 (арт. 21002).\n"
    "2. Топы (15 мл): бриллиантовый с УФ-защитой без липкого слоя (арт. 22000), кристально-прозрачный самовыравнивающийся (арт. 22001), молочный полупрозрачный Milky Top (арт. 22002).\n"
    "3. Камуфлирующие гели YaSoKo (15 г) других оттенков палитры.\n"
    "СТРОГО ЗАПРЕЩЕНО рекомендовать несуществующие товары нашего бренда: праймеры, обезжириватели, жидкости для снятия, безворсовые салфетки, лампы и пилки!"
)

# Паспорт товаров бренда (каждая запись содержит и "title", и "name" для полной совместимости всех модулей)
PRODUCTS_CATALOG: Dict[str, Dict[str, Any]] = {
    # --- ОСНОВНАЯ ЛИНЕЙКА ГЕЛЕЙ 1001–1511 (с названиями цветов) ---
    "1001": {
        "art": "1001", "shade": "#01", "color_name": "Ванильный Крем",
        "name": "Гель YaSoKo 15 г, #01 Ванильный Крем",
        "title": "Гель для наращивания и моделирования ногтей YaSoKo 15 г, #01 Ванильный Крем",
        "category": "Камуфлирующий моделирующий UV/LED-гель", "volume": "15 г",
        "formula": "HEMA FREE, 11-FREE, pH-нейтральный",
        "ozon_sku": "3405669329", "wb_sku": "765197240", "barcode": "4638944332008", "gtin": "04638944332008",
        "composition": "Acrylates/Carbamate Copolymer; Acrylates/VP Copolymer; Acrylates Copolymer; CI 77891; CI 77491; CI 77492; CI 77499; CI 77742",
        "curing_time": "60–90 сек UV/LED",
        "recommended_upsell": "эластичная каучуковая база YaSoKo (арт. 21000) под подложку или топ без липкого слоя (арт. 22000)",
    },
    "1002": {
        "art": "1002", "shade": "#02", "color_name": "Зефирная Мечта",
        "name": "Гель YaSoKo 15 г, #02 Зефирная Мечта",
        "title": "Гель для наращивания и моделирования ногтей YaSoKo 15 г, #02 Зефирная Мечта",
        "category": "Камуфлирующий моделирующий UV/LED-гель", "volume": "15 г",
        "formula": "HEMA FREE, 11-FREE", "ozon_sku": "3405668968", "wb_sku": "765197247",
        "barcode": "4638944332015", "gtin": "04638944332015",
        "composition": "Acrylates/Carbamate Copolymer; Acrylates/VP Copolymer; Acrylates Copolymer; CI 77891; CI 77491; CI 77492; CI 77499; CI 77742",
        "curing_time": "60–90 сек UV/LED",
        "recommended_upsell": "бриллиантовый топ YaSoKo без липкого слоя (арт. 22000)",
    },
    "1003": {
        "art": "1003", "shade": "#03", "color_name": "Клубничный Джем",
        "name": "Гель YaSoKo 15 г, #03 Клубничный Джем",
        "title": "Гель для наращивания и моделирования ногтей YaSoKo 15 г, #03 Клубничный Джем",
        "category": "Камуфлирующий моделирующий UV/LED-гель", "volume": "15 г",
        "formula": "HEMA FREE, 11-FREE", "ozon_sku": "3405668876", "wb_sku": "765197248",
        "barcode": "4638944332022", "gtin": "04638944332022",
        "composition": "Acrylates/Carbamate Copolymer; Acrylates/VP Copolymer; Acrylates Copolymer; CI 77891; CI 77491; CI 77492; CI 77499; CI 77742",
        "curing_time": "60–90 сек UV/LED",
        "recommended_upsell": "кристально-прозрачный топ YaSoKo (арт. 22001)",
    },
    "1006": {
        "art": "1006", "shade": "#06", "color_name": "Малиновый Сорбет",
        "name": "Гель YaSoKo 15 г, #06 Малиновый Сорбет",
        "title": "Гель для наращивания и моделирования ногтей YaSoKo 15 г, #06 Малиновый Сорбет",
        "category": "Камуфлирующий моделирующий UV/LED-гель", "volume": "15 г",
        "formula": "HEMA FREE, 11-FREE", "ozon_sku": "3405668993", "wb_sku": "765197241",
        "barcode": "4638944332039", "gtin": "04638944332039",
        "composition": "Acrylates/Carbamate Copolymer; Acrylates/VP Copolymer; Acrylates Copolymer; CI 77891; CI 77491; CI 77492; CI 77499; CI 77742",
        "curing_time": "60–90 сек UV/LED",
        "recommended_upsell": "бриллиантовый топ YaSoKo (арт. 22000)",
    },
    "1010": {
        "art": "1010", "shade": "#10", "color_name": "Малиновый Щербет",
        "name": "Гель YaSoKo 15 г, #10 Малиновый Щербет",
        "title": "Гель для наращивания и моделирования ногтей YaSoKo 15 г, #10 Малиновый Щербет",
        "category": "Камуфлирующий моделирующий UV/LED-гель", "volume": "15 г",
        "formula": "HEMA FREE, 11-FREE", "ozon_sku": "3405669196", "wb_sku": "765197249",
        "barcode": "4638944332046", "gtin": "04638944332046",
        "composition": "Acrylates/Carbamate Copolymer; Acrylates/VP Copolymer; Acrylates Copolymer; CI 77891; CI 77491; CI 77492; CI 77499; CI 77742",
        "curing_time": "60–90 сек UV/LED",
        "recommended_upsell": "каучуковая эластичная база YaSoKo (арт. 21000)",
    },
    "1023": {
        "art": "1023", "shade": "#23", "color_name": "Черничный Мусс",
        "name": "Гель YaSoKo 15 г, #23 Черничный Мусс",
        "title": "Гель для наращивания и моделирования ногтей YaSoKo 15 г, #23 Черничный Мусс",
        "category": "Камуфлирующий моделирующий UV/LED-гель", "volume": "15 г",
        "formula": "HEMA FREE, 11-FREE", "ozon_sku": "3405669300", "wb_sku": "765197245",
        "barcode": "4638944332053", "gtin": "04638944332053",
        "composition": "Acrylates/Carbamate Copolymer; Acrylates/VP Copolymer; Acrylates Copolymer; CI 77891; CI 77491; CI 77492; CI 77499; CI 77742",
        "curing_time": "60–90 сек UV/LED",
        "recommended_upsell": "бриллиантовый топ YaSoKo (арт. 22000)",
    },
    "1028": {
        "art": "1028", "shade": "#28", "color_name": "Миндальное Молоко",
        "name": "Гель YaSoKo 15 г, #28 Миндальное Молоко",
        "title": "Гель для наращивания и моделирования ногтей YaSoKo 15 г, #28 Миндальное Молоко",
        "category": "Камуфлирующий моделирующий UV/LED-гель", "volume": "15 г",
        "formula": "HEMA FREE, 11-FREE", "ozon_sku": "3405669632", "wb_sku": "765197242",
        "barcode": "4638944332060", "gtin": "04638944332060",
        "composition": "Acrylates/Carbamate Copolymer; Acrylates/VP Copolymer; Acrylates Copolymer; CI 77891; CI 77491; CI 77492; CI 77499; CI 77742",
        "curing_time": "60–90 сек UV/LED",
        "recommended_upsell": "молочный топ Milky Top (арт. 22002) или прозрачный топ (арт. 22001)",
    },
    "1031": {
        "art": "1031", "shade": "#31", "color_name": "Арахисовое Пралине",
        "name": "Гель YaSoKo 15 г, #31 Арахисовое Пралине",
        "title": "Гель для наращивания и моделирования ногтей YaSoKo 15 г, #31 Арахисовое Пралине",
        "category": "Камуфлирующий моделирующий UV/LED-гель", "volume": "15 г",
        "formula": "HEMA FREE, 11-FREE", "ozon_sku": "3405669169", "wb_sku": "765197243",
        "barcode": "4638944332077", "gtin": "04638944332077",
        "composition": "Acrylates/Carbamate Copolymer; Acrylates/VP Copolymer; Acrylates Copolymer; CI 77891; CI 77491; CI 77492; CI 77499; CI 77742",
        "curing_time": "60–90 сек UV/LED",
        "recommended_upsell": "бриллиантовый топ YaSoKo (арт. 22000)",
    },
    "1033": {
        "art": "1033", "shade": "#33", "color_name": "Карамельный Макиато",
        "name": "Гель YaSoKo 15 г, #33 Карамельный Макиато",
        "title": "Гель для наращивания и моделирования ногтей YaSoKo 15 г, #33 Карамельный Макиато",
        "category": "Камуфлирующий моделирующий UV/LED-гель", "volume": "15 г",
        "formula": "HEMA FREE, 11-FREE", "ozon_sku": "3405668938", "wb_sku": "765197246",
        "barcode": "4638944332084", "gtin": "04638944332084",
        "composition": "Acrylates/Carbamate Copolymer; Acrylates/VP Copolymer; Acrylates Copolymer; CI 77891; CI 77491; CI 77492; CI 77499; CI 77742",
        "curing_time": "60–90 сек UV/LED",
        "recommended_upsell": "топ без липкого слоя YaSoKo (арт. 22001)",
    },
    "1103": {
        "art": "1103", "shade": "#39", "color_name": "Ванильная Глазурь",
        "name": "Гель YaSoKo 15 г, #39 Ванильная Глазурь",
        "title": "Гель для наращивания и моделирования ногтей YaSoKo 15 г, #39 Ванильная Глазурь",
        "category": "Камуфлирующий моделирующий UV/LED-гель", "volume": "15 г",
        "formula": "HEMA FREE, 11-FREE", "ozon_sku": "3405669610", "wb_sku": "765197254",
        "barcode": "4638944332091", "gtin": "04638944332091",
        "composition": "Acrylates/Carbamate Copolymer; Acrylates/VP Copolymer; Acrylates Copolymer; CI 77891; CI 77491; CI 77492; CI 77499; CI 77742",
        "curing_time": "60–90 сек UV/LED",
        "recommended_upsell": "бриллиантовый топ YaSoKo (арт. 22000)",
    },
    "1104": {
        "art": "1104", "shade": "#40", "color_name": "Розовый Леденец",
        "name": "Гель YaSoKo 15 г, #40 Розовый Леденец",
        "title": "Гель для наращивания и моделирования ногтей YaSoKo 15 г, #40 Розовый Леденец",
        "category": "Камуфлирующий моделирующий UV/LED-гель", "volume": "15 г",
        "formula": "HEMA FREE, 11-FREE", "ozon_sku": "3405669152", "wb_sku": "765197255",
        "barcode": "4638944332107", "gtin": "04638944332107",
        "composition": "Acrylates/Carbamate Copolymer; Acrylates/VP Copolymer; Acrylates Copolymer; CI 77891; CI 77491; CI 77492; CI 77499; CI 77742",
        "curing_time": "60–90 сек UV/LED",
        "recommended_upsell": "бриллиантовый топ YaSoKo (арт. 22000)",
    },
    "1202": {
        "art": "1202", "shade": "#52", "color_name": "Молочный Жемчуг",
        "name": "Гель YaSoKo 15 г, #52 Молочный Жемчуг",
        "title": "Гель для наращивания и моделирования ногтей YaSoKo 15 г, #52 Молочный Жемчуг",
        "category": "Камуфлирующий моделирующий UV/LED-гель с шиммером", "volume": "15 г",
        "formula": "HEMA FREE, 11-FREE", "ozon_sku": "3405669481", "wb_sku": "765197262",
        "barcode": "4638944332114", "gtin": "04638944332114",
        "composition": "Acrylates/Carbamate Copolymer; Acrylates/VP Copolymer; Acrylates Copolymer; CI 77891; CI 77491; CI 77019; CI 77492; CI 77499; CI 77742",
        "curing_time": "60–90 сек UV/LED",
        "recommended_upsell": "глянцевый топ без УФ-фильтра (арт. 22001), чтобы подчеркнуть мерцание шиммера",
    },
    "1204": {
        "art": "1204", "shade": "#54", "color_name": "Клубничный Жемчуг",
        "name": "Гель YaSoKo 15 г, #54 Клубничный Жемчуг",
        "title": "Гель для наращивания и моделирования ногтей YaSoKo 15 г, #54 Клубничный Жемчуг",
        "category": "Камуфлирующий моделирующий UV/LED-гель с шиммером", "volume": "15 г",
        "formula": "HEMA FREE, 11-FREE", "ozon_sku": "3405669161", "wb_sku": "765197263",
        "barcode": "4638944332121", "gtin": "04638944332121",
        "composition": "Acrylates/Carbamate Copolymer; Acrylates/VP Copolymer; Acrylates Copolymer; CI 77891; CI 77491; CI 77019; CI 77492; CI 77499; CI 77742",
        "curing_time": "60–90 сек UV/LED",
        "recommended_upsell": "бриллиантовый топ YaSoKo (арт. 22000)",
    },
    "1206": {
        "art": "1206", "shade": "#56", "color_name": "Сиреневое Сияние",
        "name": "Гель YaSoKo 15 г, #56 Сиреневое Сияние",
        "title": "Гель для наращивания и моделирования ногтей YaSoKo 15 г, #56 Сиреневое Сияние",
        "category": "Камуфлирующий моделирующий UV/LED-гель с шиммером", "volume": "15 г",
        "formula": "HEMA FREE, 11-FREE", "ozon_sku": "3405669406", "wb_sku": "765197264",
        "barcode": "4638944332138", "gtin": "04638944332138",
        "composition": "Acrylates/Carbamate Copolymer; Acrylates/VP Copolymer; Acrylates Copolymer; CI 77891; CI 77491; CI 77019; CI 77492; CI 77499; CI 77742",
        "curing_time": "60–90 сек UV/LED",
        "recommended_upsell": "кристально-прозрачный топ YaSoKo (арт. 22001)",
    },
    "1302": {
        "art": "1302", "shade": "#59", "color_name": "Жемчужная Жвачка",
        "name": "Гель YaSoKo 15 г, #59 Жемчужная Жвачка",
        "title": "Гель для наращивания и моделирования ногтей YaSoKo 15 г, #59 Жемчужная Жвачка",
        "category": "Камуфлирующий моделирующий UV/LED-гель с шиммером", "volume": "15 г",
        "formula": "HEMA FREE, 11-FREE", "ozon_sku": "3405669162", "wb_sku": "765197196",
        "barcode": "4638944332145", "gtin": "04638944332145",
        "composition": "Acrylates/Carbamate Copolymer; Acrylates/VP Copolymer; Acrylates Copolymer; CI 77891; CI 77491; CI 77019; CI 77492; CI 77499; CI 77742",
        "curing_time": "60–90 сек UV/LED",
        "recommended_upsell": "бриллиантовый топ YaSoKo (арт. 22000)",
    },
    "1305": {
        "art": "1305", "shade": "#62", "color_name": "Аквамариновая Мечта",
        "name": "Гель YaSoKo 15 г, #62 Аквамариновая Мечта",
        "title": "Гель для наращивания и моделирования ногтей YaSoKo 15 г, #62 Аквамариновая Мечта",
        "category": "Камуфлирующий моделирующий UV/LED-гель с шиммером", "volume": "15 г",
        "formula": "HEMA FREE, 11-FREE", "ozon_sku": "3405668546", "wb_sku": "765197197",
        "barcode": "4638944332152", "gtin": "04638944332152",
        "composition": "Acrylates/Carbamate Copolymer; Acrylates/VP Copolymer; Acrylates Copolymer; CI 77891; CI 77491; CI 77019; CI 77492; CI 77499; CI 77742",
        "curing_time": "60–90 сек UV/LED",
        "recommended_upsell": "кристально-прозрачный топ YaSoKo (арт. 22001)",
    },
    "1402": {
        "art": "1402", "shade": "#66", "color_name": "Манговое Смузи",
        "name": "Гель YaSoKo 15 г, #66 Манговое Смузи",
        "title": "Гель для наращивания и моделирования ногтей YaSoKo 15 г, #66 Манговое Смузи",
        "category": "Камуфлирующий моделирующий UV/LED-гель", "volume": "15 г",
        "formula": "HEMA FREE, 11-FREE", "ozon_sku": "3405669594", "wb_sku": "765197229",
        "barcode": "4638944332169", "gtin": "04638944332169",
        "composition": "Acrylates/Carbamate Copolymer; Acrylates/VP Copolymer; Acrylates Copolymer; CI 77891; CI 77491; CI 77492; CI 77499; CI 77742",
        "curing_time": "60–90 сек UV/LED",
        "recommended_upsell": "бриллиантовый топ YaSoKo (арт. 22000)",
    },
    "1403": {
        "art": "1403", "shade": "#67", "color_name": "Желе из Личи",
        "name": "Гель YaSoKo 15 г, #67 Желе из Личи",
        "title": "Гель для наращивания и моделирования ногтей YaSoKo 15 г, #67 Желе из Личи",
        "category": "Камуфлирующий моделирующий UV/LED-гель", "volume": "15 г",
        "formula": "HEMA FREE, 11-FREE", "ozon_sku": "3405669236", "wb_sku": "765197230",
        "barcode": "4638944332176", "gtin": "04638944332176",
        "composition": "Acrylates/Carbamate Copolymer; Acrylates/VP Copolymer; Acrylates Copolymer; CI 77891; CI 77491; CI 77492; CI 77499; CI 77742",
        "curing_time": "60–90 сек UV/LED",
        "recommended_upsell": "эластичная каучуковая база YaSoKo (арт. 21000)",
    },
    "1405": {
        "art": "1405", "shade": "#69", "color_name": "Голубая Малина",
        "name": "Гель YaSoKo 15 г, #69 Голубая Малина",
        "title": "Гель для наращивания и моделирования ногтей YaSoKo 15 г, #69 Голубая Малина",
        "category": "Камуфлирующий моделирующий UV/LED-гель", "volume": "15 г",
        "formula": "HEMA FREE, 11-FREE", "ozon_sku": "3405669393", "wb_sku": "765197227",
        "barcode": "4638944332183", "gtin": "04638944332183",
        "composition": "Acrylates/Carbamate Copolymer; Acrylates/VP Copolymer; Acrylates Copolymer; CI 77891; CI 77491; CI 77492; CI 77499; CI 77742",
        "curing_time": "60–90 сек UV/LED",
        "recommended_upsell": "бриллиантовый топ YaSoKo (арт. 22000)",
    },
    "1408": {
        "art": "1408", "shade": "#72", "color_name": "Виноградная Газировка",
        "name": "Гель YaSoKo 15 г, #72 Виноградная Газировка",
        "title": "Гель для наращивания и моделирования ногтей YaSoKo 15 г, #72 Виноградная Газировка",
        "category": "Камуфлирующий моделирующий UV/LED-гель", "volume": "15 г",
        "formula": "HEMA FREE, 11-FREE", "ozon_sku": "3405669407", "wb_sku": "765197228",
        "barcode": "4638944332190", "gtin": "04638944332190",
        "composition": "Acrylates/Carbamate Copolymer; Acrylates/VP Copolymer; Acrylates Copolymer; CI 77891; CI 77491; CI 77492; CI 77499; CI 77742",
        "curing_time": "60–90 сек UV/LED",
        "recommended_upsell": "бриллиантовый топ YaSoKo (арт. 22000)",
    },
    "1503": {
        "art": "1503", "shade": "#75", "color_name": "Сахарная Вата",
        "name": "Гель YaSoKo 15 г, #75 Сахарная Вата",
        "title": "Гель для наращивания и моделирования ногтей YaSoKo 15 г, #75 Сахарная Вата",
        "category": "Камуфлирующий моделирующий UV/LED-гель", "volume": "15 г",
        "formula": "HEMA FREE, 11-FREE", "ozon_sku": "5516476562", "wb_sku": "1442223874",
        "barcode": "4638944332206", "gtin": "04638944332206",
        "composition": "Acrylates/Carbamate Copolymer; Acrylates/VP Copolymer; Acrylates Copolymer; CI 77891; CI 77491; CI 77492; CI 77499; CI 77742",
        "curing_time": "60–90 сек UV/LED",
        "recommended_upsell": "кристально-прозрачный топ YaSoKo (арт. 22001)",
    },
    "1511": {
        "art": "1511", "shade": "#83", "color_name": "Клубничный Коктейль",
        "name": "Гель YaSoKo 15 г, #83 Клубничный Коктейль",
        "title": "Гель для наращивания и моделирования ногтей YaSoKo 15 г, #83 Клубничный Коктейль",
        "category": "Камуфлирующий моделирующий UV/LED-гель", "volume": "15 г",
        "formula": "HEMA FREE, 11-FREE", "ozon_sku": "5516664814", "wb_sku": "1442223875",
        "barcode": "4638944332213", "gtin": "04638944332213",
        "composition": "Acrylates/Carbamate Copolymer; Acrylates/VP Copolymer; Acrylates Copolymer; CI 77891; CI 77491; CI 77492; CI 77499; CI 77742",
        "curing_time": "60–90 сек UV/LED",
        "recommended_upsell": "эластичная каучуковая база YaSoKo (арт. 21000)",
    },

    # --- ДРУГИЕ ГЕЛИ КАТАЛОГА ---
    "1000": {
        "art": "1000", "shade": "Прозрачный", "color_name": "Прозрачный Кристалл",
        "name": "Гель YaSoKo 15 г, Прозрачный Кристалл",
        "title": "Гель для наращивания и моделирования ногтей YaSoKo 15 г, прозрачный",
        "category": "Прозрачный конструирующий UV/LED-гель", "volume": "15 г",
        "formula": "9-FREE", "ozon_sku": None, "wb_sku": None, "barcode": "4638944332428", "gtin": "04638944332428",
        "composition": "Acrylated Aliphatic Urethane; 1-Hydroxycyclohexyl Phenyl Ketone; 2-Hydroxyethyl Methacrylate",
        "curing_time": "60–90 сек UV/LED",
        "recommended_upsell": "топ без липкого слоя YaSoKo (арт. 22000)",
    },
    "1601": {
        "art": "1601", "shade": "#85", "color_name": "Персиковый Парфе (Peach Parfait)",
        "name": "Гель YaSoKo 15 г, #85 Персиковый Парфе",
        "title": "Гель для наращивания и моделирования ногтей YaSoKo 15 г, #85 Персиковый Парфе",
        "category": "Камуфлирующий нюдовый UV/LED-гель", "volume": "15 г", "formula": "9-FREE",
        "ozon_sku": None, "wb_sku": None, "barcode": "4638944332299", "gtin": "04638944332299",
        "composition": "Acrylates/Carbamate Copolymer; Acrylates/VP Copolymer; VP/DMAPA Acrylates Copolymer; 1-Hydroxycyclohexyl Phenyl Ketone; 2-Hydroxyethyl Methacrylate; CI 77891; CI 77491",
        "curing_time": "60–90 сек UV/LED",
        "recommended_upsell": "молочный топ Milky Top (арт. 22002)",
    },
    "1701": {
        "art": "1701", "shade": "#101", "color_name": "Сиреневый Блеск (Lilac Sparkle)",
        "name": "Гель YaSoKo 15 г, кошачий глаз лавандовый #101",
        "title": "Гель для наращивания ногтей YaSoKo 15 г, кошачий глаз лавандовый #101",
        "category": "Моделирующий UV/LED-гель с эффектом «Кошачий глаз»", "volume": "15 г", "formula": "HEMA FREE",
        "ozon_sku": None, "wb_sku": None, "barcode": "4638944332350", "gtin": "04638944332350",
        "composition": "Acrylates/Carbamate Copolymer; Acrylates/VP Copolymer; Acrylates Copolymer; CI 77891; CI 77491; CI 77019; CI 77492; CI 77499; CI 77742",
        "curing_time": "60–90 сек UV/LED",
        "recommended_upsell": "кристально-прозрачный топ YaSoKo без УФ-фильтра (арт. 22001)",
    },

    # --- БАЗЫ ДЛЯ НОГТЕЙ (15 мл) ---
    "21000": {
        "art": "21000", "model": "D44",
        "name": "База каучуковая эластичная YaSoKo, 15 мл HEMA FREE",
        "title": "База каучуковая для ногтей эластичная YaSoKo, 15 мл HEMA FREE",
        "category": "Базовое покрытие (Rubber Base)", "volume": "15 мл",
        "formula": "HEMA FREE, эластичная, pH-нейтральная",
        "ozon_sku": None, "wb_sku": None, "barcode": "4638944332237", "gtin": "04638944332237",
        "composition": "Styrene/Acrylates Copolymer; Acrylates Copolymer; VP/DMAPA Acrylates Copolymer; Isobornyl Acrylate; 1-Hydroxycyclohexyl Phenyl Ketone; Ethyl (2,4,6-trimethylbenzoyl) Phenylphosphinate",
        "description": "Эластичная каучуковая база. Идеальна для тонких и слоящихся ногтей, а также в качестве тонкой амортизирующей подложки под твердые гели для моделирования.",
        "recommended_upsell": "камуфлирующий гель YaSoKo (арт. 1001 или 1028) и бриллиантовый топ (арт. 22000)",
    },
    "21001": {
        "art": "21001", "model": "D5A",
        "name": "База многофункциональная 5 в 1 жесткая YaSoKo, 15 мл HEMA FREE",
        "title": "База многофункциональная для ногтей 5 в 1 жесткая YaSoKo, 15 мл HEMA FREE",
        "category": "Базовое покрытие (Hard Base)", "volume": "15 мл",
        "formula": "HEMA FREE, жесткая армирующая",
        "ozon_sku": None, "wb_sku": None, "barcode": "4638944332244", "gtin": "04638944332244",
        "composition": "Styrene/Acrylates Copolymer; Acrylates Copolymer; VP/DMAPA Acrylates Copolymer; 1-Hydroxycyclohexyl Phenyl Ketone; Trimethylbenzoyl Diphenylphosphine Oxide; Isobornyl Acrylate",
        "description": "Жесткая база для армирования свободного края, укрепления длины и ремонта ногтей без проседания.",
        "recommended_upsell": "топ без липкого слоя YaSoKo (арт. 22000)",
    },
    "21002": {
        "art": "21002", "model": "D5C",
        "name": "База жидкая 8 в 1 адгезивная YaSoKo, 15 мл HEMA FREE",
        "title": "База для ногтей жидкая 8 в 1 адгезивная YaSoKo, 15 мл HEMA FREE",
        "category": "Базовое покрытие (Adhesive Base)", "volume": "15 мл",
        "formula": "HEMA FREE, супер-сцепка",
        "ozon_sku": None, "wb_sku": None, "barcode": "4638944332251", "gtin": "04638944332251",
        "composition": "Acrylates/VP Copolymer; VP/DMAPA Acrylates Copolymer; Acrylates Copolymer; 1-Hydroxycyclohexyl Phenyl Ketone; Diphenyl(2,4,6-trimethylbenzoyl)phosphine Oxide",
        "description": "Жидкая адгезивная база для гелевых типс, скоростного выравнивания и надежной фиксации декора.",
        "recommended_upsell": "камуфлирующий гель YaSoKo (арт. 1001, 1002)",
    },

    # --- ТОПЫ ДЛЯ НОГТЕЙ (15 мл) ---
    "22000": {
        "art": "22000", "model": "F2806",
        "name": "Топ без липкого слоя бриллиантовый YaSoKo, 15 мл 9-FREE",
        "title": "Топ для ногтей без липкого слоя бриллиантовый YaSoKo, 15 мл 9-FREE",
        "category": "Финишное покрытие (Diamond Top No Wipe)", "volume": "15 мл",
        "formula": "9-FREE, с УФ-защитой от выгорания",
        "ozon_sku": None, "wb_sku": None, "barcode": "4638944332268", "gtin": "04638944332268",
        "composition": "Acrylates/VP Copolymer; Acrylates Copolymer; VP/DMAPA Acrylates Copolymer; 2-Hydroxyethyl Methacrylate; Diphenyl(2,4,6-trimethylbenzoyl)phosphine Oxide; Hydroxycyclohexyl Phenyl Ketone",
        "description": "Глянцевый финиш с оптическим осветлителем (УФ-фильтром). Сохраняет зеркальный блеск до 4 недель, предотвращает пожелтение светлых и пастельных оттенков.",
        "recommended_upsell": "камуфлирующий гель YaSoKo светлых тонов (#01, #28)",
    },
    "22001": {
        "art": "22001", "model": "F130D",
        "name": "Топ без липкого слоя кристально-прозрачный YaSoKo, 15 мл 9-FREE",
        "title": "Топ для ногтей без липкого слоя кристально-прозрачный YaSoKo, 15 мл 9-FREE",
        "category": "Финишное покрытие (Crystal Top No Wipe)", "volume": "15 мл",
        "formula": "9-FREE, без синевы на темных оттенках",
        "ozon_sku": None, "wb_sku": None, "barcode": "4638944332275", "gtin": "04638944332275",
        "composition": "Acrylates/Carbamate Copolymer; Acrylates Copolymer; VP/DMAPA Acrylates Copolymer; Diphenyl(2,4,6-trimethylbenzoyl)phosphine Oxide; 2-Hydroxyethyl Methacrylate",
        "description": "Кристально-прозрачный топ без синего отлива. Идеально передает глубину темных цветов, мерцание шиммера и гель-лаков с эффектом «кошачий глаз».",
        "recommended_upsell": "гель YaSoKo серии «Кошачий глаз» (арт. 1701) или гели с шиммером (арт. 1202, 1204, 1206)",
    },
    "22002": {
        "art": "22002", "model": "Milky Top",
        "name": "Топ молочный без липкого слоя Milky Top YaSoKo, 15 мл HEMA FREE",
        "title": "Топ для ногтей молочный без липкого слоя Milky Top YaSoKo, 15 мл HEMA FREE",
        "category": "Финишное покрытие (Milky Top No Wipe)", "volume": "15 мл",
        "formula": "HEMA FREE, полупрозрачный полуматовый/глянцевый эффект",
        "ozon_sku": None, "wb_sku": None, "barcode": "4638944332282", "gtin": "04638944332282",
        "composition": "Acrylates/Carbamate Copolymer; Acrylates/VP Copolymer; Acrylates Copolymer; CI 77891",
        "description": "Полупрозрачный молочный топ. Мягко сглаживает переход у кутикулы, идеален для создания градиента (бэбибумер) и френч-дизайна.",
        "recommended_upsell": "камуфлирующие гели YaSoKo #01 Ванильный Крем или #02 Зефирная Мечта",
    },
}

# Быстрые обратные индексы внешних идентификаторов маркетплейсов -> артикул продавца (art)
OZON_SKU_TO_ART: Dict[str, str] = {
    str(meta["ozon_sku"]): art_key
    for art_key, meta in PRODUCTS_CATALOG.items()
    if meta.get("ozon_sku")
}

WB_SKU_TO_ART: Dict[str, str] = {
    str(meta["wb_sku"]): art_key
    for art_key, meta in PRODUCTS_CATALOG.items()
    if meta.get("wb_sku")
}

EXTERNAL_ID_TO_ART: Dict[str, str] = {**OZON_SKU_TO_ART, **WB_SKU_TO_ART}


def get_catalog_product(identifier: str) -> Optional[Dict[str, Any]]:
    """Находит карточку товара в каталоге по артикулу продавца, Ozon SKU, WB nmID или штрихкоду."""
    clean = str(identifier or "").strip()
    if not clean:
        return None
    if clean in PRODUCTS_CATALOG:
        return PRODUCTS_CATALOG[clean]
    mapped_art = EXTERNAL_ID_TO_ART.get(clean)
    if mapped_art and mapped_art in PRODUCTS_CATALOG:
        return PRODUCTS_CATALOG[mapped_art]
    for meta in PRODUCTS_CATALOG.values():
        if clean in (meta.get("barcode"), meta.get("gtin")):
            return meta
    return None


def resolve_catalog_external_id(sku: str, marketplace: str) -> str:
    """Возвращает известный nmID WB; Ozon SKU не является product_id."""
    meta = get_catalog_product(sku)
    if not meta:
        return ""
    mp = str(marketplace or "").strip().lower()
    if mp == "wb" and meta.get("wb_sku"):
        return str(meta["wb_sku"])
    return ""
