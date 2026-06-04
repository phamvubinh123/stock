#!/bin/bash
source /Users/binhpham/stock-env/bin/activate
exec uvicorn server:app --port ${PORT:-8000}
