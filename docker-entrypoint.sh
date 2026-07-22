#!/bin/sh
# Generate .streamlit/secrets.toml from environment variables so the API token
# is never baked into the image or committed to git. If CUBEANALYTICS_TOKEN is
# not set, fall back to a secrets.toml provided via bind mount.
set -e

SECRETS="/app/.streamlit/secrets.toml"

if [ -n "$CUBEANALYTICS_TOKEN" ]; then
    mkdir -p /app/.streamlit
    {
        echo "[cubeanalytics]"
        echo "token = \"$CUBEANALYTICS_TOKEN\""
        if [ -n "$SNOWFLAKE_ACCOUNT" ]; then
            echo ""
            echo "[snowflake]"
            echo "account = \"$SNOWFLAKE_ACCOUNT\""
            echo "user = \"$SNOWFLAKE_USER\""
            echo "password = \"$SNOWFLAKE_PASSWORD\""
            echo "warehouse = \"${SNOWFLAKE_WAREHOUSE:-QUERY_RUNNER_XSMALL}\""
            echo "database = \"${SNOWFLAKE_DATABASE:-DWH_PROD_TRANSFORM}\""
            echo "schema = \"${SNOWFLAKE_SCHEMA:-TR_LEVEL_0_ARCOS}\""
            echo "role = \"${SNOWFLAKE_ROLE:-DWH_PROD_TRANSFORM_READER}\""
        fi
    } > "$SECRETS"
elif [ ! -f "$SECRETS" ]; then
    echo "ERROR: no CubeAnalytics token found." >&2
    echo "Set CUBEANALYTICS_TOKEN env var, or bind-mount a secrets.toml to $SECRETS" >&2
    exit 1
fi

exec streamlit run app.py
