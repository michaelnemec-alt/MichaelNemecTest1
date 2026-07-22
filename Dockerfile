# AutoStore analytics Streamlit app — container image for self-hosting
# (e.g. QNAP Container Station on TS-464). Runs on port 8501.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    STREAMLIT_SERVER_PORT=8501 \
    STREAMLIT_SERVER_ADDRESS=0.0.0.0 \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

WORKDIR /app

# Install Python deps first for better layer caching.
COPY requirements.txt ./
RUN pip install -r requirements.txt

# App source.
COPY . .

# Persisted on-disk cache (@st.cache_data persist="disk") lives here.
RUN mkdir -p /app/.streamlit

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8501/_stcore/health', timeout=4).status==200 else 1)"

ENTRYPOINT ["docker-entrypoint.sh"]
