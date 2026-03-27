FROM python:3.12.8-slim-bookworm
RUN groupadd -r agent && useradd -r -g agent agent
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY watchdog.py .
USER agent
CMD ["python", "-u", "watchdog.py"]
