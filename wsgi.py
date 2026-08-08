# 배포용 진입점 (gunicorn wsgi:app)
import os, sys
BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE, "web"))
sys.path.insert(0, os.path.join(BASE, "src"))
from app import app  # noqa: E402,F401
