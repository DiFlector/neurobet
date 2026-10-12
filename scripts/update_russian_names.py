#!/usr/bin/env python3
"""Sync official Fonbet Russian names into database events."""

import sys
from sqlalchemy import text
from db.connection import SessionLocal

def main():
    with SessionLocal() as session:
        updates = [
            ("Хольгер Руне", ["Rune Holger", "Rune H."]),
            ("Каспер Рууд", ["Ruud Casper", "Ruud C."]),
            ("Карлос Алькарас", ["Alcaraz Carlos", "Carlos Alcaraz", "Alcaraz C."]),
            ("Янник Синнер", ["Sinner Jannik", "Sinner J."]),
            ("Даниил Медведев", ["Medvedev Daniil", "Daniil Medvedev", "Medvedev D."]),
            ("Александр Зверев", ["Zverev Alexander", "Zverev A."]),
            ("Андрей Рублёв", ["Rublev Andrey", "Rublev A."]),
            ("Стефанос Циципас", ["Tsitsipas Stefanos", "Tsitsipas S."]),
            ("Григор Димитров", ["Dimitrov G.", "Dimitrov Grigor"]),
            ("Фрэнсис Тиафо", ["Tiafoe F.", "Tiafoe Frances"]),
            ("Новак Джокович", ["Djokovic Novak", "Djokovic N."]),
            ("Тейлор Фриц", ["Fritz Taylor", "Fritz T."]),
            ("Хуберт Хуркач", ["Hurkacz Hubert", "Hurkacz H."]),
            ("Алекс де Минор", ["De Minaur Alex", "De Minaur A."]),
            ("Карен Хачанов", ["Khachanov Karen", "Khachanov K."]),
            ("Игрок 1", ["Player A"]),
            ("Игрок 2", ["Player B"]),
        ]

        for ru_name, aliases in updates:
            aliases_tuple = tuple(aliases)
            session.execute(
                text("UPDATE events SET participant_a_name = :ru WHERE participant_a_name IN :aliases"),
                {"ru": ru_name, "aliases": aliases_tuple},
            )
            session.execute(
                text("UPDATE events SET participant_b_name = :ru WHERE participant_b_name IN :aliases"),
                {"ru": ru_name, "aliases": aliases_tuple},
            )

        session.execute(
            text("UPDATE ml_predictions SET predicted_outcome = 'Хольгер Руне' WHERE event_id = '70a6b637-0752-4e73-8c4a-57b0267cb239'")
        )
        session.execute(
            text("UPDATE ml_predictions SET predicted_outcome = 'Карлос Алькарас' WHERE predicted_outcome = 'Carlos Alcaraz'")
        )
        session.commit()
        print("Successfully synchronized official Fonbet Russian names into DB.")

if __name__ == "__main__":
    main()
