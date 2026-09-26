import re
from typing import List, Dict, Any, Optional, Tuple


def clean_number(text: str) -> Optional[float]:
    """
    Очищает строку от посторонних символов (символы валют, пробелы, шум OCR)
    и возвращает float.
    """
    if not text:
        return None
    # Оставляем только цифры, точки и запятые
    cleaned = re.sub(r'[^\d.,]', '', text)
    cleaned = cleaned.replace(',', '.')
    
    # Если несколько точек (например, 1.200.50 или артефакт OCR), оставляем последнюю
    if cleaned.count('.') > 1:
        parts = cleaned.split('.')
        cleaned = ''.join(parts[:-1]) + '.' + parts[-1]

    try:
        return float(cleaned)
    except ValueError:
        return None


# Регулярка для строки расчёта:
# Поддерживает:
# - кириллическое 'шт' и смешанную латиницу от OCR (шT, wT, wt, итд)
# - дробные количества (весовые товары: 0.450 кг)
# - формулу с равно и суммой (115.00 * 1шт. = 115.00)
# - варианты без единицы измерения (115.00 * 1 = 115.00)
PRICE_LINE_PATTERN = re.compile(
    r'([\d.,]+)\s*[\*xXхХ×]\s*([\d.,]+)\s*(?:[шwW][тtT]|шт|кг|г|л|уп)?\.?\s*(?:=\s*([\d.,]+))?',
    re.IGNORECASE
)


def preprocess_lines(raw_lines: List[str]) -> List[str]:
    """
    Предобработка строк от OCR:
    1. Убирает пустые строки и краевые пробелы.
    2. Склеивает разорванные OCR строки расчёта (например, '85.00 * 1шт. =' и '85.00').
    """
    cleaned_lines = [line.strip() for line in raw_lines if line and line.strip()]
    merged_lines: List[str] = []
    
    i = 0
    while i < len(cleaned_lines):
        current = cleaned_lines[i]
        
        # Если строка заканчивается на '=' или 'знак умножения/расчет без суммы',
        # а следующая строка — это просто число, объединяем их
        if i + 1 < len(cleaned_lines):
            next_line = cleaned_lines[i + 1]
            if (current.endswith('=') or re.search(r'[\*xXхХ×]\s*[\d.,]+\s*(?:[шwW][тtT]|шт)?\.?$', current)) and \
               re.match(r'^[≡=\s]*[\d.,]+$', next_line):
                if not current.endswith('='):
                    current += ' ='
                merged_lines.append(f"{current} {next_line.lstrip('≡= ')}")
                i += 2
                continue

        merged_lines.append(current)
        i += 1
        
    return merged_lines


def find_store_name(lines: List[str]) -> Optional[str]:
    """Ищет название магазина (в кавычках, либо юрлицо ООО/ИП/ЗАО)."""
    for line in lines[:10]:
        # Поиск по кавычкам (напр. "АВТОКАФЕ" или ООО "Автокафе")
        quote_match = re.search(r'["«»](.+?)["«»]', line)
        if quote_match and len(quote_match.group(1)) > 2:
            return quote_match.group(1).strip()

        # Поиск по ООО / ИП
        legal_match = re.search(r'\b(ООО|ИП|АО|ЗАО)\s+([A-Za-zА-Яа-я0-9\s\-]+)', line, re.IGNORECASE)
        if legal_match:
            return legal_match.group(0).strip()
            
    return None


def find_date_time(lines: List[str]) -> Tuple[Optional[str], Optional[str]]:
    """Ищет дату (ДД.ММ.ГГГГ) и время (ЧЧ:ММ)."""
    date = None
    time = None
    date_pattern = re.compile(r'\b(\d{2}[./-]\d{2}[./-]\d{4})\b')
    time_pattern = re.compile(r'\b(\d{2}:\d{2}(?::\d{2})?)\b')

    for line in lines:
        if not date:
            match_date = date_pattern.search(line)
            if match_date:
                date = match_date.group(1).replace('-', '.').replace('/', '.')
        if not time:
            match_time = time_pattern.search(line)
            if match_time:
                time = match_time.group(1)
        if date and time:
            break

    return date, time


def find_total(lines: List[str]) -> Optional[float]:
    """
    Ищет общую сумму чека (ИТОГ / ИТОГО / К ОПЛАТЕ).
    Проверяет как саму строку с ключевым словом, так и следующую.
    """
    for i, line in enumerate(lines):
        upper = line.upper()
        if 'ИТОГ' in upper or 'ИТОГО' in upper or 'СУММА К ОПЛАТЕ' in upper:
            # Сначала проверяем, есть ли число в этой же строке: "ИТОГ = 785.00"
            match_inline = re.search(r'(?:ИТОГ[А-Я]?|К ОПЛАТЕ)[^\d]*([\d.,]+)', upper)
            if match_inline:
                val = clean_number(match_inline.group(1))
                if val is not None:
                    return val

            # Если число на следующей строке: "ИТОГ" -> "≡785.00"
            if i + 1 < len(lines):
                val = clean_number(lines[i + 1])
                if val is not None:
                    return val
                    
    return None


def find_items(lines: List[str]) -> List[Dict[str, Any]]:
    """Извлекает список позиций (товаров/услуг) с ценами и количеством."""
    items = []
    name_buffer = []

    # Служебные ключевые слова, где список товаров гарантированно заканчивается
    stop_keywords = [
        'ИТОГ', 'ИТОГО', 'БЕЗНАЛИЧНЫМИ', 'НАЛИЧНЫМИ', 'СУММА БЕЗ НДС',
        'НДС', 'СНО:', 'ПРИХОД', 'КАССИР', 'ИНН', 'РН ККТ', 'ФН', 'ФД', 'ФП'
    ]

    # Служебные слова шапки чека, которые не могут быть названиями товаров
    header_keywords = [
        'КАССОВЫЙ ЧЕК', 'ДОБРО ПОЖАЛОВАТЬ', 'МЕСТО РАСЧЕТОВ', 'ПАВИЛЬОН',
        'САВЕЛОВСКОГО', 'ВОКЗАЛА', 'ДОМ', 'Г.МОСКВА', 'Г. МОСКВА', 'УЛ.', 'ПР-Т'
    ]

    def is_stop_line(l: str) -> bool:
        u = l.upper()
        return any(kw in u for kw in stop_keywords)

    def is_header_line(l: str) -> bool:
        u = l.upper()
        # Служебные слова шапки
        if any(kw in u for kw in header_keywords):
            return True
        # Реквизиты, адреса, почтовые индексы (напр. 127015, г.Москва, дом, ул.)
        if re.search(r'\b(?:\d{6}|г\.|ул\.|дом|пл\.|пр-т|пер\.|обл\.|шоссе)\b', u):
            return True
        # Названия магазинов в кавычках
        if '"' in l or '«' in l or '»' in l:
            return True
        # Юрлица (ООО, 000, ИП, ЗАО, АО, ПАО)
        if re.search(r'\b(?:ООО|000|ИП|ЗАО|АО|ПАО)\b', u):
            return True
        return False

    for line in lines:
        if is_stop_line(line):
            # Достигли подвала чека с суммами и реквизитами
            break

        match = PRICE_LINE_PATTERN.search(line)
        if match:
            price_per_unit = clean_number(match.group(1))
            quantity = clean_number(match.group(2)) or 1.0
            total_price = clean_number(match.group(3)) if match.group(3) else None

            # Если общая цена позиции не была указана, вычисляем
            if total_price is None and price_per_unit is not None:
                total_price = round(price_per_unit * quantity, 2)

            name = ' '.join(name_buffer).strip()
            if name:
                items.append({
                    'name': name,
                    'quantity': int(quantity) if quantity.is_integer() else quantity,
                    'price_per_unit': price_per_unit,
                    'total_price': total_price
                })
            name_buffer = []
        else:
            stripped = line.strip()
            if not stripped:
                continue

            # До первого товара весь шум шапки сбрасывает буфер
            if len(items) == 0 and is_header_line(stripped):
                name_buffer = []
                continue

            # Пропускаем служебные строки или чистые числа
            if is_header_line(stripped) or stripped.replace('.', '').isdigit():
                continue

            name_buffer.append(stripped)

    return items


def parse_receipt(raw_lines: List[str]) -> Dict[str, Any]:
    """
    Главная точка входа парсера: принимает список строк OCR и возвращает
    структурированные данные по чеку.
    """
    lines = preprocess_lines(raw_lines)
    date, time = find_date_time(lines)
    items = find_items(lines)
    total = find_total(lines)
    
    # Если итог не распознан, можно посчитать сумму всех позиций как fallback
    if total is None and items:
        total = round(sum(item['total_price'] for item in items if item['total_price'] is not None), 2)

    return {
        'store': find_store_name(lines),
        'date': date,
        'time': time,
        'items': items,
        'total': total,
        'currency': '₸',
        'items_count': len(items)
    }


if __name__ == '__main__':
    import json

    test_lines = [
        '000', '"АBТOKAФE"', 'Кассовый чек', '000 "Автокафе"',
        '127015, г.МоСКВА, пл.', 'Савеловского вокзала, дом 2',
        'МЕСТО РАСЧЕТОВ', 'Павильон',
        'картофель', 'печёный', '115.00 * 1шT. = 115.00',
        'растительное масло', '85.00 * 1шт. = 85.00',
        'лук фри', '85.00 * 1шт. =', '85.00',
        'Закусочный', '120.00 * 1шT. = 120.00',
        'Закусочный', '120.00 * 1шт. = 120.00',
        'кофе американо 0.2', '100.00 * 1шт. = 100.00',
        'MOPc', '160.00 * 1шт. = 160.00',
        'ИТОГ', '≡785.00',
        'BЕ3НАЛИЧНЫМИ', '785.00',
        'CУMMA БЕЗ НДС', '785.00',
        'СНО:УСН доход-расход', 'ПРИХОД',
        'КАССИР Прохорова М.', '888/25',
        '29.07.2025', '16:36',
        'HHH', '7705202538',
        'PH KKT 0000502615061209',
        'ΦH', '728C440500094315',
        'ФД', '89523',
        'ФП', '608422087'
    ]

    result = parse_receipt(test_lines)
    print(json.dumps(result, ensure_ascii=False, indent=2))