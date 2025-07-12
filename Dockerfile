FROM python:3.9-slim

WORKDIR /good_repository

COPY . .

RUN pip install --no-cache-dir -r requirements.txt

CMD ["python", "main.py"]