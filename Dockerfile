FROM python:3.10

# System dependencies for OCR
RUN apt-get update && apt-get install -y \
    libgl1-mesa-glx \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Requirements install karein
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pura code copy karein
COPY . .

# Port set karein
ENV PORT=7860
EXPOSE 7860

# App chalaein
CMD ["python", "app.py"]