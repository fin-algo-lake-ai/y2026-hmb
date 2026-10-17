"""Пересоздаёт env/data/orders.xlsx детерминированно (фиксированный сид).

Запуск из каталога задачи:  python tools/gen_orders.py
"""
import random
from pathlib import Path

from openpyxl import Workbook

SEED = 20261010
N = 64
REGIONS = ["east", "north", "south", "west"]
PRODUCTS = [("widget-a", 9.9), ("widget-b", 14.5),
            ("gadget-c", 39.0), ("gadget-d", 4.2)]


def main():
    rng = random.Random(SEED)
    wb = Workbook()
    ws = wb.active
    ws.title = "Orders"
    ws.append(["date", "region", "product", "units", "unit_price"])
    for _ in range(N):
        month = rng.choice([7, 8, 9])
        day = rng.randint(1, 28)
        region = rng.choice(REGIONS)
        product, price = rng.choice(PRODUCTS)
        ws.append([f"2026-{month:02d}-{day:02d}", region, product,
                   rng.randint(1, 50), price])
    out = Path(__file__).resolve().parents[1] / "env" / "data" / "orders.xlsx"
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
