# NM i AI 2026 — Tripletex track (Python 3.12)
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agent.py .

# Adjust CMD to match official submission instructions
CMD ["python", "agent.py"]
