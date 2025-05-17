# Use an official Playwright image which includes browsers and dependencies.
# Find the latest stable version tag from:
# https://mcr.microsoft.com/v2/playwright/python/tags/list
# Example: v1.44.0-jammy (for Playwright 1.44.0 on Ubuntu Jammy)
FROM mcr.microsoft.com/playwright/python:v1.52.0-jammy

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file into the container at /app
COPY requirements.txt .

# Install any dependencies specified in requirements.txt
# The Playwright image already has playwright, but this ensures other deps like fastapi/uvicorn are installed.
# --no-cache-dir reduces image size.
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code into the container at /app
COPY . .

# Make port 8000 available to the world outside this container
EXPOSE 8000

# Recommended for Playwright in Docker to avoid issues with shared memory.
# The base image might already set this or similar, but it's good to be explicit.
# Optional: enables Playwright debug logs
ENV DEBUG="pw:api"

# Command to run the application using Uvicorn
# It will listen on all available network interfaces (0.0.0.0) inside the container.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]