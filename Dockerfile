FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY runtime runtime
COPY oauth oauth
COPY examples examples
COPY evaluation evaluation
EXPOSE 8000
CMD ["python","-m","runtime.server"]
