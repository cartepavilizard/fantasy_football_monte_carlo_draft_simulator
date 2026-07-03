FROM python:3.12-slim

RUN mkdir -p /backend

WORKDIR /backend

COPY requirements.txt /backend

RUN pip install -r requirements.txt

COPY . /backend

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]