FROM python:3.10

# System dependencies: 'libgl1' use kar rahe hain kyunki purana wala available nahi hai
RUN apt-get update && apt-get install -y \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Port 7860 Hugging Face ke liye
ENV PORT=7860
EXPOSE 7860

CMD ["python", "app.py"]