FROM python:3.12-slim

WORKDIR /app

# Install dependencies first (layer-cached unless requirements.txt changes)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source (data/ and credentials/ are mounted as volumes, not baked in)
COPY . .

# Ensure data and credentials dirs exist inside the image as mount points
RUN mkdir -p /app/data /app/credentials

CMD ["python", "bot.py"]
