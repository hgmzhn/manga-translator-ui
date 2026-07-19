#!/bin/bash
set -e

# Restore default config if directory is empty
if [ -d "/app/default_config" ] && [ -d "/app/config" ]; then
    if [ -z "$(ls -A /app/config)" ]; then
        echo "Initializing empty config directory..."
        cp -r /app/default_config/* /app/config/ || true
    fi
fi

# Restore default fonts if directory is empty
if [ -d "/app/default_fonts" ] && [ -d "/app/fonts" ]; then
    if [ -z "$(ls -A /app/fonts)" ]; then
        echo "Initializing empty fonts directory..."
        cp -r /app/default_fonts/* /app/fonts/ || true
    fi
fi

# Restore default dict if directory is empty
if [ -d "/app/default_dict" ] && [ -d "/app/dict" ]; then
    if [ -z "$(ls -A /app/dict)" ]; then
        echo "Initializing empty dict directory..."
        cp -r /app/default_dict/* /app/dict/ || true
    fi
fi

# Restore default server data if directory is empty
if [ -d "/app/default_server_data" ] && [ -d "/app/manga_translator/server/data" ]; then
    if [ -z "$(ls -A /app/manga_translator/server/data)" ]; then
        echo "Initializing empty server data directory..."
        cp -r /app/default_server_data/* /app/manga_translator/server/data/ || true
    fi
fi

# Execute the main command
exec "$@"
