import os

# Токены берутся только из environment variables на Render
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
CRYPTO_BOT_TOKEN = os.environ.get('CRYPTO_BOT_TOKEN')
DATABASE_URL = os.environ.get('DATABASE_URL')

# Логирование
print(f"✅ Config loaded from environment variables")
