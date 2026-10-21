"""Пересоздаёт env/data/orders.xlsx детерминированно (фиксированный сид).

feedback берётся из конечного словаря шаблонов (тот же лексикон, что в
verifier и solve) — это ключевое требование масштабируемости: число
уникальных текстов ограничено, поэтому дедупликация решает задачу при
любом числе строк (100 или 100000).

Запуск из каталога задачи:  python tools/gen_orders.py [--rows N] [--force]

Без --force перезапись существующего env-файла запрещена (ручной файл
не будет перезаписан случайно).
"""
import argparse
import random
from pathlib import Path

from openpyxl import Workbook

SEED = 20261010
N_DEFAULT = 100
REGIONS = ["east", "north", "south", "west"]
PRODUCTS = [("widget-a", 9.5), ("widget-b", 14.5),
            ("gadget-c", 39.0), ("gadget-d", 4.2)]

PHRASES = {
    "positive": ["All is good", "Great service", "Fast delivery, thanks",
                 "Product works perfectly", "Very satisfied",
                 "Excellent quality", "Will order again"],
    "negative": ["All is bad", "Terrible support", "Item arrived broken",
                 "Too expensive for this quality", "Very disappointed",
                 "Slow shipping", "Would not recommend"],
    "neutral": ["Where is my invoice", "Please confirm delivery date",
                "Do you have this in black", "How to return the item",
                "Tracking number not received", "Is warranty included"],
}

# Распределение классов ~45/35/20 процентов.
WEIGHTS = (0.45, 0.35, 0.20)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=N_DEFAULT)
    ap.add_argument("--force", action="store_true",
                    help="разрешить перезапись существующего env-файла")
    args = ap.parse_args()

    out = (Path(__file__).resolve().parents[1] / "env" / "data"
           / "orders.xlsx")
    if out.exists() and not args.force:
        print(f"REFUSED: {out} уже существует; для перезапуска добавь "
              f"--force (файл ручной, случайная перезапись запрещена)")
        return

    rng = random.Random(SEED)
    wb = Workbook()
    ws = wb.active
    ws.title = "Orders"
    ws.append(["date", "region", "product", "units", "unit_price", "feedback"])
    for _ in range(args.rows):
        month = rng.choice([7, 8, 9])
        day = rng.randint(1, 28)
        region = rng.choice(REGIONS)
        product, price = rng.choice(PRODUCTS)
        i = rng.random()
        if i < WEIGHTS[0]:
            cls = "positive"
        elif i < WEIGHTS[0] + WEIGHTS[1]:
            cls = "negative"
        else:
            cls = "neutral"
        feedback = rng.choice(PHRASES[cls])
        ws.append([f"2026-{month:02d}-{day:02d}", region, product,
                   rng.randint(1, 50), price, feedback])
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    print(f"wrote {out} ({args.rows} rows, seed {SEED})")


if __name__ == "__main__":
    main()
