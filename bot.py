# PostgreSQL Setup для OxideEscort Bot

## ШАГ 1: Создать PostgreSQL базу в Render

1. **Зайди https://dashboard.render.com**
2. Нажми **New +** (в левом углу)
3. Выбери **PostgreSQL**
4. Заполни:
   - **Name:** `oxideescort-db`
   - **Database:** `oxideescort`
   - **User:** `oxideescort`
   - **Region:** выбери same as oxideescort-3
   - **PostgreSQL Version:** 15
5. **Create Database**
6. ⏳ Жди ~2 минуты создание БД

## ШАГ 2: Скопировать Connection String

Когда БД создана:

1. Откроется страница БД
2. Найди "External Database URL"
3. Скопируй ВСЁ (начинается с `postgresql://`)

Пример:
```
postgresql://oxideescort:xxxxx@oregon-postgres.render.com/oxideescort
```

## ШАГ 3: Добавить переменную в oxideescort-3

1. **Зайди на oxideescort-3 → Environment**
2. Нажми **Add Environment Variable**
3. **Name:** `DATABASE_URL`
4. **Value:** вставь скопированный URL
5. **Save**

Render автоматически редеплоит!

## ШАГ 4: Загрузить код на GitHub

1. Открой **bot_with_postgres.py** 
2. Замени содержимое bot.py на GitHub
3. **requirements.txt** замени на **requirements_postgres.txt**
4. **Commit**

Render автоматически перезагружается.

## ШАГ 5: Проверить логи

**Render → oxideescort-3 → Logs**

Должно быть:
```
✅ PostgreSQL connection pool created
✅ Database tables created
✅ Database: PostgreSQL
```

## Готово! 🎉

Теперь все данные сохраняются в PostgreSQL и не теряются при redeploy!

### Тестирование:

1. `/start`
2. 💳 Кошелек
3. 💳 Пополнить
4. Введи 81
5. Оплати счет
6. Проверь платеж

**Все данные останутся даже после redeploy!** ✅
