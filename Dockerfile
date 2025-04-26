FROM python:3.9-slim

WORKDIR /app

# 必要なパッケージをインストール
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# アプリケーションコードをコピー
COPY app.py .
COPY .env .

# Streamlitのポートを公開
EXPOSE 8501

# 起動コマンド
ENTRYPOINT ["streamlit", "run", "app.py", "--server.address=0.0.0.0"]