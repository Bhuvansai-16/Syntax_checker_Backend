# Use Python 3.9 slim for Gramformer/spaCy 2.x compatibility
FROM python:3.9-slim

# Install system dependencies for PyMuPDF, git (for pip git installs), and other build tools
RUN apt-get update && apt-get install -y \
    build-essential \
    python3-dev \
    libmupdf-dev \
    git \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Download spaCy model (en_core_web_sm for v2.3.9)
RUN python -m spacy download en_core_web_sm

# Copy the application code
COPY . .

# Expose port 8000 (default for FastAPI/Uvicorn)
EXPOSE 8000

# Run the application with Uvicorn
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
