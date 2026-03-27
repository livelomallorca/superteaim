FROM python:3.14.3-slim-bookworm
RUN groupadd -r agent && useradd -r -g agent agent
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY boss.py .
USER agent
CMD ["python", "-u", "boss.py"]
