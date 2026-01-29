FROM python:3.12-slim

# set workdir
WORKDIR /app

# system deps for some crypto libs
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libssl-dev libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# copy requirements and install
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# copy app
COPY . /app

# drop privileges
RUN useradd --create-home appuser || true
USER appuser

ENV PYTHONUNBUFFERED=1
ENV PORT=3001

EXPOSE 3001

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "3001"]
